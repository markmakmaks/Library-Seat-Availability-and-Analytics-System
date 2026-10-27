"""
Business rules for LibraryOS, kept out of the views so they are easy to test.

Every mutating function runs inside a transaction and raises BookingError when a
rule is broken. The views turn that into a JSON error response.

Reservation life cycle
    request  -> PENDING   (does not hold the seat; the Admins are notified)
    accept   -> RESERVED  (seat is held, 15-minute grace period starts, user gets a confirmation code)
    verify   -> ACTIVE    (Admin checked the code; seat is occupied)
    otherwise-> EXPIRED (grace period ran out), REJECTED (Admin declined) or RELEASED (cancelled / finished)
"""
import re
import secrets
from datetime import datetime, timedelta
from datetime import time as dtime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.db.models.functions import ExtractHour
from django.utils import timezone

from .models import OPEN_STATUSES, AdminActionLog, Notification, Reservation, Seat, Zone

STUDENT_DURATIONS = (1, 2, 3)
PROFESSOR_DURATIONS = (2, 4, 6)
MAX_TITLE_LENGTH = 100
DEFAULT_PROF_TITLE = "Faculty Group Consultation"
GRACE_PERIOD = timedelta(minutes=15)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I to avoid misreading
NOTIFICATIONS_SHOWN = 15

Status = Reservation.Status
Role = Reservation.Role
EndReason = Reservation.EndReason
Action = AdminActionLog.Action
User = get_user_model()


