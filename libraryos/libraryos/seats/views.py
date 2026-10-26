import json
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import services
from .forms import SignUpForm
from .models import AdminActionLog, Profile, Reservation
from .services import BookingError

POLL_INTERVAL_MS = 15000
SEAT_PLACEHOLDER = "__SEAT__"

DASHBOARD_URLS = {
    "admin": "seats:admin-dashboard",
    "professor": "seats:professor-dashboard",
    "student": "seats:student-dashboard",
}


def _profile(user):
    """The approved profile of a signed-in user, or None."""
    if not user.is_authenticated:
        return None
    profile = Profile.objects.filter(user=user).first()
    if profile is None and user.is_superuser:  # e.g. created with `createsuperuser`
        profile = Profile.objects.create(
            user=user,
            role=Profile.Role.ADMIN,
            status=Profile.Status.APPROVED,
            first_name=user.first_name or user.username,
            last_name=user.last_name,
        )
    if profile is None or profile.status != Profile.Status.APPROVED:
        return None
    return profile


def _require_role(role, *allowed):
    if role not in allowed:
        raise BookingError("Your current role is not allowed to do that.", status=403, code="forbidden")


def _json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except ValueError:
        raise BookingError("Request body must be valid JSON.", code="invalid_json")
    if not isinstance(data, dict):
        raise BookingError("Request body must be a JSON object.", code="invalid_json")
    return data


