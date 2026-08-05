from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from apps.reception.models import Conversation, Handoff, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    can_delete = False
    fields = ("created_at", "role", "content", "confidence", "latency_ms", "citations")
    readonly_fields = fields
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "guest_name",
        "tenant",
        "channel",
        "status_badge",
        "turn_count",
        "total_tokens",
        "total_cost_usd",
    )
    list_filter = ("status", "channel", "tenant", "handoff_reason", "started_at")
    search_fields = ("guest_name", "session_key", "messages__content")
    date_hierarchy = "started_at"
    inlines = (MessageInline,)
    readonly_fields = ("total_tokens", "total_cost_usd", "turn_count", "started_at", "ended_at")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj: Conversation) -> str:
        colour = {"handoff": "#f59e0b", "resolved": "#22c55e", "abandoned": "#94a3b8"}.get(
            obj.status, "#38bdf8"
        )
        return format_html('<b style="color:{}">{}</b>', colour, obj.get_status_display())


@admin.register(Handoff)
class HandoffAdmin(admin.ModelAdmin):
    list_display = ("created_at", "conversation", "reason", "claimed_by", "waiting", "resolved_at")
    list_filter = ("reason", "tenant", "resolved_at")
    search_fields = ("detail", "conversation__guest_name")
    autocomplete_fields = ("claimed_by",)

    @admin.display(description="Waited")
    def waiting(self, obj: Handoff) -> str:
        return f"{obj.waiting_seconds}s"


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    """Read-only: a transcript that can be edited is not evidence."""

    list_display = ("created_at", "conversation", "role", "short", "confidence", "latency_ms")
    list_filter = ("role", "tenant", "was_spoken")
    search_fields = ("content",)
    date_hierarchy = "created_at"

    @admin.display(description="Content")
    def short(self, obj: Message) -> str:
        return obj.content[:80]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
