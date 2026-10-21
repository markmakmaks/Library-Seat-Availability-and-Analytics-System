"""
Business rules for LibraryOS, kept out of the views so they are easy to test.

Every mutating function runs inside a transaction and raises BookingError when a
rule is broken. The views turn that into a JSON error response.
"""
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch
from django.utils import timezone

from .demo_users import DEMO_USERS
from .models import OPEN_STATUSES, Reservation, Seat, Zone

STUDENT_DURATIONS = (1, 2, 3)
PROFESSOR_DURATIONS = (2, 4, 6)
MAX_TITLE_LENGTH = 100
DEFAULT_PROF_TITLE = "Faculty Group Consultation"

Status = Reservation.Status
Role = Reservation.Role


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


def _ensure_available(seat):
    if seat.is_maintenance:
        raise BookingError(f"Seat {seat.seat_id} is under maintenance.", status=409, code="unavailable")
    if seat.current_reservation is not None:
        raise BookingError(f"Seat {seat.seat_id} is no longer available.", status=409, code="unavailable")


def _create_reservation(**fields):
    try:
        with transaction.atomic():  # savepoint, so an IntegrityError doesn't poison the outer transaction
            return Reservation.objects.create(**fields)
    except IntegrityError:
        raise BookingError("That seat was just taken by someone else.", status=409, code="seat_taken")


def _close(reservation):
    reservation.status = Status.RELEASED
    reservation.released_at = timezone.now()
    reservation.save(update_fields=["status", "released_at"])


# ----------------------------------------------------------------- actions
@transaction.atomic
def reserve_seat_for_student(seat_id, duration, start_hour=None):
    user = DEMO_USERS["student"]
    duration = _parse_choice(duration, STUDENT_DURATIONS, "duration")
    seat = _lock_seat(seat_id)

    existing = (
        Reservation.objects.select_related("seat__zone")
        .filter(role=Role.STUDENT, user_name=user["name"], status__in=OPEN_STATUSES)
        .first()
    )
    if existing:
        raise BookingError(
            "Students may hold only one active or upcoming reservation at a time.",
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
    _create_reservation(
        seat=seat,
        user_name=user["name"],
        role=Role.STUDENT,
        start_hour=start,
        end_hour=start + duration,
        status=Status.RESERVED,
    )
    return f"Seat {seat.seat_id} successfully reserved! Please check in within 15 mins.", "success"


@transaction.atomic
def batch_reserve_for_professor(seat_ids, title, duration, start_hour=None):
    user = DEMO_USERS["professor"]
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
    for seat in seats:  # all-or-nothing: any failure rolls the whole batch back
        _ensure_available(seat)
        _create_reservation(
            seat=seat,
            user_name=user["name"],
            role=Role.PROFESSOR,
            title=title,
            start_hour=start,
            end_hour=start + duration,
            status=Status.RESERVED,
        )
    return f"Successfully created batch reservation for {len(seats)} seats!", "success"


@transaction.atomic
def release_seat(role, seat_id):
    seat = _lock_seat(seat_id)
    reservation = seat.current_reservation

    if role == "admin":
        if reservation is None and not seat.is_maintenance:
            raise BookingError(f"Seat {seat.seat_id} is already available.", status=409, code="already_available")
        if seat.is_maintenance:
            seat.is_maintenance = False
            seat.save(update_fields=["is_maintenance"])
    else:
        user = DEMO_USERS[role]
        if reservation is None or reservation.user_name != user["name"] or reservation.role != role:
            raise BookingError("You can only release your own reservations.", status=403, code="forbidden")

    if reservation is not None:
        _close(reservation)
    return f"Seat {seat.seat_id} has been released and is now available.", "info"


@transaction.atomic
def toggle_maintenance(seat_id):
    seat = _lock_seat(seat_id)
    if seat.is_maintenance:
        seat.is_maintenance = False
        message = f"Seat {seat.seat_id} is back in service."
    else:
        reservation = seat.current_reservation
        if reservation is not None:
            _close(reservation)
        seat.is_maintenance = True
        message = f"Seat {seat.seat_id} has been blocked for maintenance."
    seat.save(update_fields=["is_maintenance"])
    return message, "info"


@transaction.atomic
def check_in(seat_id):
    user = DEMO_USERS["student"]
    seat = _lock_seat(seat_id)
    reservation = seat.current_reservation
    if reservation is None or reservation.role != Role.STUDENT or reservation.user_name != user["name"]:
        raise BookingError("That seat is not reserved by you.", status=403, code="forbidden")
    if reservation.status == Status.ACTIVE:
        raise BookingError("You are already checked in.", status=409, code="already_checked_in")
    if reservation.status == Status.EXPIRED:
        raise BookingError("This reservation expired. Please book a new seat.", status=409, code="expired")
    reservation.status = Status.ACTIVE
    reservation.save(update_fields=["status"])
    return "QR Code Verified! Checked in successfully to your seat.", "success"


# ------------------------------------------------------------ serialization
def _serialize_seat(seat):
    reservation = seat.current_reservation
    return {
        "id": seat.seat_id,
        "zone": seat.zone.code,
        "zoneName": seat.zone.name,
        "number": seat.number,
        "status": seat.status,
        "user": reservation.user_name if reservation else None,
        "role": reservation.role if reservation else None,
        "time": reservation.time_display if reservation else None,
        "features": seat.features,
        "noShow": bool(reservation and reservation.status == Status.EXPIRED),
    }


def _pct(part, whole):
    return round(part / whole * 100) if whole else 0


def build_state(role):
    """Everything the front end needs to render all three role views."""
    zones = [
        {"id": z.code, "name": z.name, "title": z.title, "desc": z.description, "total": z.seat_total}
        for z in Zone.objects.annotate(seat_total=Count("seats"))
    ]
    seat_qs = (
        Seat.objects.select_related("zone")
        .prefetch_related(
            Prefetch(
                "reservations",
                queryset=Reservation.objects.filter(status__in=OPEN_STATUSES),
                to_attr="open_reservations",
            )
        )
        .order_by("zone__code", "number")
    )
    seats = [_serialize_seat(s) for s in seat_qs]

    total = len(seats)
    available = sum(1 for s in seats if s["status"] == "available")
    occupied = sum(1 for s in seats if s["status"] == "occupied")
    reserved = sum(1 for s in seats if s["status"] == "reserved")
    summary = {
        "total": total,
        "available": available,
        "occupied": occupied,
        "reserved": reserved,
        "availablePct": _pct(available, total),
        "occupiedPct": _pct(occupied, total),
    }
    return {"role": role, "users": DEMO_USERS, "zones": zones, "seats": seats, "summary": summary}
