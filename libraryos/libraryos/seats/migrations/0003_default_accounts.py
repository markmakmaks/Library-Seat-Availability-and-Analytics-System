from django.contrib.auth.hashers import make_password
from django.db import migrations

from seats.default_accounts import DEFAULT_ACCOUNTS


def create_default_accounts(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Profile = apps.get_model("seats", "Profile")
    Reservation = apps.get_model("seats", "Reservation")

    for acct in DEFAULT_ACCOUNTS:
        is_admin = acct["role"] == "admin"
        user, created = User.objects.get_or_create(
            username=acct["username"],
            defaults={
                "first_name": acct["first_name"],
                "last_name": acct["last_name"],
                "email": acct["email"],
                "password": make_password(acct["password"]),
                "is_staff": is_admin,
                "is_superuser": is_admin,
                "is_active": True,
            },
        )
        Profile.objects.get_or_create(
            user=user,
            defaults={
                "role": acct["role"],
                "status": "approved",
                "first_name": acct["first_name"],
                "middle_name": acct["middle_name"],
                "last_name": acct["last_name"],
                "year_level": acct.get("year_level", ""),
                "course": acct.get("course", ""),
                "department": acct.get("department", ""),
                "id_number": acct.get("id_number", ""),
            },
        )
        # Link demo reservations from the old role-switcher prototype to the real account.
        if acct["role"] in ("student", "professor"):
            full_name = f"{acct['first_name']} {acct['last_name']}"
            Reservation.objects.filter(user__isnull=True, role=acct["role"], user_name=full_name).update(user=user)


class Migration(migrations.Migration):
    dependencies = [
        ("seats", "0002_profile_and_reservation_user"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(create_default_accounts, migrations.RunPython.noop)]
