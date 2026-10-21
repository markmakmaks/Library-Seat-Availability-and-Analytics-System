from django.conf import settings
from django.db import models
from django.db.models import Q

# A reservation that still "holds" its seat. Released reservations are kept as history.
OPEN_STATUSES = ["reserved", "active", "expired"]


class Zone(models.Model):
    code = models.CharField(max_length=20, unique=True, help_text='e.g. "Zone A"')
    title = models.CharField(max_length=80, help_text='e.g. "Quiet Study"')
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.name

    @property
    def letter(self):
        return self.code.split()[-1]

    @property
    def name(self):
        return f"{self.code} - {self.title}"


class Seat(models.Model):
    zone = models.ForeignKey(Zone, on_delete=models.CASCADE, related_name="seats")
    number = models.PositiveSmallIntegerField()
    seat_id = models.CharField(max_length=8, unique=True, blank=True, help_text="Generated, e.g. A01")
    features = models.CharField(max_length=200, blank=True)
    is_maintenance = models.BooleanField(default=False)

    class Meta:
        ordering = ["zone__code", "number"]
        constraints = [
            models.UniqueConstraint(fields=["zone", "number"], name="unique_seat_number_per_zone"),
        ]

    def __str__(self):
        return self.seat_id

    def save(self, *args, **kwargs):
        if not self.seat_id:
            self.seat_id = f"{self.zone.letter}{self.number:02d}"
        super().save(*args, **kwargs)

    @property
    def current_reservation(self):
        """The reservation currently holding this seat (or None). Uses the prefetch cache if present."""
        cached = getattr(self, "open_reservations", None)
        if cached is not None:
            return cached[0] if cached else None
        return self.reservations.filter(status__in=OPEN_STATUSES).first()

    @property
    def status(self):
        """available | occupied | reserved | maintenance (derived, never stored)."""
        if self.is_maintenance:
            return "maintenance"
        reservation = self.current_reservation
        if reservation is None:
            return "available"
        return "occupied" if reservation.status == Reservation.Status.ACTIVE else "reserved"


class Reservation(models.Model):
    class Status(models.TextChoices):
        RESERVED = "reserved", "Reserved (pending check-in)"
        ACTIVE = "active", "Active (checked in)"
        EXPIRED = "expired", "Expired (no-show)"
        RELEASED = "released", "Released"

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        PROFESSOR = "professor", "Professor"

    seat = models.ForeignKey(Seat, on_delete=models.CASCADE, related_name="reservations")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reservations"
    )
    user_name = models.CharField(max_length=120)
    role = models.CharField(max_length=20, choices=Role.choices)
    title = models.CharField(max_length=100, blank=True, help_text="Session / class title (faculty batches)")
    start_hour = models.PositiveSmallIntegerField(null=True, blank=True)
    end_hour = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RESERVED)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Database-level guarantee against double-booking a seat.
            models.UniqueConstraint(
                fields=["seat"],
                condition=Q(status__in=OPEN_STATUSES),
                name="one_open_reservation_per_seat",
            ),
        ]

    def __str__(self):
        return f"{self.seat.seat_id} - {self.user_name} ({self.status})"

    @property
    def time_display(self):
        if self.status == self.Status.EXPIRED:
            return "Expired (No-Show)"
        if self.start_hour is None or self.end_hour is None:
            return "Active Session"
        label = f"{self.start_hour:02d}:00 - {self.end_hour % 24:02d}:00"
        return f"{label} ({self.title})" if self.title else label


class Profile(models.Model):
    """Role and registration details for a login account. Created at sign-up, approved by an Admin."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        PROFESSOR = "professor", "Professor"
        STUDENT = "student", "Student"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    class YearLevel(models.TextChoices):
        FIRST = "1st Year", "1st Year"
        SECOND = "2nd Year", "2nd Year"
        THIRD = "3rd Year", "3rd Year"
        FOURTH = "4th Year", "4th Year"
        FIFTH = "5th Year", "5th Year"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Role.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    last_name = models.CharField(max_length=80)
    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    year_level = models.CharField(max_length=20, choices=YearLevel.choices, blank=True)
    course = models.CharField(max_length=120, blank=True)
    department = models.CharField(max_length=120, blank=True)
    id_number = models.CharField(max_length=30, blank=True, help_text="Student ID or Professor ID")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["role", "id_number"],
                condition=~Q(id_number=""),
                name="unique_id_number_per_role",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} ({self.role}, {self.status})"

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)

    @property
    def initials(self):
        letters = (self.first_name[:1] + self.last_name[:1]).upper()
        return letters or self.user.username[:2].upper()

    @property
    def role_text(self):
        return {"admin": "System Admin", "professor": "Faculty Professor", "student": "Student"}[self.role]

    @property
    def subtitle(self):
        if self.role == self.Role.STUDENT:
            return f"{self.course} • {self.year_level} • Student ID: {self.id_number}"
        if self.role == self.Role.PROFESSOR:
            return f"{self.department} Department • Professor ID: {self.id_number}"
        return "Main Campus Central Library High Authority"
