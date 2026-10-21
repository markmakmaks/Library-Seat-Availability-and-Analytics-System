# LibraryOS (Django)

Library seat monitoring & reservation system, converted from the single-file HTML prototype
into a Django project. Seats and reservations are now stored in a database and every action
(reserve, batch reserve, check-in, release, maintenance) is validated on the server.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py makemigrations seats
python manage.py migrate
python manage.py seed_seats      # 4 zones x 18 seats + the prototype's demo reservations
python manage.py runserver
```

Open http://127.0.0.1:8000/ . Optional: `python manage.py createsuperuser` and visit `/admin/`.
Use `python manage.py seed_seats --reset` to wipe and rebuild the demo data.

## Layout

```
libraryos/                 project package (settings, urls, wsgi/asgi)
manage.py
requirements.txt
seats/
  models.py                Zone, Seat, Reservation (seat status is derived, never stored)
  services.py              booking rules + state serialization
  views.py, urls.py        page view + JSON API
  demo_users.py            the Admin / Professor / Student demo identities
  admin.py                 Django admin registration
  management/commands/seed_seats.py
  templates/seats/         base.html, index.html, partials/ (header, admin, professor, student, modals...)
  static/seats/            css/styles.css, js/app.js, js/tailwind-config.js
```

## JSON API (POST, CSRF-protected, used by `static/seats/js/app.js`)

| URL | Who | What |
|---|---|---|
| `GET api/state/` | all | full seat state (the page polls this every 15 s) |
| `api/role/` | all | switch demo role (stored in the session) |
| `api/seats/<id>/reserve/` | student | reserve one seat (enforces the single-booking rule) |
| `api/seats/batch-reserve/` | professor | all-or-nothing multi-seat reservation |
| `api/seats/<id>/check-in/` | student | QR check-in, reserved -> occupied |
| `api/seats/<id>/release/` | admin any; others only their own | free a seat |
| `api/seats/<id>/maintenance/` | admin | block / unblock a seat |

## Notes

- **No login yet.** Like the prototype, the header role switcher stands in for authentication.
  To use real accounts, replace `_current_role()` in `seats/views.py` with a lookup on `request.user`.
- The "Advanced Analytics" charts are still static mock-ups from the original HTML.
- Tailwind, Font Awesome and Inter are loaded from CDNs, as in the original.
- Settings read `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_TIME_ZONE`.
