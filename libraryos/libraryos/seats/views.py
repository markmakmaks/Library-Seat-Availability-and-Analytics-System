import json
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import services
from .demo_users import DEFAULT_ROLE, DEMO_USERS
from .services import BookingError

POLL_INTERVAL_MS = 15000
SEAT_PLACEHOLDER = "__SEAT__"


def _current_role(request):
    role = request.session.get("role", DEFAULT_ROLE)
    return role if role in DEMO_USERS else DEFAULT_ROLE


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
    POST-only JSON endpoint. The wrapped view receives (request, role, ...) and
    returns (message, level). On success the response carries the fresh state so
    the page can re-render without a second request.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            message, level = view(request, _current_role(request), *args, **kwargs)
        except BookingError as exc:
            return JsonResponse(
                {"ok": False, "error": exc.message, "code": exc.code, **exc.extra}, status=exc.status
            )
        return JsonResponse(
            {
                "ok": True,
                "message": message,
                "level": level,
                "state": services.build_state(_current_role(request)),
            }
        )

    return require_POST(wrapper)


# -------------------------------------------------------------------- pages
@ensure_csrf_cookie
def index(request):
    role = _current_role(request)
    state = services.build_state(role)
    config = {
        "pollMs": POLL_INTERVAL_MS,
        "urls": {
            "state": reverse("seats:state"),
            "role": reverse("seats:set-role"),
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
        "current_user": DEMO_USERS[role],
        "zones": state["zones"],
        "kpis": state["summary"],
        "state": state,
        "config": config,
    }
    return render(request, "seats/index.html", context)


# ---------------------------------------------------------------------- API
@never_cache
@require_GET
def api_state(request):
    return JsonResponse({"ok": True, "state": services.build_state(_current_role(request))})


@api_post
def set_role(request, role):
    new_role = _json_body(request).get("role")
    if new_role not in DEMO_USERS:
        raise BookingError("Unknown role.", code="invalid_role")
    request.session["role"] = new_role
    return "", "info"


@api_post
def reserve_seat(request, role, seat_id):
    _require_role(role, "student")
    data = _json_body(request)
    return services.reserve_seat_for_student(seat_id, data.get("duration"), data.get("start_hour"))


@api_post
def batch_reserve(request, role):
    _require_role(role, "professor")
    data = _json_body(request)
    return services.batch_reserve_for_professor(
        data.get("seat_ids"), data.get("title"), data.get("duration"), data.get("start_hour")
    )


@api_post
def release_seat(request, role, seat_id):
    return services.release_seat(role, seat_id)


@api_post
def toggle_maintenance(request, role, seat_id):
    _require_role(role, "admin")
    return services.toggle_maintenance(seat_id)


@api_post
def check_in(request, role, seat_id):
    _require_role(role, "student")
    return services.check_in(seat_id)
