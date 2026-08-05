from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from apps.billing.models import BusinessDate, Folio, FolioLine, Invoice, Payment
from apps.core.exceptions import ASHOSError


class FolioLineInline(admin.TabularInline):
    model = FolioLine
    extra = 0
    fields = (
        "business_date",
        "charge_type",
        "description",
        "quantity",
        "amount",
        "is_voided",
        "posted_by",
    )
    readonly_fields = fields
    ordering = ("business_date", "created_at")

    def has_add_permission(self, request, obj=None) -> bool:
        # Charges are posted through services.billing so totals and audit stay
        # correct. A hand-typed line here would bypass both.
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("received_at", "method", "status", "amount", "reference", "received_by")
    readonly_fields = ("received_at",)


@admin.register(Folio)
class FolioAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "guest",
        "tenant",
        "status",
        "charges_total",
        "payments_total",
        "balance_badge",
    )
    list_filter = ("tenant", "status")
    search_fields = ("number", "guest__first_name", "guest__last_name", "reservation__code")
    readonly_fields = ("charges_total", "payments_total", "balance", "opened_at", "settled_at")
    inlines = (FolioLineInline, PaymentInline)
    actions = ("recalculate_totals", "issue_invoice_action")

    @admin.display(description="Balance", ordering="balance")
    def balance_badge(self, obj: Folio) -> str:
        colour = "#ef4444" if obj.balance > 0 else "#22c55e"
        return format_html('<b style="color:{}">{}</b>', colour, obj.balance)

    @admin.action(description="Recalculate totals from lines")
    def recalculate_totals(self, request, queryset) -> None:
        for folio in queryset:
            folio.recalculate()
        self.message_user(request, f"{queryset.count()} folio(s) recalculated.")

    @admin.action(description="Issue invoice")
    def issue_invoice_action(self, request, queryset) -> None:
        from services.billing import folio as billing

        for folio in queryset:
            try:
                invoice = billing.issue_invoice(folio, user=request.user)
                self.message_user(request, f"{folio.number} to invoice {invoice.number}")
            except ASHOSError as exc:
                self.message_user(request, f"{folio.number}: {exc.detail}", level=messages.WARNING)


@admin.register(FolioLine)
class FolioLineAdmin(admin.ModelAdmin):
    list_display = (
        "business_date",
        "folio",
        "charge_type",
        "description",
        "amount",
        "is_voided",
        "ai_suggested",
    )
    list_filter = ("charge_type", "is_voided", "ai_suggested", "tenant")
    search_fields = ("description", "folio__number")
    date_hierarchy = "business_date"

    def has_change_permission(self, request, obj=None) -> bool:
        # Posted lines are immutable. Corrections are reversing lines.
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "guest", "tenant", "issued_at", "total", "amount_paid", "is_paid")
    list_filter = ("tenant", "is_paid")
    search_fields = ("number", "guest__first_name", "guest__last_name")
    date_hierarchy = "issued_at"
    readonly_fields = (
        "number",
        "snapshot",
        "subtotal",
        "tax_amount",
        "service_amount",
        "total",
        "amount_paid",
    )

    def has_change_permission(self, request, obj=None) -> bool:
        # An invoice is a frozen document. Editing one is not a correction.
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "received_at",
        "folio",
        "method",
        "status",
        "amount",
        "reference",
        "received_by",
    )
    list_filter = ("method", "status", "tenant")
    search_fields = ("reference", "provider_ref", "folio__number")
    date_hierarchy = "received_at"


@admin.register(BusinessDate)
class BusinessDateAdmin(admin.ModelAdmin):
    list_display = ("tenant", "current_date", "last_audit_at", "rooms_charged", "revenue_posted")
    actions = ("run_audit",)

    @admin.action(description="Run night audit now")
    def run_audit(self, request, queryset) -> None:
        from services.billing import folio as billing

        for row in queryset:
            result = billing.run_night_audit(row.tenant, user=request.user)
            self.message_user(
                request,
                f"{row.tenant}: {result['rooms_charged']} room-night(s), "
                f"{result['revenue_posted']} posted for {result['business_date']}. "
                f"Business date is now {result['next_business_date']}.",
            )
