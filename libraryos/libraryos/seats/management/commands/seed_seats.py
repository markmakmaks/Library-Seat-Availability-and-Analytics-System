import random

from django.core.management.base import BaseCommand
from django.db import transaction

from django.contrib.auth.models import User
from seats.models import Reservation, Seat, Zone

ZONES = [
    ("Zone A", "Quiet Study", "Silent zone, individual desks"),
    ("Zone B", "Group Work", "Collaborative tables, whiteboards"),
    ("Zone C", "Digital Lab", "Computers & charging stations"),
    ("Zone D", "Reading Lounge", "Comfortable seating, natural light"),
]
SEATS_PER_ZONE = 18


class Command(BaseCommand):
    help = "Create the 4 zones x 18 seats and the demo reservations from the original prototype."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete all zones, seats and reservations first.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            Reservation.objects.all().delete()
            Seat.objects.all().delete()
            Zone.objects.all().delete()
        elif Zone.objects.exists():
            self.stdout.write(self.style.WARNING("Seat data already exists. Re-run with --reset to rebuild it."))
            return

        rng = random.Random(2026)
        seat_rows = []
        for code, title, description in ZONES:
            zone = Zone.objects.create(code=code, title=title, description=description)
            for number in range(1, SEATS_PER_ZONE + 1):
                features = ("Power Outlet, " if number % 2 == 0 else "") + ("Monitor, " if number % 3 == 0 else "") + "Desk Lamp"
                seat_rows.append(
                    Seat(
                        zone=zone,
                        number=number,
                        seat_id=f"{zone.letter}{number:02d}",
                        features=features,
                        is_maintenance=(code == "Zone C" and number == SEATS_PER_ZONE),
                    )
                )
        Seat.objects.bulk_create(seat_rows)
        seats = Seat.objects.in_bulk(field_name="seat_id")

        student_user = User.objects.filter(username="Student").first()
        professor_user = User.objects.filter(username="Professor").first()
        student = student_user.profile.full_name if student_user else "Alex Martinez"
        professor = professor_user.profile.full_name if professor_user else "Dr. Patricia Hall"
        Status, Role = Reservation.Status, Reservation.Role

        def book(seat_id, user, role, status, start=None, end=None, title=""):
            owner = {student: student_user, professor: professor_user}.get(user)
            Reservation.objects.create(
                seat=seats[seat_id], user=owner, user_name=user, role=role, status=status,
                start_hour=start, end_hour=end, title=title,
            )

        # Demo data that mirrors the prototype
        book("A15", student, Role.STUDENT, Status.RESERVED, 11, 13)
        book("B12", professor, Role.PROFESSOR, Status.RESERVED, 14, 18, "CS101 Group Study")
        book("B13", professor, Role.PROFESSOR, Status.RESERVED, 14, 18, "CS101 Group Study")
        book("C14", "Student #409", Role.STUDENT, Status.EXPIRED)  # no-show waiting for release

        taken = {"A15", "B12", "B13", "C14"}
        for seat_id, seat in sorted(seats.items()):
            if seat.number % 5 == 0 and seat_id not in taken:
                book(seat_id, f"Student #{rng.randint(100, 899)}", Role.STUDENT, Status.ACTIVE)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Zone.objects.count()} zones, {Seat.objects.count()} seats, "
            f"{Reservation.objects.count()} demo reservations."
        ))
