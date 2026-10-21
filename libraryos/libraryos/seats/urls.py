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
    path("api/state/", views.api_state, name="state"),
    path("api/seats/batch-reserve/", views.batch_reserve, name="batch-reserve"),
    path("api/seats/<str:seat_id>/reserve/", views.reserve_seat, name="reserve"),
    path("api/seats/<str:seat_id>/release/", views.release_seat, name="release"),
    path("api/seats/<str:seat_id>/maintenance/", views.toggle_maintenance, name="maintenance"),
    path("api/seats/<str:seat_id>/check-in/", views.check_in, name="check-in"),
]
