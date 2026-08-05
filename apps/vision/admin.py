"""Vision admin.

Read-only by design. Face captures are created by the kiosk after recorded
consent and destroyed by the retention purge; there is no legitimate reason for
somebody to hand-edit one, and an editable biometric row is an unaccountable one.

The list view shows metadata only — never the image. A staff list that renders
thumbnails of every guest's face turns "the hotel holds photographs" into "the
hotel displays photographs", which is a different and worse thing.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils import timezone

from apps.vision.models import GuestFace


@admin.register(GuestFace)
class GuestFaceAdmin(admin.ModelAdmin):
    list_display = (
        "guest",
        "sequence",
        "pose_hint",
        "source",
        "has_image",
        "captured_at",
        "expires_at",
        "is_expired",
    )
    list_filter = ("tenant", "source", "captured_at")
    search_fields = ("guest__first_name", "guest__last_name", "reservation__code")
    readonly_fields = (
        "guest",
        "reservation",
        "consent",
        "sequence",
        "pose_hint",
        "content_type",
        "byte_size",
        "width",
        "height",
        "source",
        "captured_at",
        "expires_at",
    )
    exclude = ("image",)  # the whole point
    date_hierarchy = "captured_at"
    actions = ("purge_now",)

    def has_add_permission(self, request) -> bool:  # noqa: ARG002
        return False

    def has_change_permission(self, request, obj=None) -> bool:  # noqa: ARG002
        return False

    @admin.display(boolean=True, description="Image held")
    def has_image(self, obj: GuestFace) -> bool:
        return obj.has_image

    @admin.display(boolean=True, description="Expired")
    def is_expired(self, obj: GuestFace) -> bool:
        return obj.is_expired

    @admin.action(description="Delete these captures now (irreversible)")
    def purge_now(self, request, queryset) -> None:
        """Early deletion, ahead of the retention window.

        Hard delete, not soft: a soft-deleted biometric row is still stored
        biometric data, and the hotel would remain in breach of the promise it
        made on the consent screen.
        """
        count = queryset.count()
        queryset.hard_delete()
        self.message_user(
            request,
            f"Hard-deleted {count} face capture(s) at {timezone.now():%H:%M}.",
            level=messages.WARNING,
        )