def api_post(view):
    """
    POST-only, sign-in-required JSON endpoint. The wrapped view receives
    (request, role, user, ...) and returns (message, level). On success the
    response carries the fresh state so the page can re-render without a second request.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        profile = _profile(request.user)
        if profile is None:
            return JsonResponse({"ok": False, "error": "Please sign in.", "code": "unauthenticated"}, status=401)
        role, user = profile.role, request.user
        try:
            message, level = view(request, role, user, *args, **kwargs)
        except BookingError as exc:
            return JsonResponse(
                {"ok": False, "error": exc.message, "code": exc.code, **exc.extra}, status=exc.status
            )
        return JsonResponse(
            {"ok": True, "message": message, "level": level, "state": services.build_state(role, user)}
        )

    return require_POST(wrapper)


# --------------------------------------------------------------------- auth
def _home_for(profile):
    return redirect(DASHBOARD_URLS[profile.role])


def home(request):
    profile = _profile(request.user)
    return _home_for(profile) if profile else redirect("seats:login")


def login_view(request):
    profile = _profile(request.user)
    if profile:
        return _home_for(profile)

    error = None
    username = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        account = User.objects.filter(username__iexact=username).first()
        if account and not account.is_active and account.check_password(password):
            status = getattr(getattr(account, "profile", None), "status", None)
            if status == Profile.Status.REJECTED:
                error = "Your registration was not approved. Please contact the library administrator."
            else:
                error = "Your account is still waiting for Admin approval. You can sign in once it is approved."
        else:
            user = authenticate(request, username=account.username if account else username, password=password)
            if user is None:
                error = "Invalid username or password."
            elif _profile(user) is None:
                error = "This account has no approved library profile."
            else:
                login(request, user)
                return _home_for(user.profile)
    return render(request, "seats/auth/login.html", {"error": error, "username": username})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("seats:login")


def signup_view(request):
    profile = _profile(request.user)
    if profile:
        return _home_for(profile)
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request, "Registration submitted! An Admin must approve your account before you can sign in."
            )
            return redirect("seats:login")
    else:
        role = request.GET.get("role")
        form = SignUpForm(initial={"role": role if role in ("student", "professor") else "student"})
    return render(request, "seats/auth/signup.html", {"form": form})


def _admin_only(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        profile = _profile(request.user)
        if profile is None:
            return redirect("seats:login")
        if profile.role != Profile.Role.ADMIN:
            return _home_for(profile)
        return view(request, *args, **kwargs)

    return wrapper


@_admin_only
def approvals(request):
    if request.method == "POST":
        target = get_object_or_404(Profile.objects.select_related("user"), pk=request.POST.get("profile_id"))
        action = request.POST.get("action")
        if target.role != Profile.Role.ADMIN and action in ("approve", "reject"):
            approved = action == "approve"
            was_approved = target.status == Profile.Status.APPROVED
            target.status = Profile.Status.APPROVED if approved else Profile.Status.REJECTED
            target.save(update_fields=["status"])
            target.user.is_active = approved
            target.user.save(update_fields=["is_active"])
            if approved:
                log_action = AdminActionLog.Action.APPROVED
            elif was_approved:
                log_action = AdminActionLog.Action.REVOKED
            else:
                log_action = AdminActionLog.Action.REJECTED
            services.log_admin_action(
                request.user,
                log_action,
                f"{target.full_name} ({target.user.username})",
                f"{target.get_role_display()} account",
                target_user=target.user,
            )
            messages.success(request, f"{target.full_name} was {log_action.label.lower()}.")
        return redirect("seats:approvals")

    accounts = Profile.objects.exclude(role=Profile.Role.ADMIN).select_related("user")
    context = {
        "pending": accounts.filter(status=Profile.Status.PENDING).order_by("created_at"),
        "reviewed": accounts.exclude(status=Profile.Status.PENDING)[:25],
        "current_user": services.identity(request.user),
    }
    return render(request, "seats/auth/approvals.html", context)


@_admin_only
def users_list(request):
    role = request.GET.get("role")
    if role not in (Profile.Role.STUDENT, Profile.Role.PROFESSOR):
        role = "all"
    profiles = Profile.objects.exclude(role=Profile.Role.ADMIN).select_related("user").order_by("last_name", "first_name")
    if role != "all":
        profiles = profiles.filter(role=role)
    context = {
        "profiles": profiles,
        "role_filter": role,
        "current_user": services.identity(request.user),
        "nav": "users",
    }
    return render(request, "seats/auth/users.html", context)


@_admin_only
def activity_logs(request):
    tab = "seats" if request.GET.get("tab") == "seats" else "admin"
    context = {"tab": tab, "current_user": services.identity(request.user), "nav": "logs"}
    if tab == "seats":
        context["reservations"] = Reservation.objects.select_related("seat__zone").order_by("-created_at")[:300]
    else:
        context["actions"] = AdminActionLog.objects.all()[:300]
    return render(request, "seats/auth/activity_logs.html", context)


# -------------------------------------------------------------------- pages
@ensure_csrf_cookie
def dashboard(request, page_role):
    """Each role has its own URL. Signed-in users are always sent to their own dashboard."""
    profile = _profile(request.user)
    if profile is None:
        return redirect("seats:login")
    if profile.role != page_role:
        return _home_for(profile)

    role, user = profile.role, request.user
    state = services.build_state(role, user)
    config = {
        "pollMs": POLL_INTERVAL_MS,
        "urls": {
            "state": reverse("seats:state"),
            "batchReserve": reverse("seats:batch-reserve"),
            "reserve": reverse("seats:reserve", args=[SEAT_PLACEHOLDER]),
            "release": reverse("seats:release", args=[SEAT_PLACEHOLDER]),
            "maintenance": reverse("seats:maintenance", args=[SEAT_PLACEHOLDER]),
            "checkIn": reverse("seats:check-in", args=[SEAT_PLACEHOLDER]),
        },
        "seatPlaceholder": SEAT_PLACEHOLDER,
    }
    context = {
        "role": role,
        "current_user": services.identity(user),
        "zones": state["zones"],
        "kpis": state["summary"],
        "state": state,
        "config": config,
        "pending_count": Profile.objects.filter(status=Profile.Status.PENDING).count() if role == "admin" else 0,
    }
    return render(request, "seats/index.html", context)


# ---------------------------------------------------------------------- API
@never_cache
@require_GET
def api_state(request):
    profile = _profile(request.user)
    if profile is None:
        return JsonResponse({"ok": False, "error": "Please sign in.", "code": "unauthenticated"}, status=401)
    return JsonResponse({"ok": True, "state": services.build_state(profile.role, request.user)})


@api_post
def reserve_seat(request, role, user, seat_id):
    _require_role(role, "student")
    data = _json_body(request)
    return services.reserve_seat_for_student(user, seat_id, data.get("duration"), data.get("start_hour"))


@api_post
def batch_reserve(request, role, user):
    _require_role(role, "professor", "admin")
    data = _json_body(request)
    return services.batch_reserve_for_professor(
        user, data.get("seat_ids"), data.get("title"), data.get("duration"), data.get("start_hour")
    )


@api_post
def release_seat(request, role, user, seat_id):
    return services.release_seat(role, user, seat_id)


@api_post
def toggle_maintenance(request, role, user, seat_id):
    _require_role(role, "admin")
    return services.toggle_maintenance(user, seat_id)


@api_post
def check_in(request, role, user, seat_id):
    _require_role(role, "student")
    return services.check_in(user, seat_id)
