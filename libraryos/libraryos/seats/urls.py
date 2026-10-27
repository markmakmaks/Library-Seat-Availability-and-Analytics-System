from django.urls import path

from . import views

app_name = "seats"

urlpatterns = [
    path("", views.home, name="index"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("signup/", views.signup_view, name="signup"),
    path("student/", views.dashboard, {"page_role": "student"}, name="student-dashboard"),
    path("professor/", views.dashboard, {"page_role": "professor"}, name="professor-dashboard"),
    path("admin-dashboard/", views.dashboard, {"page_role": "admin"}, name="admin-dashboard"),
    path("admin-dashboard/approvals/", views.approvals, name="approvals"),
    path("admin-dashboard/users/", views.users_list, name="users"),
    path("admin-dashboard/analytics.csv", views.analytics_csv, name="analytics-csv"),
    path("admin-dashboard/report.csv", views.date_report_csv, name="date-report-csv"),
    path("admin-dashboard/logs/", views.activity_logs, name="logs"),
    path("api/state/", views.api_state, name="state"),
    path("api/live/", views.api_live, name="live"),
    path("api/analytics/", views.api_analytics, name="analytics"),
    path("api/notifications/read/", views.notifications_read, name="notifications-read"),
    path("api/reservations/verify/", views.verify_code, name="verify"),
    path("api/reservations/<str:reservation_id>/accept/", views.accept_reservation, name="accept"),
    path("api/reservations/<str:reservation_id>/reject/", views.reject_reservation, name="reject"),
    path("api/reservations/<str:reservation_id>/cancel/", views.cancel_reservation, name="cancel"),
    path("api/seats/batch-reserve/", views.batch_reserve, name="batch-reserve"),
    path("api/seats/<str:seat_id>/reserve/", views.reserve_seat, name="reserve"),
    path("api/seats/<str:seat_id>/release/", views.release_seat, name="release"),
    path("api/seats/<str:seat_id>/maintenance/", views.toggle_maintenance, name="maintenance"),
]
