from django.contrib import admin

from .models import Reservation, Seat, Zone


@admin.register(Zone)
class ZoneAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "description")


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ("seat_id", "zone", "number", "features", "is_maintenance")
    list_filter = ("zone", "is_maintenance")
    search_fields = ("seat_id",)
    readonly_fields = ("seat_id",)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("seat", "user_name", "role", "status", "start_hour", "end_hour", "created_at")
    list_filter = ("status", "role")
    search_fields = ("user_name", "seat__seat_id", "title")
