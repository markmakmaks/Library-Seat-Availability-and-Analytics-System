"""Pre-approved test accounts created by migration 0003 and `manage.py create_default_accounts`."""

DEFAULT_ACCOUNTS = [
    {
        "username": "Admin",
        "password": "Admin123",
        "role": "admin",
        "first_name": "James",
        "middle_name": "",
        "last_name": "Okafor",
        "email": "admin@libraryos.local",
        "is_staff": True,
    },
    {
        "username": "Professor",
        "password": "Professor123",
        "role": "professor",
        "first_name": "Patricia",
        "middle_name": "",
        "last_name": "Hall",
        "email": "professor@libraryos.local",
        "department": "Computer Science",
        "id_number": "PROF-0001",
    },
    {
        "username": "Student",
        "password": "Student123",
        "role": "student",
        "first_name": "Alex",
        "middle_name": "",
        "last_name": "Martinez",
        "email": "student@libraryos.local",
        "year_level": "4th Year",
        "course": "BS Computer Science",
        "id_number": "2023-88219",
    },
]
