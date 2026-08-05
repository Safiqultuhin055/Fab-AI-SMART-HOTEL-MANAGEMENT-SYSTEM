from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from apps.rooms.models import (
    Amenity,
    RatePlan,
    Room,
    RoomRate,
    RoomStatus,
    RoomStatusLog,
    RoomType,
    RoomTypePhoto,
)

STATUS_COLOURS = {
    RoomStatus.VACANT_CLEAN: "#22c55e",
    RoomStatus.VACANT_DIRTY: "#f59e0b",
    RoomStatus.OCCUPIED: "#6366f1",
    RoomStatus.OUT_OF_ORDER: "#ef4444",
    RoomStatus.OUT_OF_SERVICE: "#94a3b8",
}


@admin.register(Amenity)
class AmenityAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "is_active")
    list_filter = ("tenant", "is_active")
    search_fields = ("name",)


class RoomRateInline(admin.TabularInline):
    model = RoomRate
    extra = 0
    fields = ("rate_plan", "valid_from", "valid_to", "price", "priority", "weekdays", "label")


class RoomTypePhotoInline(admin.TabularInline):
    model = RoomTypePhoto
    extra = 1
    fields = ("preview", "image", "caption", "sort_order")
    readonly_fields = ("preview",)

    @admin.display(description="")
    def preview(self, obj: RoomTypePhoto) -> str:
        if not obj.pk or not obj.image:
            return "—"
        return format_html(
            '<img src="{}" style="height:56px;border-radius:6px;object-fit:cover">', obj.image.url
        )


@admin.register(RoomType)
class RoomTypeAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "tenant",
        "base_occupancy",
        "max_occupancy",
        "base_rate",
        "room_count",
        "is_active",
    )
    list_filter = ("tenant", "is_active", "bed_type")
    search_fields = ("name", "code", "view")
    filter_horizontal = ("amenities",)
    inlines = (RoomTypePhotoInline, RoomRateInline)

    @admin.display(description="Rooms")
    def room_count(self, obj: RoomType) -> int:
        return obj.rooms.count()


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("number", "room_type", "floor", "status_badge", "tenant", "is_active")
    list_filter = ("tenant", "status", "room_type", "floor", "is_active")
    search_fields = ("number", "notes")
    list_select_related = ("room_type", "tenant")
    actions = ("mark_clean", "mark_dirty", "mark_out_of_order")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj: Room) -> str:
        return format_html(
            '<b style="color:{}">{}</b>',
            STATUS_COLOURS.get(obj.status, "#94a3b8"),
            obj.get_status_display(),
        )

    def _bulk_status(self, request, queryset, status):
        # One at a time so every transition lands in RoomStatusLog; a bulk
        # update would change the board and leave no trail.
        for room in queryset:
            room.set_status(status, note="changed from admin", user=request.user)
        self.message_user(request, f"{queryset.count()} room(s) set to {status}.")

    @admin.action(description="Mark vacant clean")
    def mark_clean(self, request, queryset):
        self._bulk_status(request, queryset, RoomStatus.VACANT_CLEAN)

    @admin.action(description="Mark vacant dirty")
    def mark_dirty(self, request, queryset):
        self._bulk_status(request, queryset, RoomStatus.VACANT_DIRTY)

    @admin.action(description="Mark out of order")
    def mark_out_of_order(self, request, queryset):
        self._bulk_status(request, queryset, RoomStatus.OUT_OF_ORDER)


@admin.register(RoomStatusLog)
class RoomStatusLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "room", "from_status", "to_status", "changed_by", "note")
    list_filter = ("to_status", "tenant")
    search_fields = ("room__number", "note")
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(RatePlan)
class RatePlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "tenant",
        "discount_percent",
        "includes_breakfast",
        "is_default",
        "is_active",
    )
    list_filter = ("tenant", "is_default", "is_active", "includes_breakfast")
    search_fields = ("name", "code")


@admin.register(RoomRate)
class RoomRateAdmin(admin.ModelAdmin):
    list_display = (
        "room_type",
        "rate_plan",
        "valid_from",
        "valid_to",
        "price",
        "priority",
        "label",
    )
    list_filter = ("tenant", "rate_plan", "room_type")
    date_hierarchy = "valid_from"