class BookingError(Exception):
    def __init__(self, message, *, status=400, code="error", extra=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.extra = extra or {}


# ----------------------------------------------------------------- helpers
def _lock_seat(seat_id):
    try:
        return Seat.objects.select_for_update().select_related("zone").get(seat_id=seat_id)
    except Seat.DoesNotExist:
        raise BookingError(f"Seat {seat_id} does not exist.", status=404, code="not_found")


def _lock_reservation(reservation_id):
    try:
        pk = int(reservation_id)
    except (TypeError, ValueError):
        raise BookingError("Reservation not found.", status=404, code="not_found")
    reservation = Reservation.objects.select_for_update(of=("self",)).select_related("seat__zone", "user").filter(pk=pk).first()
    if reservation is None:
        raise BookingError("Reservation not found.", status=404, code="not_found")
    return reservation


def _parse_choice(value, allowed, label):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise BookingError(f"Invalid {label}.", code="invalid_input")
    if number not in allowed:
        raise BookingError(f"Invalid {label}.", code="invalid_input")
    return number


def _clean_start_hour(value):
    """Hour of day (0-23) the session starts. The browser sends its local hour."""
    try:
        hour = int(value)
        if 0 <= hour <= 23:
            return hour
    except (TypeError, ValueError):
        pass
    return timezone.localtime().hour


def _ensure_available(seat, *, ignore_pending=False):
    if seat.is_maintenance:
        raise BookingError(f"Seat {seat.seat_id} is under maintenance.", status=409, code="unavailable")
    if seat.current_reservation is not None:
        raise BookingError(f"Seat {seat.seat_id} is no longer available.", status=409, code="unavailable")
    if not ignore_pending and seat.pending_request is not None:
        raise BookingError(
            f"Seat {seat.seat_id} already has a request waiting for approval.", status=409, code="unavailable"
        )


def _create_reservation(**fields):
    try:
        with transaction.atomic():  # savepoint, so an IntegrityError doesn't poison the outer transaction
            return Reservation.objects.create(**fields)
    except IntegrityError:
        raise BookingError("That seat was just taken by someone else.", status=409, code="seat_taken")


def _new_code():
    for _ in range(20):
        code = "LIB-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not Reservation.objects.filter(confirmation_code=code).exists():
            return code
    raise BookingError("Could not generate a confirmation code. Please try again.", status=500, code="error")


def log_admin_action(admin, action, target_name, details="", target_user=None):
    AdminActionLog.objects.create(
        admin=admin,
        admin_username=admin.username if admin else "system",
        action=action,
        target_user=target_user,
        target_name=target_name,
        details=details,
    )


def _close(reservation, reason):
    reservation.status = Status.RELEASED
    reservation.released_at = timezone.now()
    reservation.end_reason = reason
    reservation.save(update_fields=["status", "released_at", "end_reason"])


def _user_end_reason(reservation):
    return EndReason.COMPLETED if reservation.checked_in_at else EndReason.CANCELLED


# ----------------------------------------------------------- notifications
def notify(user, message, kind="info", reservation=None):
    if user is not None:
        Notification.objects.create(user=user, message=message, kind=kind, reservation=reservation)


def notify_admins(message, kind="info", reservation=None):
    admins = User.objects.filter(is_active=True, profile__role="admin", profile__status="approved")
    Notification.objects.bulk_create(
        [Notification(user=a, message=message, kind=kind, reservation=reservation) for a in admins]
    )


def mark_notifications_read(user, ids=None):
    queryset = user.notifications.filter(read_at__isnull=True)
    if ids:
        try:
            queryset = queryset.filter(pk__in=[int(i) for i in ids])
        except (TypeError, ValueError):
            raise BookingError("Invalid notification id.", code="invalid_input")
    queryset.update(read_at=timezone.now())


# ----------------------------------------------------- requests (student/prof)
@transaction.atomic
def reserve_seat_for_student(user, seat_id, duration, start_hour=None):
    duration = _parse_choice(duration, STUDENT_DURATIONS, "duration")
    seat = _lock_seat(seat_id)

    existing = (
        Reservation.objects.select_related("seat__zone")
        .filter(role=Role.STUDENT, user=user, status__in=[Status.PENDING, *OPEN_STATUSES])
        .first()
    )
    if existing:
        raise BookingError(
            "Students may hold only one pending, active or upcoming reservation at a time.",
            status=409,
            code="single_seat_rule",
            extra={
                "current": {
                    "seat": existing.seat.seat_id,
                    "zoneName": existing.seat.zone.name,
                    "time": existing.time_display,
                }
            },
        )

    _ensure_available(seat)
    start = _clean_start_hour(start_hour)
    reservation = _create_reservation(
        seat=seat,
        user=user,
        user_name=user.profile.full_name,
        role=Role.STUDENT,
        start_hour=start,
        end_hour=start + duration,
        status=Status.PENDING,
        confirmation_code=_new_code(),
    )
    notify_admins(
        f"New seat reservation request from {reservation.user_name} for Seat {seat.seat_id} awaiting approval.",
        reservation=reservation,
    )
    return f"Request sent for Seat {seat.seat_id}. Waiting for Admin approval.", "success"


@transaction.atomic
def batch_reserve_for_professor(user, seat_ids, title, duration, start_hour=None):
    if not isinstance(seat_ids, list) or not seat_ids:
        raise BookingError("Please select at least one available seat first.", code="invalid_input")
    seat_ids = list(dict.fromkeys(str(s) for s in seat_ids))
    duration = _parse_choice(duration, PROFESSOR_DURATIONS, "duration")
    title = str(title or "").strip()[:MAX_TITLE_LENGTH] or DEFAULT_PROF_TITLE

    seats = list(
        Seat.objects.select_for_update().select_related("zone").filter(seat_id__in=seat_ids).order_by("pk")
    )
    missing = set(seat_ids) - {s.seat_id for s in seats}
    if missing:
        raise BookingError(f"Unknown seat(s): {', '.join(sorted(missing))}.", status=404, code="not_found")

    start = _clean_start_hour(start_hour)
    is_admin = user.profile.role == "admin"
    now = timezone.now()
    created = []
    for seat in seats:  # all-or-nothing: any failure rolls the whole batch back
        _ensure_available(seat)
        fields = dict(
            seat=seat, user=user, user_name=user.profile.full_name, title=title,
            start_hour=start, end_hour=start + duration,
        )
        if is_admin:  # an Admin does not need anybody's approval: the seats are theirs immediately
            fields.update(role=Role.ADMIN, status=Status.ACTIVE, checked_in_at=now)
        else:
            fields.update(role=Role.PROFESSOR, status=Status.PENDING, confirmation_code=_new_code())
        created.append(_create_reservation(**fields))

    seat_list = ", ".join(s.seat_id for s in seats)
    if is_admin:
        log_admin_action(user, Action.SEATS_RESERVED, seat_list, f"Session: {title}")
        return f"Successfully reserved {len(seats)} seats!", "success"
    notify_admins(
        f"New seat reservation request from {user.profile.full_name} for Seat{'s' if len(seats) > 1 else ''} "
        f"{seat_list} awaiting approval.",
        reservation=created[0],
    )
    return f"Request sent for {len(seats)} seat(s). Waiting for Admin approval.", "success"


@transaction.atomic
def cancel_reservation(user, reservation_id):
    """A user withdraws a pending request, cancels a reservation, or ends their own session."""
    reservation = _lock_reservation(reservation_id)
    if reservation.user_id != user.id:
        raise BookingError("You can only cancel your own reservations.", status=403, code="forbidden")
    if reservation.status not in (Status.PENDING, *OPEN_STATUSES):
        raise BookingError("This reservation is already over.", status=409, code="already_closed")
    pending = reservation.status == Status.PENDING
    _close(reservation, _user_end_reason(reservation))
    seat_id = reservation.seat.seat_id
    if pending:
        notify_admins(f"{reservation.user_name} withdrew the request for Seat {seat_id}.", reservation=reservation)
        return f"Your request for Seat {seat_id} was cancelled.", "info"
    return f"Seat {seat_id} has been released and is now available.", "info"


# ------------------------------------------------------------- admin actions
@transaction.atomic
def accept_reservation(admin, reservation_id):
    """Pending -> Reserved. The 15-minute grace period starts now."""
    reservation = _lock_reservation(reservation_id)
    if reservation.status != Status.PENDING:
        raise BookingError("That request is no longer pending.", status=409, code="not_pending")
    seat = _lock_seat(reservation.seat.seat_id)
    _ensure_available(seat, ignore_pending=True)

    now = timezone.now()
    reservation.status = Status.RESERVED
    reservation.accepted_at = now
    reservation.grace_period_expires_at = now + GRACE_PERIOD
    reservation.save(update_fields=["status", "accepted_at", "grace_period_expires_at"])

    # any other request for the same seat can no longer be satisfied
    for other in seat.reservations.select_for_update().filter(status=Status.PENDING):
        _reject(other, f"Seat {seat.seat_id} was reserved by someone else.", EndReason.REJECTED)

    notify(
        reservation.user,
        f"Your reservation for Seat {seat.seat_id} has been accepted! Your confirmation code is "
        f"{reservation.confirmation_code}. Please check in within 15 minutes.",
        "success",
        reservation,
    )
    log_admin_action(
        admin, Action.RESERVATION_ACCEPTED, f"{reservation.user_name} - Seat {seat.seat_id}",
        f"Code {reservation.confirmation_code}; check in before {timezone.localtime(reservation.grace_period_expires_at):%H:%M}",
        target_user=reservation.user,
    )
    return f"Accepted: Seat {seat.seat_id} is reserved for {reservation.user_name} (15 minutes to check in).", "success"


def _reject(reservation, message, reason):
    reservation.status = Status.REJECTED
    reservation.released_at = timezone.now()
    reservation.end_reason = reason
    reservation.save(update_fields=["status", "released_at", "end_reason"])
    notify(reservation.user, message, "warning", reservation)


@transaction.atomic
def reject_reservation(admin, reservation_id):
    reservation = _lock_reservation(reservation_id)
    if reservation.status != Status.PENDING:
        raise BookingError("That request is no longer pending.", status=409, code="not_pending")
    seat_id = reservation.seat.seat_id
    _reject(reservation, f"Your reservation request for Seat {seat_id} was rejected by the Admin.", EndReason.REJECTED)
    log_admin_action(
        admin, Action.RESERVATION_REJECTED, f"{reservation.user_name} - Seat {seat_id}", target_user=reservation.user
    )
    return f"Request for Seat {seat_id} was rejected.", "info"


def verify_confirmation_code(admin, code):
    """Time-in: an Admin checks the code the user presents. Valid only inside the grace period."""
    expire_overdue_reservations()  # so a late code is reported as expired, not accepted
    return _verify_confirmation_code(admin, code)


@transaction.atomic
def _verify_confirmation_code(admin, code):
    code = re.sub(r"\s", "", str(code or "")).upper()
    if re.fullmatch(r"[A-Z0-9]{6}", code):
        code = f"LIB-{code}"
    if not code:
        raise BookingError("Enter the confirmation code.", code="invalid_input")

    reservation = (
        Reservation.objects.select_for_update().select_related("seat").filter(confirmation_code=code).first()
    )
    if reservation is None:
        raise BookingError(f"No reservation matches code {code}.", status=404, code="not_found")
    seat_id = reservation.seat.seat_id
    if reservation.status == Status.PENDING:
        raise BookingError(f"{code} has not been accepted yet. Accept the request first.", status=409, code="not_accepted")
    if reservation.status == Status.ACTIVE:
        raise BookingError(f"{code} was already verified.", status=409, code="already_checked_in")
    if reservation.status == Status.EXPIRED:
        raise BookingError(f"{code} expired: the 15-minute grace period has passed.", status=409, code="expired")
    if reservation.status != Status.RESERVED:
        raise BookingError(f"{code} is no longer valid.", status=409, code="not_valid")

    now = timezone.now()
    reservation.status = Status.ACTIVE
    reservation.checked_in_at = now
    reservation.save(update_fields=["status", "checked_in_at"])
    notify(reservation.user, f"Time-in confirmed for Seat {seat_id}. Enjoy your session!", "success", reservation)
    log_admin_action(
        admin, Action.TIME_IN_VERIFIED, f"{reservation.user_name} - Seat {seat_id}", f"Code {code}",
        target_user=reservation.user,
    )
    return f"Time-in confirmed: {reservation.user_name} is now seated at {seat_id}.", "success"


def expire_overdue_reservations():
    """
    Mark accepted reservations whose 15-minute grace period ran out as No-Show; the seat becomes
    available again. There is no scheduler, so this runs whenever state is built (every poll).
    """
    now = timezone.now()
    overdue = list(
        Reservation.objects.filter(status=Status.RESERVED, grace_period_expires_at__lt=now).values_list("pk", flat=True)
    )
    for pk in overdue:
        with transaction.atomic():
            reservation = (
                Reservation.objects.select_for_update(of=("self",))
                .select_related("seat", "user")
                .filter(pk=pk, status=Status.RESERVED)
                .first()
            )
            if reservation is None:
                continue
            reservation.status = Status.EXPIRED
            reservation.released_at = now
            reservation.end_reason = EndReason.NO_SHOW
            reservation.save(update_fields=["status", "released_at", "end_reason"])
            seat_id = reservation.seat.seat_id
            notify(
                reservation.user,
                f"Your reservation for Seat {seat_id} expired: you did not check in within 15 minutes.",
                "warning",
                reservation,
            )
            notify_admins(
                f"No-show: {reservation.user_name}'s reservation for Seat {seat_id} expired. The seat is available again.",
                "warning",
                reservation,
            )
            log_admin_action(
                None, Action.NO_SHOW_EXPIRED, f"{reservation.user_name} - Seat {seat_id}",
                f"Code {reservation.confirmation_code}; grace period ended "
                f"{timezone.localtime(reservation.grace_period_expires_at):%H:%M}",
                target_user=reservation.user,
            )
    return len(overdue)


@transaction.atomic
def release_seat(role, user, seat_id):
    seat = _lock_seat(seat_id)
    reservation = seat.current_reservation

    if role == "admin":
        pending = seat.pending_request
        if reservation is None and pending is not None and not seat.is_maintenance:
            return reject_reservation(user, pending.pk)  # "force release" of a seat that only has a request
        if reservation is None and not seat.is_maintenance:
            raise BookingError(f"Seat {seat.seat_id} is already available.", status=409, code="already_available")
        if seat.is_maintenance:
            seat.is_maintenance = False
            seat.save(update_fields=["is_maintenance"])
    else:
        if reservation is None or reservation.user_id != user.id or reservation.role != role:
            raise BookingError("You can only release your own reservations.", status=403, code="forbidden")

    if reservation is not None:
        reason = EndReason.FORCE_RELEASED if role == "admin" else _user_end_reason(reservation)
        _close(reservation, reason)
        if role == "admin":
            notify(
                reservation.user,
                f"Your reservation for Seat {seat.seat_id} was released by the Admin.",
                "warning",
                reservation,
            )
    if role == "admin":
        details = f"Reservation held by {reservation.user_name}" if reservation else "Maintenance block lifted"
        log_admin_action(user, Action.SEAT_RELEASED, seat.seat_id, details)
    return f"Seat {seat.seat_id} has been released and is now available.", "info"


@transaction.atomic
def toggle_maintenance(user, seat_id):
    seat = _lock_seat(seat_id)
    if seat.is_maintenance:
        log_admin_action(user, Action.MAINTENANCE_OFF, seat.seat_id)
        seat.is_maintenance = False
        message = f"Seat {seat.seat_id} is back in service."
    else:
        reservation = seat.current_reservation
        if reservation is not None:
            _close(reservation, EndReason.BLOCKED)
            notify(
                reservation.user,
                f"Your reservation for Seat {seat.seat_id} was cancelled: the seat is blocked for maintenance.",
                "warning",
                reservation,
            )
        for pending in seat.reservations.select_for_update().filter(status=Status.PENDING):
            _reject(pending, f"Your request for Seat {seat.seat_id} was rejected: the seat is blocked.", EndReason.BLOCKED)
        details = f"Cancelled reservation of {reservation.user_name}" if reservation else ""
        log_admin_action(user, Action.MAINTENANCE_ON, seat.seat_id, details)
        seat.is_maintenance = True
        message = f"Seat {seat.seat_id} has been blocked for maintenance."
    seat.save(update_fields=["is_maintenance"])
    return message, "info"


# ------------------------------------------------------------------ camera sync
BELONGINGS_THRESHOLD = timedelta(minutes=10)
WALKIN_NAME = "Walk-in (camera)"


def _update_belongings(seat, now, person, objects):
    """Track items left with nobody present; flag the seat (once) after the threshold."""
    if person or not objects:
        seat.belongings_since, seat.belongings_flagged = None, False
        return
    if seat.belongings_since is None:
        seat.belongings_since = now
    if not seat.belongings_flagged and now - seat.belongings_since >= BELONGINGS_THRESHOLD:
        seat.belongings_flagged = True
        AdminActionLog.objects.create(
            admin=None,
            admin_username="camera",
            action=Action.BELONGINGS_FLAGGED,
            target_name=seat.seat_id,
            details=f"Items left unattended since {timezone.localtime(seat.belongings_since):%H:%M}",
        )


@transaction.atomic
def sync_seat_from_camera(seat_id, is_person_detected, is_object_detected):
    """
    Apply a *debounced* camera reading to a seat without breaking reservations.

    - Available + person            -> walk-in session (active reservation, no user)
    - Walk-in session + nobody      -> released
    - Reserved / checked-in booking -> left alone: time-in is confirmed by an Admin verifying the user's
                                       code, and the 15-minute grace period is enforced by
                                       expire_overdue_reservations()
    - Nobody but belongings         -> flagged for the admin after BELONGINGS_THRESHOLD
    Returns a short description of what changed ("" when nothing did).
    """
    expire_overdue_reservations()
    seat = _lock_seat(seat_id)
    now = timezone.now()
    seat.last_camera_check = now
    changed = ""

    if not seat.is_maintenance:
        reservation = seat.current_reservation
        if reservation is None:
            if is_person_detected:
                _create_reservation(
                    seat=seat, user=None, user_name=WALKIN_NAME, role=Role.WALKIN, status=Status.ACTIVE,
                    checked_in_at=now,
                )
                changed = "walk-in occupied"
        elif not is_person_detected and reservation.role == Role.WALKIN and reservation.status == Status.ACTIVE:
            _close(reservation, EndReason.COMPLETED)
            changed = "walk-in released"

    _update_belongings(seat, now, is_person_detected, is_object_detected)
    seat.save(update_fields=["last_camera_check", "belongings_since", "belongings_flagged"])
    return changed


# ------------------------------------------------------------ serialization
def identity(user):
    """Display details of the logged-in account, shown in the dashboard banner."""
    profile = user.profile
    return {
        "name": profile.full_name,
        "roleText": profile.role_text,
        "avatar": profile.initials,
        "sub": profile.subtitle,
    }


def _iso(value):
    return value.isoformat() if value else None


def _seat_queryset():
    """Seats with their holding reservation (`open_reservations`) and waiting requests prefetched."""
    return (
        Seat.objects.select_related("zone")
        .prefetch_related(
            Prefetch(
                "reservations",
                queryset=Reservation.objects.filter(status__in=OPEN_STATUSES),
                to_attr="open_reservations",
            ),
            Prefetch(
                "reservations",
                queryset=Reservation.objects.filter(status=Status.PENDING).order_by("created_at"),
                to_attr="pending_reservations",
            ),
        )
        .order_by("zone__code", "number")
    )


def _serialize_seat(seat, user):
    reservation = seat.current_reservation or seat.pending_request
    return {
        "id": seat.seat_id,
        "zone": seat.zone.code,
        "zoneName": seat.zone.name,
        "number": seat.number,
        "status": seat.status,
        "reservationId": reservation.pk if reservation else None,
        "user": reservation.user_name if reservation else None,
        "mine": bool(reservation and reservation.user_id == user.id),
        "role": reservation.role if reservation else None,
        "time": reservation.time_display if reservation else None,
        "features": seat.features,
        "noShow": False,
        "unattended": seat.belongings_flagged,
    }


def _serialize_reservation(r):
    return {
        "id": r.pk,
        "seat": r.seat.seat_id,
        "zone": r.seat.zone.code,
        "zoneName": r.seat.zone.name,
        "features": r.seat.features,
        "status": r.status,
        "role": r.role,
        "user": r.user_name,
        "title": r.title,
        "code": r.confirmation_code or "",
        "time": r.time_display,
        "requestedAt": _iso(r.created_at),
        "acceptedAt": _iso(r.accepted_at),
        "graceExpiresAt": _iso(r.grace_period_expires_at),
        "checkedInAt": _iso(r.checked_in_at),
    }


def _my_reservations(user):
    queryset = (
        Reservation.objects.filter(user=user, status__in=[Status.PENDING, *OPEN_STATUSES])
        .select_related("seat__zone")
        .order_by("created_at")
    )
    return [_serialize_reservation(r) for r in queryset]


def _admin_queues():
    """Requests waiting for approval, and accepted reservations waiting for the user to check in."""
    pending = Reservation.objects.filter(status=Status.PENDING).select_related("seat__zone").order_by("created_at")
    awaiting = Reservation.objects.filter(status=Status.RESERVED).select_related("seat__zone").order_by("grace_period_expires_at")
    awaiting_rows = []
    for r in awaiting:
        row = _serialize_reservation(r)
        row["code"] = ""  # the Admin checks the code the user presents; it is never shown back
        awaiting_rows.append(row)
    return [_serialize_reservation(r) for r in pending], awaiting_rows


def _notification_state(user):
    queryset = user.notifications.all()
    return {
        "unread": queryset.filter(read_at__isnull=True).count(),
        "items": [
            {"id": n.pk, "message": n.message, "kind": n.kind, "at": _iso(n.created_at), "read": n.read_at is not None}
            for n in queryset[:NOTIFICATIONS_SHOWN]
        ],
    }


def _pct(part, whole):
    return round(part / whole * 100) if whole else 0


def build_state(role, user):
    """Everything the front end needs to render all three role views."""
    expire_overdue_reservations()
    zones = [
        {"id": z.code, "name": z.name, "title": z.title, "desc": z.description, "total": z.seat_total}
        for z in Zone.objects.annotate(seat_total=Count("seats"))
    ]
    seats = [_serialize_seat(s, user) for s in _seat_queryset()]

    total = len(seats)
    available = sum(1 for s in seats if s["status"] == "available")
    occupied = sum(1 for s in seats if s["status"] == "occupied")
    reserved = sum(1 for s in seats if s["status"] == "reserved")
    pending = sum(1 for s in seats if s["status"] == "pending")
    summary = {
        "total": total,
        "available": available,
        "occupied": occupied,
        "reserved": reserved,
        "pending": pending,
        "availablePct": _pct(available, total),
        "occupiedPct": _pct(occupied, total),
    }
    state = {
        "role": role,
        "users": {role: identity(user)},
        "zones": zones,
        "seats": seats,
        "summary": summary,
        "myReservations": _my_reservations(user),
        "notifications": _notification_state(user),
    }
    if role == "admin":
        state["pendingRequests"], state["awaiting"] = _admin_queues()
        state["analytics"] = get_seat_and_reservation_analytics()
    return state


# ---------------------------------------------------------------- analytics
def _pct1(part, whole):
    """Percentage rounded to 1 decimal; 0.0 when there is nothing to divide by."""
    return round(part / whole * 100, 1) if whole else 0.0


def _share(count, whole):
    return {"count": count, "pct": _pct1(count, whole)}


def _hour_counts(queryset, field):
    counts = [0] * 24
    for row in queryset.annotate(h=ExtractHour(field)).values("h").annotate(n=Count("id")):
        counts[row["h"]] = row["n"]
    return counts


def _peaks(counts, limit=3):
    ranked = sorted(((n, h) for h, n in enumerate(counts) if n), key=lambda t: (-t[0], t[1]))[:limit]
    return [{"hour": h, "label": f"{h:02d}:00-{(h + 1) % 24:02d}:00", "count": n} for n, h in ranked]


def get_seat_and_reservation_analytics():
    """
    Live occupancy figures plus reservation statistics, all percentages rounded to 1 decimal.
    Walk-in (camera) sessions count towards occupancy but not towards reservation statistics.
    """
    now = timezone.now()

    # -- live seat occupancy
    seats = list(_seat_queryset())
    total = len(seats)
    counts = {"available": 0, "pending": 0, "occupied": 0, "reserved": 0, "maintenance": 0}
    walkin = 0
    zone_counts = {}
    for seat in seats:
        status = seat.status
        counts[status] += 1
        if status == "occupied" and seat.current_reservation.role == Role.WALKIN:
            walkin += 1
        zc = zone_counts.setdefault(seat.zone, dict.fromkeys(("total", *counts), 0))
        zc["total"] += 1
        zc[status] += 1

    seat_block = {"total": total, **{k: _share(v, total) for k, v in counts.items()}}
    occupied_block = {"walkin": _share(walkin, total), "checkedIn": _share(counts["occupied"] - walkin, total)}
    zones = [
        {
            "id": zone.code,
            "name": zone.name,
            "total": zc["total"],
            "available": _share(zc["available"], zc["total"]),
            "occupied": _share(zc["occupied"], zc["total"]),
            "reserved": _share(zc["reserved"], zc["total"]),
            "utilization": _pct1(zc["occupied"] + zc["reserved"], zc["total"]),
        }
        for zone, zc in zone_counts.items()
    ]

    # -- reservations (walk-ins are not reservations)
    booked = Reservation.objects.exclude(role=Role.WALKIN)
    today_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    agg = booked.aggregate(
        total=Count("id"),
        today=Count("id", filter=Q(created_at__gte=today_start)),
        week=Count("id", filter=Q(created_at__gte=week_start)),
        awaiting_approval=Count("id", filter=Q(status=Status.PENDING)),
        pending=Count("id", filter=Q(status=Status.RESERVED)),
        in_session=Count("id", filter=Q(status=Status.ACTIVE)),
        completed=Count("id", filter=Q(status=Status.RELEASED, checked_in_at__isnull=False)),
        cancelled=Count("id", filter=Q(status=Status.RELEASED, checked_in_at__isnull=True)),
        expired=Count("id", filter=Q(status=Status.EXPIRED)),
        rejected=Count("id", filter=Q(status=Status.REJECTED)),
    )
    r_total = agg["total"]
    reservations = {
        "total": r_total,
        "today": agg["today"],
        "week": agg["week"],
        "allTime": r_total,
        "status": {
            "awaitingApproval": _share(agg["awaiting_approval"], r_total),
            "pending": _share(agg["pending"], r_total),
            "inSession": _share(agg["in_session"], r_total),
            "completed": _share(agg["completed"], r_total),
            "cancelled": _share(agg["cancelled"], r_total),
            "expired": _share(agg["expired"], r_total),
            "rejected": _share(agg["rejected"], r_total),
        },
        # accepted and still holding a seat (waiting for check-in or in session)
        "activeRate": _pct1(agg["pending"] + agg["in_session"], r_total),
        # the user actually showed up (now or earlier)
        "fulfillmentRate": _pct1(agg["in_session"] + agg["completed"], r_total),
        # never showed up: cancelled before check-in, or expired
        "noShowRate": _pct1(agg["cancelled"] + agg["expired"], r_total),
    }

    by_role = {row["role"]: row["n"] for row in booked.values("role").annotate(n=Count("id"))}
    roles = [
        {"role": value, "label": label, **_share(by_role.get(value, 0), r_total)}
        for value, label in Reservation.Role.choices
        if value != Role.WALKIN
    ]

    # -- busiest hours and seats
    reservation_hours = _hour_counts(booked, "created_at")
    occupancy_hours = _hour_counts(Reservation.objects.filter(checked_in_at__isnull=False), "checked_in_at")

    all_sessions = Reservation.objects.count()
    top = list(
        Reservation.objects.values("seat__seat_id", "seat__zone__code")
        .annotate(n=Count("id"))
        .order_by("-n", "seat__seat_id")[:5]
    )
    minutes = {}
    for seat_id, checked_in, released in Reservation.objects.filter(
        seat__seat_id__in=[t["seat__seat_id"] for t in top], checked_in_at__isnull=False
    ).values_list("seat__seat_id", "checked_in_at", "released_at"):
        minutes[seat_id] = minutes.get(seat_id, 0) + int(((released or now) - checked_in).total_seconds() // 60)
    top_seats = [
        {
            "seat": t["seat__seat_id"],
            "zone": t["seat__zone__code"],
            "count": t["n"],
            "pct": _pct1(t["n"], all_sessions),
            "minutes": minutes.get(t["seat__seat_id"], 0),
        }
        for t in top
    ]

    return {
        "seats": seat_block,
        "occupied": occupied_block,
        "zones": zones,
        "reservations": reservations,
        "roles": roles,
        "hours": {
            "reservations": reservation_hours,
            "occupancy": occupancy_hours,
            "peakReservations": _peaks(reservation_hours),
            "peakOccupancy": _peaks(occupancy_hours),
        },
        "topSeats": top_seats,
    }


# ------------------------------------------------------- historical report
MAX_REPORT_DAYS = 366


def get_date_report(start, end=None):
    """
    Analytics for one day, or an inclusive date range (datetime.date objects, in the server time zone).

    Request figures use the reservations *requested* in the range (so the percentages share one
    denominator); occupancy uses every session that overlapped the range. The seat total is today's.
    """
    end = end or start
    if end < start:
        start, end = end, start
    if (end - start).days >= MAX_REPORT_DAYS:
        raise BookingError(f"Choose a range of at most {MAX_REPORT_DAYS} days.", code="invalid_input")

    tz = timezone.get_current_timezone()
    lo = timezone.make_aware(datetime.combine(start, dtime.min), tz)
    hi = timezone.make_aware(datetime.combine(end + timedelta(days=1), dtime.min), tz)
    now = timezone.now()

    cancelled_q = Q(end_reason=EndReason.CANCELLED) | Q(status=Status.RELEASED, end_reason="", checked_in_at__isnull=True)
    forced_q = Q(end_reason__in=[EndReason.FORCE_RELEASED, EndReason.BLOCKED])
    requests = Reservation.objects.exclude(role=Role.WALKIN).filter(created_at__gte=lo, created_at__lt=hi)
    agg = requests.aggregate(
        total=Count("id"),
        awaiting=Count("id", filter=Q(status=Status.PENDING)),
        accepted=Count("id", filter=Q(accepted_at__isnull=False)),
        verified=Count("id", filter=Q(checked_in_at__isnull=False)),
        no_show=Count("id", filter=Q(status=Status.EXPIRED)),
        forced=Count("id", filter=forced_q),
        cancelled=Count("id", filter=cancelled_q & ~forced_q),
        rejected=Count("id", filter=Q(status=Status.REJECTED)),
    )
    total = agg["total"]
    released_or_cancelled = agg["forced"] + agg["cancelled"]

    # -- occupancy: sessions (checked in) that overlapped the range
    seat_total = Seat.objects.count()
    sessions = Reservation.objects.filter(checked_in_at__isnull=False, checked_in_at__lt=hi).filter(
        Q(released_at__isnull=True) | Q(released_at__gt=lo)
    )
    n_sessions = sessions.count()
    walkin_sessions = sessions.filter(role=Role.WALKIN).count()
    seats_used = sessions.values("seat_id").distinct().count()
    walkin_seats = sessions.filter(role=Role.WALKIN).values("seat_id").distinct().count()
    reserved_seats = sessions.exclude(role=Role.WALKIN).values("seat_id").distinct().count()

    by_role = {row["role"]: row["n"] for row in requests.values("role").annotate(n=Count("id"))}
    roles = [
        {"role": value, "label": label, **_share(by_role.get(value, 0), total)}
        for value, label in Reservation.Role.choices
        if value != Role.WALKIN
    ]

    # -- most used seats: every session started in the range, with the time they were occupied inside it
    started = Reservation.objects.filter(created_at__gte=lo, created_at__lt=hi)
    started_total = started.count()
    top = list(
        started.values("seat__seat_id", "seat__zone__code").annotate(n=Count("id")).order_by("-n", "seat__seat_id")[:5]
    )
    minutes = {}
    for seat_id, checked_in, released in sessions.filter(seat__seat_id__in=[t["seat__seat_id"] for t in top]).values_list(
        "seat__seat_id", "checked_in_at", "released_at"
    ):
        span_start, span_end = max(checked_in, lo), min(released or now, hi)
        if span_end > span_start:
            minutes[seat_id] = minutes.get(seat_id, 0) + int((span_end - span_start).total_seconds() // 60)
    top_seats = [
        {
            "seat": t["seat__seat_id"],
            "zone": t["seat__zone__code"],
            "count": t["n"],
            "pct": _pct1(t["n"], started_total),
            "minutes": minutes.get(t["seat__seat_id"], 0),
        }
        for t in top
    ]

    today = timezone.localdate()
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "includesToday": start <= today <= end,
        "requests": {
            "total": total,
            "awaitingApproval": _share(agg["awaiting"], total),
            "accepted": _share(agg["accepted"], total),
            "verified": _share(agg["verified"], total),
            "noShow": _share(agg["no_show"], total),
            "forcedOrCancelled": _share(released_or_cancelled, total),
            "forceReleased": _share(agg["forced"], total),
            "cancelled": _share(agg["cancelled"], total),
            "rejected": _share(agg["rejected"], total),
        },
        "occupancy": {
            "seatTotal": seat_total,
            "seatsUsed": seats_used,
            "rate": _pct1(seats_used, seat_total),
            "sessions": n_sessions,
            "walkin": {"sessions": walkin_sessions, "seats": walkin_seats, "pct": _pct1(walkin_sessions, n_sessions),
                       "seatPct": _pct1(walkin_seats, seat_total)},
            "reserved": {"sessions": n_sessions - walkin_sessions, "seats": reserved_seats,
                         "pct": _pct1(n_sessions - walkin_sessions, n_sessions), "seatPct": _pct1(reserved_seats, seat_total)},
        },
        "roles": roles,
        "topSeats": top_seats,
    }
