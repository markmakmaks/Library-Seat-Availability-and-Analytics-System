from django.urls import path

from . import views

app_name = "seats"

urlpatterns = [
    path("", views.index, name="index"),
    path("api/state/", views.api_state, name="state"),
    path("api/role/", views.set_role, name="set-role"),
    path("api/seats/batch-reserve/", views.batch_reserve, name="batch-reserve"),
    path("api/seats/<str:seat_id>/reserve/", views.reserve_seat, name="reserve"),
    path("api/seats/<str:seat_id>/release/", views.release_seat, name="release"),
    path("api/seats/<str:seat_id>/maintenance/", views.toggle_maintenance, name="maintenance"),
    path("api/seats/<str:seat_id>/check-in/", views.check_in, name="check-in"),
]
