from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from apps.ai_center.models import ModelConfig, PromptTemplate, PromptVersion, SafetyPolicy, UsageLog


@admin.register(ModelConfig)
class ModelConfigAdmin(admin.ModelAdmin):
    """The API-integration registry.

    Layout mirrors how an operator thinks: what is this and is it live, then
    the credentials, then the knobs almost nobody touches.
    """

    list_display = (
        "name",
        "scope",
        "kind",
        "provider",
        "model_name",
        "key_preview",
        "is_default",
        "is_active",
        "health",
    )
    list_display_links = ("name",)
    list_filter = ("provider", "kind", "is_default", "is_active", "tenant")
    search_fields = ("name", "model_name", "provider", "base_url", "external_ref")
    autocomplete_fields = ("fallback",)
    list_select_related = ("tenant",)
    readonly_fields = ("key_preview", "last_verified_at", "last_error", "created_by", "updated_by")
    actions = ("make_default", "verify_selected", "activate_selected", "deactivate_selected")

    fieldsets = (
        (
            None,
            {
                "fields": ("tenant", "kind", "name", "is_default", "is_active"),
                "description": (
                    "One default per hotel per capability — that row is what the AI "
                    "gateway resolves at call time. Leave <b>hotel</b> blank to make "
                    "this configuration platform-wide: every property inherits it "
                    "unless it has a row of its own. That is the right place for a "
                    "key shared across a group, because rotating it is then one edit "
                    "rather than one per hotel."
                ),
            },
        ),
        (
            "Credentials",
            {
                "fields": ("provider", "model_name", "api_key", "key_preview", "base_url"),
                "description": (
                    "Base URL may be left blank for hosted providers; it is filled from "
                    "the provider default on save. Keys are encrypted at rest and never "
                    "shown in full again."
                ),
            },
        ),
        (
            "Advanced",
            {
                "classes": ("collapse",),
                "fields": (
                    "temperature",
                    "max_tokens",
                    "timeout_s",
                    "dimension",
                    "fallback",
                    "cost_per_1k_input_usd",
                    "cost_per_1k_output_usd",
                    "extra",
                ),
                "description": (
                    "Changing dimension or the model on an embedding config invalidates "
                    "every stored vector. Re-embed before activating."
                ),
            },
        ),
        (
            "Provenance",
            {
                "classes": ("collapse",),
                "fields": (
                    "external_ref",
                    "created_by",
                    "updated_by",
                    "last_verified_at",
                    "last_error",
                ),
            },
        ),
    )

    @admin.display(description="Applies to", ordering="tenant__code")
    def scope(self, obj: ModelConfig) -> str:
        """Whose configuration this is.

        Worth a column rather than a detail-page field: somebody editing a
        platform row is changing what every property runs on, and finding that
        out afterwards is the expensive way.
        """
        return obj.tenant.code if obj.tenant_id else "ALL HOTELS"

    @admin.display(description="API key")
    def key_preview(self, obj: ModelConfig) -> str:
        return obj.masked_key

    @admin.display(description="Health")
    def health(self, obj: ModelConfig) -> str:
        if obj.last_error:
            return format_html('<span style="color:#ef4444">✕ {}</span>', obj.last_error[:40])
        if obj.last_verified_at:
            return format_html(
                '<span style="color:#22c55e">✓ {}</span>',
                obj.last_verified_at.strftime("%d %b %H:%M"),
            )
        return format_html('<span style="color:#94a3b8">not tested</span>')

    def save_model(self, request, obj, form, change) -> None:
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

        from services.ai import registry

        # An operator who changes a model expects it to take effect now, not
        # after the 60-second resolution cache expires.
        registry.invalidate(obj.kind, str(obj.tenant_id))

    @admin.action(description="Test connection (makes a real, billable call)")
    def verify_selected(self, request, queryset) -> None:
        from apps.ai_center.services import verify_config

        ok = failed = 0
        for config in queryset:
            success, detail = verify_config(config)
            ok, failed = (ok + 1, failed) if success else (ok, failed + 1)
            if not success:
                self.message_user(request, f"{config.name}: {detail}", level=messages.WARNING)
        self.message_user(request, f"{ok} verified, {failed} failed.")

    @admin.action(description="Make default for its capability (and activate)")
    def make_default(self, request, queryset) -> None:
        """Promote rows one at a time so each demotes its predecessor.

        A bulk ``.update()`` here would set several rows default for the same
        capability at once and trip the partial unique index.
        """
        promoted: list[str] = []
        seen: set[tuple] = set()

        for config in queryset:
            slot = (config.tenant_id, config.kind)
            if slot in seen:
                self.message_user(
                    request,
                    f"Skipped {config.name}: another selected row is already becoming the "
                    f"default for {config.get_kind_display()}.",
                    level=messages.WARNING,
                )
                continue
            seen.add(slot)
            config.is_default = True
            config.is_active = True
            config.updated_by = request.user
            config.save()
            promoted.append(config.name)

        if promoted:
            self.message_user(request, f"Now default: {', '.join(promoted)}.")

    @admin.action(description="Activate selected")
    def activate_selected(self, request, queryset) -> None:
        self.message_user(request, f"{queryset.update(is_active=True)} activated.")

    @admin.action(description="Deactivate selected")
    def deactivate_selected(self, request, queryset) -> None:
        self.message_user(request, f"{queryset.update(is_active=False)} deactivated.")


class PromptVersionInline(admin.TabularInline):
    model = PromptVersion
    extra = 0
    fields = ("version", "tenant", "is_active", "eval_score", "notes", "created_by")
    readonly_fields = ("created_by",)
    ordering = ("-version",)


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "active_version_label")
    search_fields = ("key", "name")
    inlines = (PromptVersionInline,)

    @admin.display(description="Active version")
    def active_version_label(self, obj: PromptTemplate) -> str:
        version = obj.active_version
        return f"v{version.version}" if version else "—"


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    list_display = ("template", "version", "tenant", "is_active", "eval_score", "created_at")
    list_filter = ("is_active", "template", "tenant")
    search_fields = ("template__key", "system_prompt", "notes")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UsageLog)
class UsageLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "tenant",
        "module",
        "kind",
        "model_name",
        "latency_badge",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "success",
    )
    list_filter = ("kind", "module", "success", "fallback_used", "tenant")
    search_fields = ("model_name", "request_id", "conversation_id", "error_code")
    date_hierarchy = "created_at"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description="Latency", ordering="latency_ms")
    def latency_badge(self, obj: UsageLog) -> str:
        # goal.txt §6 target: first token < 1500ms.
        colour = (
            "#22c55e"
            if obj.latency_ms < 1500
            else "#f59e0b" if obj.latency_ms < 3000 else "#ef4444"
        )
        return format_html('<span style="color:{}">{} ms</span>', colour, obj.latency_ms)


@admin.register(SafetyPolicy)
class SafetyPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "confidence_threshold",
        "max_conversation_turns",
        "daily_cost_cap_usd",
        "allow_financial_actions",
    )
    list_filter = ("allow_financial_actions",)
