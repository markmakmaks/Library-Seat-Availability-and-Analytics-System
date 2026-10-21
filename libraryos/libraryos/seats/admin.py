from django.contrib import admin

from .models import Profile, Reservation, Seat, Zone


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "role", "status", "id_number", "created_at")
    list_filter = ("role", "status")
    search_fields = ("user__username", "last_name", "first_name", "id_number")
    actions = ["approve_accounts", "reject_accounts"]

    @admin.action(description="Approve selected accounts")
    def approve_accounts(self, request, queryset):
        self._review(queryset, approved=True)

    @admin.action(description="Reject selected accounts")
    def reject_accounts(self, request, queryset):
        self._review(queryset, approved=False)

    def _review(self, queryset, approved):
        for profile in queryset.select_related("user"):
            profile.status = Profile.Status.APPROVED if approved else Profile.Status.REJECTED
            profile.save(update_fields=["status"])
            profile.user.is_active = approved
            profile.user.save(update_fields=["is_active"])


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
