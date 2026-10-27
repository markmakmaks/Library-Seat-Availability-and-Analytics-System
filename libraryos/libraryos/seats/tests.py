import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from seats import services as sv
from seats.models import AdminActionLog, Notification, Profile, Reservation, Seat, Zone


def make_user(name, role):
    user = User.objects.create_user(name, password="x")
    Profile.objects.create(user=user, role=role, status="approved", first_name=name.title(), last_name="T")
    return user


class ApprovalFlowTests(TestCase):
    def setUp(self):
        zone = Zone.objects.create(code="Zone A", title="Q")
        for n in (1, 2, 3):
            Seat.objects.create(zone=zone, number=n)
        self.admin = make_user("adm", "admin")
        self.student = make_user("stu", "student")
        self.prof = make_user("prof", "professor")

    def request(self, seat="A01"):
        sv.reserve_seat_for_student(self.student, seat, 2, 9)
        return Reservation.objects.get(user=self.student, status="pending")

    def test_request_is_pending_until_accepted(self):
        r = self.request()
        self.assertEqual(Seat.objects.get(seat_id="A01").status, "pending")
        self.assertTrue(r.confirmation_code.startswith("LIB-"))
        self.assertEqual(Notification.objects.filter(user=self.admin).count(), 1)
        with self.assertRaises(sv.BookingError):  # one request at a time
            sv.reserve_seat_for_student(self.student, "A02", 1, 9)
        other = make_user("stu2", "student")
        with self.assertRaises(sv.BookingError):  # seat already has a request
            sv.reserve_seat_for_student(other, "A01", 1, 9)

        sv.accept_reservation(self.admin, r.pk)
        r.refresh_from_db()
        self.assertEqual(Seat.objects.get(seat_id="A01").status, "reserved")
        self.assertEqual(r.grace_period_expires_at - r.accepted_at, timedelta(minutes=15))
        msg = Notification.objects.filter(user=self.student).first().message
        self.assertIn(r.confirmation_code, msg)
        self.assertTrue(AdminActionLog.objects.filter(action="reservation_accepted", admin=self.admin).exists())

    def test_verify_code(self):
        r = self.request()
        with self.assertRaises(sv.BookingError):
            sv.verify_confirmation_code(self.admin, r.confirmation_code)  # not accepted yet
        sv.accept_reservation(self.admin, r.pk)
        with self.assertRaises(sv.BookingError):
            sv.verify_confirmation_code(self.admin, "LIB-NOPE00")
        sv.verify_confirmation_code(self.admin, r.confirmation_code.lower().replace("lib-", ""))
        r.refresh_from_db()
        self.assertEqual(r.status, "active")
        self.assertIsNotNone(r.checked_in_at)
        self.assertEqual(Seat.objects.get(seat_id="A01").status, "occupied")
        with self.assertRaises(sv.BookingError):
            sv.verify_confirmation_code(self.admin, r.confirmation_code)  # twice

    def test_grace_period_expiry_frees_seat(self):
        r = self.request()
        sv.accept_reservation(self.admin, r.pk)
        Reservation.objects.filter(pk=r.pk).update(grace_period_expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(sv.BookingError) as ctx:
            sv.verify_confirmation_code(self.admin, r.confirmation_code)  # too late
        self.assertEqual(ctx.exception.code, "expired")
        r.refresh_from_db()
        self.assertEqual((r.status, r.end_reason), ("expired", "no_show"))
        self.assertEqual(Seat.objects.get(seat_id="A01").status, "available")
        self.assertTrue(AdminActionLog.objects.filter(action="no_show_expired", admin_username="system").exists())
        self.assertTrue(Notification.objects.filter(user=self.student, message__contains="expired").exists())
        sv.reserve_seat_for_student(self.student, "A01", 1, 9)  # free to book again

    def test_reject_force_release_and_block(self):
        r = self.request("A01")
        sv.reject_reservation(self.admin, r.pk)
        self.assertEqual(Seat.objects.get(seat_id="A01").status, "available")

        r2 = self.request("A02")
        sv.accept_reservation(self.admin, r2.pk)
        sv.release_seat("admin", self.admin, "A02")
        r2.refresh_from_db()
        self.assertEqual(r2.end_reason, "force_released")
        self.assertEqual(Seat.objects.get(seat_id="A02").status, "available")

        r3 = self.request("A03")
        sv.toggle_maintenance(self.admin, "A03")  # blocking rejects the waiting request
        r3.refresh_from_db()
        self.assertEqual(r3.status, "rejected")
        self.assertEqual(Seat.objects.get(seat_id="A03").status, "maintenance")

    def test_professor_batch_and_admin_batch(self):
        sv.batch_reserve_for_professor(self.prof, ["A01", "A02"], "CS", 2, 9)
        self.assertEqual(Reservation.objects.filter(status="pending", role="professor").count(), 2)
        sv.batch_reserve_for_professor(self.admin, ["A03"], "x", 2, 9)  # admins need no approval
        self.assertEqual(Seat.objects.get(seat_id="A03").status, "occupied")

    def test_camera_leaves_reservations_alone(self):
        r = self.request()
        sv.accept_reservation(self.admin, r.pk)
        sv.sync_seat_from_camera("A01", True, False)
        r.refresh_from_db()
        self.assertEqual(r.status, "reserved")  # only an Admin verifying the code checks a user in
        sv.sync_seat_from_camera("A02", True, False)
        self.assertEqual(Seat.objects.get(seat_id="A02").status, "occupied")
        sv.sync_seat_from_camera("A02", False, False)
        self.assertEqual(Seat.objects.get(seat_id="A02").status, "available")

    def test_cancel_and_notifications(self):
        r = self.request()
        with self.assertRaises(sv.BookingError):
            sv.cancel_reservation(self.prof, r.pk)  # not theirs
        sv.cancel_reservation(self.student, r.pk)
        r.refresh_from_db()
        self.assertEqual(r.end_reason, "cancelled")
        sv.mark_notifications_read(self.admin)
        self.assertEqual(sv.build_state("admin", self.admin)["notifications"]["unread"], 0)

    def test_date_report_and_endpoints(self):
        r = self.request("A01")
        sv.accept_reservation(self.admin, r.pk)
        sv.verify_confirmation_code(self.admin, r.confirmation_code)
        sv.sync_seat_from_camera("A03", True, False)

        today = timezone.localdate()
        rep = sv.get_date_report(today)
        self.assertEqual(rep["requests"]["total"], 1)
        self.assertEqual(rep["requests"]["accepted"], {"count": 1, "pct": 100.0})
        self.assertEqual(rep["requests"]["verified"]["pct"], 100.0)
        self.assertEqual(rep["occupancy"]["seatsUsed"], 2)
        self.assertEqual(rep["occupancy"]["rate"], 66.7)
        self.assertEqual(rep["occupancy"]["walkin"]["sessions"], 1)
        self.assertEqual(sv.get_date_report(today - timedelta(days=1))["requests"]["total"], 0)
        self.assertEqual(sv.get_date_report(date(2020, 1, 1))["occupancy"]["rate"], 0.0)

        c = Client()
        c.login(username="adm", password="x")
        data = c.get(f"/api/analytics/?start={today}&end={today}").json()
        self.assertTrue(data["ok"])
        self.assertEqual(c.get("/api/analytics/?start=bad").json()["ok"], True)  # falls back to today
        self.assertEqual(c.get("/api/analytics/?start=2024-13-45").status_code, 400)
        csv_resp = c.get(f"/admin-dashboard/report.csv?start={today}")
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn(b"Checked in (code verified)", csv_resp.content)

        s = Client()
        s.login(username="stu", password="x")
        self.assertEqual(s.get("/api/analytics/").status_code, 403)
        self.assertEqual(s.get("/admin-dashboard/report.csv").status_code, 302)

    def test_http_flow(self):
        s, a = Client(), Client()
        s.login(username="stu", password="x")
        a.login(username="adm", password="x")
        post = lambda c, url, body=None: c.post(url, json.dumps(body or {}), content_type="application/json")
        self.assertTrue(post(s, "/api/seats/A01/reserve/", {"duration": 2}).json()["ok"])
        state = a.get("/api/live/").json()["state"]
        rid = state["pendingRequests"][0]["id"]
        self.assertEqual(post(s, f"/api/reservations/{rid}/accept/").status_code, 403)  # students can't accept
        self.assertTrue(post(a, f"/api/reservations/{rid}/accept/").json()["ok"])
        mine = s.get("/api/live/").json()["state"]["myReservations"][0]
        self.assertEqual(mine["status"], "reserved")
        self.assertTrue(mine["code"].startswith("LIB-"))
        self.assertEqual(a.get("/api/live/").json()["state"]["awaiting"][0]["code"], "")
        self.assertTrue(post(a, "/api/reservations/verify/", {"code": mine["code"]}).json()["ok"])
        self.assertEqual(s.get("/api/live/").json()["state"]["notifications"]["unread"], 2)
        post(s, "/api/notifications/read/")
        self.assertEqual(s.get("/api/live/").json()["state"]["notifications"]["unread"], 0)


class PageRenderTests(TestCase):
    def test_dashboards_render(self):
        zone = Zone.objects.create(code="Zone A", title="Q")
        Seat.objects.create(zone=zone, number=1)
        for name, role, url in (("adm", "admin", "/admin-dashboard/"), ("stu", "student", "/student/"),
                                ("prof", "professor", "/professor/")):
            make_user(name, role)
            c = Client()
            c.login(username=name, password="x")
            resp = c.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'id="notif-wrap"')
            self.assertNotContains(resp, "qr-checkin")
        admin = Client()
        admin.login(username="adm", password="x")
        page = admin.get("/admin-dashboard/")
        for marker in ('id="verify-code-input"', 'id="req-pending-list"', 'id="rep-start"'):
            self.assertContains(page, marker)
        self.assertContains(page, "/admin-dashboard/report.csv")
        self.assertEqual(admin.get("/admin-dashboard/logs/?tab=seats").status_code, 200)
