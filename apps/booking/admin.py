from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from apps.booking.models import Reservation, ReservationRoom, ReservationStatus, StayEvent
from apps.core.exceptions import ASHOSError
from services.booking import reservations as booking

STATUS_COLOURS = {
    ReservationStatus.PENDING: "#38bdf8",
    ReservationStatus.CONFIRMED: "#6366f1",
    ReservationStatus.CHECKED_IN: "#22c55e",
    ReservationStatus.CHECKED_OUT: "#94a3b8",
    ReservationStatus.CANCELLED: "#ef4444",
    ReservationStatus.NO_SHOW: "#f59e0b",
}


class ReservationRoomInline(admin.TabularInline):
    model = ReservationRoom
    extra = 0
    fields = (
        "room_type",
        "room",
        "stay",
        "adults",
        "children",
        "rate_snapshot",
        "blocks_inventory",
    )
    readonly_fields = ("stay", "blocks_inventory")


class StayEventInline(admin.TabularInline):
    model = StayEvent
    extra = 0
    fields = ("occurred_at", "kind", "room", "detail", "performed_by")
    readonly_fields = fields
    ordering = ("-occurred_at",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "guest",
        "tenant",
        "check_in",
        "check_out",
        "nights",
        "status_badge",
        "grand_total",
        "balance",
    )
    list_filter = ("tenant", "status", "source", "check_in")
    search_fields = ("code", "guest__first_name", "guest__last_name", "guest__phone")
    date_hierarchy = "check_in"
    autocomplete_fields = ("guest",)
    inlines = (ReservationRoomInline, StayEventInline)
    readonly_fields = (
        "code",
        "room_total",
        "tax_total",
        "service_total",
        "grand_total",
        "checked_in_at",
        "checked_out_at",
        "cancelled_at",
    )
    actions = ("do_check_in", "do_check_out", "do_cancel", "do_no_show")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj: Reservation) -> str:
        return format_html(
            '<b style="color:{}">{}</b>',
            STATUS_COLOURS.get(obj.status, "#94a3b8"),
            obj.get_status_display(),
        )

    @admin.display(description="Balance")
    def balance(self, obj: Reservation):
        return obj.balance_due

    def _run(self, request, queryset, action, label):
        """Run a lifecycle action row by row, reporting each refusal.

        A bulk action that swallows errors is how a receptionist ends up
        believing five guests were checked in when only three were.
        """
        done = 0
        total = queryset.count()
        for reservation in queryset:
            try:
                action(reservation, user=request.user)
                done += 1
            except ASHOSError as exc:
                self.message_user(
                    request, f"{reservation.code}: {exc.detail}", level=messages.WARNING
                )
        self.message_user(request, f"{label}: {done} of {total}.")

    @admin.action(description="Check in")
    def do_check_in(self, request, queryset):
        self._run(request, queryset, booking.check_in, "Checked in")

    @admin.action(description="Check out (settles folio, issues invoice)")
    def do_check_out(self, request, queryset):
        self._run(request, queryset, booking.check_out, "Checked out")

    @admin.action(description="Cancel")
    def do_cancel(self, request, queryset):
        self._run(
            request,
            queryset,
            lambda r, user: booking.cancel(r, reason="cancelled from admin", user=user),
            "Cancelled",
        )

    @admin.action(description="Mark no-show")
    def do_no_show(self, request, queryset):
        self._run(request, queryset, booking.mark_no_show, "Marked no-show")


@admin.register(ReservationRoom)
class ReservationRoomAdmin(admin.ModelAdmin):
    list_display = ("reservation", "room_type", "room", "stay", "blocks_inventory")
    list_filter = ("tenant", "blocks_inventory", "room_type")
    search_fields = ("reservation__code", "room__number")


@admin.register(StayEvent)
class StayEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "reservation", "kind", "room", "performed_by", "detail")
    list_filter = ("kind", "tenant")
    search_fields = ("reservation__code",)
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request) -> bool:
        return False
