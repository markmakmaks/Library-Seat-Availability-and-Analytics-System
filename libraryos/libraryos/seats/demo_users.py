"""
Demo identities used by the role switcher in the header.

The original prototype had no login: anyone can flip between Admin, Professor
and Student. That behaviour is kept here (the chosen role lives in the session).
To move to real accounts later, replace `_current_role()` in views.py with a
lookup based on `request.user` and delete this module.
"""

DEFAULT_ROLE = "admin"

DEMO_USERS = {
    "admin": {
        "name": "Dr. James Okafor",
        "roleText": "System Admin",
        "avatar": "JO",
        "sub": "Main Campus Central Library High Authority",
    },
    "professor": {
        "name": "Dr. Patricia Hall",
        "roleText": "Faculty Professor",
        "avatar": "PH",
        "sub": "Computer Science Department Priority Privileges",
    },
    "student": {
        "name": "Alex Martinez",
        "roleText": "Student (CS Senior)",
        "avatar": "AM",
        "sub": "Student ID: 2023-88219 • Single Seat Constraint",
    },
}
