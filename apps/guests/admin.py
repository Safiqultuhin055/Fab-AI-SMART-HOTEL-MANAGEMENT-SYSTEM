from __future__ import annotations

from django.contrib import admin, messages

from apps.guests.models import Guest, GuestConsent, GuestDocument


class GuestDocumentInline(admin.TabularInline):
    model = GuestDocument
    extra = 0
    fields = (
        "doc_type",
        "masked_number",
        "issuing_country",
        "expiry_date",
        "mrz_valid",
        "verified_at",
    )
    readonly_fields = ("masked_number", "mrz_valid", "verified_at")

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class GuestConsentInline(admin.TabularInline):
    model = GuestConsent
    extra = 0
    fields = ("purpose", "granted", "granted_at", "withdrawn_at", "method", "recorded_by")
    readonly_fields = ("granted_at", "recorded_by")


@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "tenant",
        "phone",
        "email",
        "tier",
        "total_stays",
        "total_spend",
        "last_stay_at",
    )
    list_filter = ("tenant", "tier", "is_blacklisted", "nationality")
    search_fields = ("first_name", "last_name", "email", "phone")
    inlines = (GuestDocumentInline, GuestConsentInline)
    readonly_fields = ("total_stays", "total_spend", "last_stay_at")
    actions = ("erase_personal_data",)
    fieldsets = (
        (
            None,
            {"fields": ("tenant", "title", "first_name", "last_name", "tier", "is_blacklisted")},
        ),
        ("Contact", {"fields": ("email", "phone", "address", "city", "country", "nationality")}),
        ("Personal", {"fields": ("date_of_birth", "gender", "language")}),
        (
            "Stay profile",
            {"fields": ("preferences", "notes", "total_stays", "total_spend", "last_stay_at")},
        ),
    )

    @admin.action(description="Erase personal data (right to erasure)")
    def erase_personal_data(self, request, queryset) -> None:
        # Irreversible and legally meaningful, so it reports exactly what went.
        for guest in queryset:
            removed = guest.forget()
            self.message_user(
                request,
                f"Erased {guest.pk}: {removed['documents']} document(s), "
                f"{removed['faces']} biometric record(s). Financial history retained.",
                level=messages.WARNING,
            )


@admin.register(GuestDocument)
class GuestDocumentAdmin(admin.ModelAdmin):
    list_display = ("guest", "doc_type", "masked_number", "expiry_date", "mrz_valid", "verified_at")
    list_filter = ("doc_type", "tenant", "mrz_valid")
    search_fields = ("guest__first_name", "guest__last_name", "holder_name")
    readonly_fields = ("masked_number",)


@admin.register(GuestConsent)
class GuestConsentAdmin(admin.ModelAdmin):
    list_display = ("guest", "purpose", "granted", "granted_at", "withdrawn_at", "method")
    list_filter = ("purpose", "granted", "tenant")
    search_fields = ("guest__first_name", "guest__last_name")
