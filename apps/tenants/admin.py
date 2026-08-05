from __future__ import annotations

from django.contrib import admin

from apps.tenants.models import Hotel, HotelMembership


class HotelMembershipInline(admin.TabularInline):
    model = HotelMembership
    extra = 0
    autocomplete_fields = ("user", "role")


@admin.register(Hotel)
class HotelAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "city", "plan", "total_rooms", "ai_available", "is_active")
    list_filter = ("plan", "is_active", "ai_enabled", "biometric_enabled", "country")
    search_fields = ("code", "name", "legal_name", "email")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (HotelMembershipInline,)
    fieldsets = (
        (None, {"fields": ("code", "name", "legal_name", "slug", "is_active", "plan")}),
        (
            "Location",
            {
                "fields": (
                    "address_line1",
                    "address_line2",
                    "city",
                    "state",
                    "postal_code",
                    "country",
                    ("latitude", "longitude"),
                )
            },
        ),
        ("Contact", {"fields": ("phone", "email", "website")}),
        (
            "Operations",
            {
                "fields": (
                    "timezone",
                    "currency",
                    "star_rating",
                    "total_rooms",
                    ("check_in_time", "check_out_time"),
                )
            },
        ),
        (
            "Finance",
            {"fields": ("tax_rate", "service_charge_rate", "tax_registration_no")},
        ),
        (
            "Payment",
            {
                "description": (
                    "What guests are told about paying — on the booking page, on the "
                    "printed slip, and by the assistant when they ask. Nothing here "
                    "charges anybody: no surface in this product takes money "
                    "(goal.txt D11). An advance is only shown to guests when both the "
                    "wallet and its number are filled in, because an advance nobody "
                    "can send reads as a scam."
                ),
                "fields": (
                    "payment_timing",
                    ("accepts_cash", "accepts_card"),
                    ("accepts_bkash", "accepts_nagad"),
                    ("advance_wallet", "advance_wallet_number"),
                    "payment_note",
                ),
            },
        ),
        ("Branding", {"fields": ("logo", "accent_color")}),
        (
            "AI posture",
            {
                "description": (
                    "Biometrics stay disabled until legal sign-off is recorded "
                    "(goal.txt R1). The kill switch takes effect immediately."
                ),
                "fields": (
                    "ai_enabled",
                    "ai_kill_switch",
                    "biometric_enabled",
                    "ai_daily_cost_cap_usd",
                ),
            },
        ),
        (
            "Lobby kiosk",
            {
                "description": (
                    "Presence detection is NOT face recognition. It answers "
                    "“is somebody standing here” in the browser so the greeting "
                    "starts on approach — no identification, nothing uploaded, "
                    "nothing stored. Identifying a guest needs "
                    "“face recognition enabled” above, plus consent."
                ),
                "fields": (
                    "kiosk_language",
                    "kiosk_greeting_style",
                    "kiosk_presence_detection",
                    "kiosk_capture_photo",
                ),
            },
        ),
    )

    @admin.display(boolean=True, description="AI live")
    def ai_available(self, obj: Hotel) -> bool:
        return obj.ai_available


@admin.register(HotelMembership)
class HotelMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "hotel", "role", "is_default")
    list_filter = ("hotel", "role", "is_default")
    search_fields = ("user__email", "hotel__name")
    autocomplete_fields = ("user", "hotel", "role")
