from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from seats.default_accounts import DEFAULT_ACCOUNTS
from seats.models import Profile


class Command(BaseCommand):
    help = "Create (or reset) the default Admin / Professor / Student test accounts."

    @transaction.atomic
    def handle(self, *args, **options):
        for acct in DEFAULT_ACCOUNTS:
            is_admin = acct["role"] == "admin"
            user, created = User.objects.update_or_create(
                username=acct["username"],
                defaults={
                    "first_name": acct["first_name"],
                    "last_name": acct["last_name"],
                    "email": acct["email"],
                    "is_staff": is_admin,
                    "is_superuser": is_admin,
                    "is_active": True,
                },
            )
            user.set_password(acct["password"])
            user.save(update_fields=["password"])
            Profile.objects.update_or_create(
                user=user,
                defaults={
                    "role": acct["role"],
                    "status": Profile.Status.APPROVED,
                    "first_name": acct["first_name"],
                    "middle_name": acct["middle_name"],
                    "last_name": acct["last_name"],
                    "year_level": acct.get("year_level", ""),
                    "course": acct.get("course", ""),
                    "department": acct.get("department", ""),
                    "id_number": acct.get("id_number", ""),
                },
            )
            self.stdout.write(f"{'Created' if created else 'Reset'} {acct['role']} account: {acct['username']}")
