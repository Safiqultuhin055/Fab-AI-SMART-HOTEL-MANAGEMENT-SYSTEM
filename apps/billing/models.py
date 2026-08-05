"""Folio, invoice and payment.

Folio-based billing: one running account per stay that every module posts to —
room charges from the night audit, food from the restaurant, laundry from
housekeeping. Checkout settles the folio and freezes it into an invoice.

Three rules the code enforces rather than merely documents:

*Lines are immutable.* A posted charge is voided by a reversing line, never
edited. An editable charge history is not an audit trail.

*Money is Decimal, always.* Quantised through ``apps.core.utils.money``.

*The AI never moves money* (goal.txt D11). It can propose a discount or a late
checkout; a human posts it, and the row records who.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel
from apps.core.utils import money, short_code


class FolioStatus(models.TextChoices):
    OPEN = "open", _("Open")
    SETTLED = "settled", _("Settled")
    VOID = "void", _("Void")


class ChargeType(models.TextChoices):
    ROOM = "room", _("Room charge")
    TAX = "tax", _("Tax / VAT")
    SERVICE = "service", _("Service charge")
    RESTAURANT = "restaurant", _("Restaurant")
    MINIBAR = "minibar", _("Minibar")
    LAUNDRY = "laundry", _("Laundry")
    SPA = "spa", _("Spa")
    TRANSPORT = "transport", _("Transport")
    LATE_CHECKOUT = "late_checkout", _("Late checkout")
    DAMAGE = "damage", _("Damage / loss")
    DISCOUNT = "discount", _("Discount")
    ADJUSTMENT = "adjustment", _("Adjustment")
    OTHER = "other", _("Other")


class PaymentMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    CARD = "card", _("Card (taken at desk)")
    BKASH = "bkash", _("bKash")
    NAGAD = "nagad", _("Nagad")
    BANK_TRANSFER = "bank_transfer", _("Bank transfer")
    CORPORATE = "corporate", _("Corporate account")
    ONLINE = "online", _("Online gateway")


class PaymentStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CAPTURED = "captured", _("Captured")
    FAILED = "failed", _("Failed")
    REFUNDED = "refunded", _("Refunded")
    VOID = "void", _("Void")


class Folio(TenantOwnedModel):
    """The running account for one stay."""

    number = models.CharField(max_length=16, db_index=True)
    reservation = models.OneToOneField(
        "booking.Reservation", on_delete=models.PROTECT, related_name="folio"
    )
    guest = models.ForeignKey("guests.Guest", on_delete=models.PROTECT, related_name="folios")

    status = models.CharField(
        max_length=12, choices=FolioStatus.choices, default=FolioStatus.OPEN, db_index=True
    )
    currency = models.CharField(max_length=3, default="BDT")

    # Maintained by ``recalculate()`` rather than summed on every render: the
    # folio appears on the dashboard, the checkout screen and the kiosk.
    charges_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payments_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    opened_at = models.DateTimeField(default=timezone.now)
    settled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _("folio")
        verbose_name_plural = _("folios")
        ordering = ("-opened_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "number"],
                condition=models.Q(is_deleted=False),
                name="uniq_folio_number_per_hotel",
            )
        ]
        indexes = [models.Index(fields=["tenant", "status"])]

    def __str__(self) -> str:
        return f"Folio {self.number} · {self.guest}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = f"F{short_code(7)}"
        return super().save(*args, **kwargs)

    def recalculate(self) -> None:
        """Re-derive the totals from the immutable lines.

        Cheap, and the only safe way to keep denormalised money honest: never
        increment a running total, always re-sum the source of truth.

        **Every** line counts, including ones marked voided. A void is a
        reversing line of the opposite sign, so the pair nets to zero on its
        own. Excluding the original as well would subtract the charge twice and
        leave the folio in credit — which is exactly what happened before a test
        caught it.
        """
        charges = self.lines.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
        payments = self.payments.filter(status=PaymentStatus.CAPTURED).aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0")

        self.charges_total = money(charges)
        self.payments_total = money(payments)
        self.balance = money(charges - payments)
        self.save(update_fields=["charges_total", "payments_total", "balance", "updated_at"])

    @property
    def is_settled(self) -> bool:
        return self.balance <= Decimal("0.00")


class FolioLine(TenantOwnedModel):
    """One posted charge. Immutable once written."""

    folio = models.ForeignKey(Folio, on_delete=models.CASCADE, related_name="lines")
    charge_type = models.CharField(max_length=20, choices=ChargeType.choices, db_index=True)
    description = models.CharField(max_length=200)

    business_date = models.DateField(
        db_index=True, help_text=_("Hotel business date, not the wall clock. Set by night audit.")
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, help_text=_("Negative for discounts and reversals.")
    )

    # Where the charge came from, without a foreign key to every module.
    source_module = models.CharField(max_length=30, blank=True)
    source_ref = models.CharField(max_length=64, blank=True)

    is_voided = models.BooleanField(default=False, db_index=True)
    voided_by_line = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="voids"
    )
    posted_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    ai_suggested = models.BooleanField(
        default=False,
        help_text=_("The AI proposed this line; a human posted it (goal.txt D11)."),
    )

    class Meta:
        verbose_name = _("folio line")
        verbose_name_plural = _("folio lines")
        ordering = ("business_date", "created_at")
        indexes = [
            models.Index(fields=["folio", "business_date"]),
            models.Index(fields=["charge_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.description}: {self.amount}"


class Invoice(TenantOwnedModel):
    """A frozen, numbered statement of a folio at settlement.

    Separate from the folio because an invoice must never change after issue,
    while a folio keeps moving until checkout.
    """

    number = models.CharField(max_length=20, db_index=True)
    folio = models.ForeignKey(Folio, on_delete=models.PROTECT, related_name="invoices")
    guest = models.ForeignKey("guests.Guest", on_delete=models.PROTECT, related_name="invoices")

    issued_at = models.DateTimeField(default=timezone.now)
    currency = models.CharField(max_length=3, default="BDT")

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    service_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    # A frozen copy of the lines and the hotel's tax details as they were at
    # issue. Re-rendering from live data would change a historical document
    # whenever the VAT rate or the hotel's address is edited.
    snapshot = models.JSONField(default=dict, blank=True)

    bill_to_name = models.CharField(max_length=150, blank=True)
    bill_to_address = models.CharField(max_length=255, blank=True)
    tax_registration_no = models.CharField(max_length=50, blank=True)

    is_paid = models.BooleanField(default=False, db_index=True)
    issued_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = _("invoice")
        verbose_name_plural = _("invoices")
        ordering = ("-issued_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "number"],
                condition=models.Q(is_deleted=False),
                name="uniq_invoice_number_per_hotel",
            )
        ]

    def __str__(self) -> str:
        return f"Invoice {self.number}"

    @property
    def balance(self) -> Decimal:
        return money(self.total - self.amount_paid)


class Payment(TenantOwnedModel):
    """Money received. Cash and desk-taken card today; a gateway later.

    ``provider`` and ``provider_ref`` exist now so that adding bKash, Nagad or
    SSLCommerz is an adapter plus a row, not a schema migration on a table that
    already holds a year of live payments.
    """

    folio = models.ForeignKey(Folio, on_delete=models.PROTECT, related_name="payments")
    invoice = models.ForeignKey(
        Invoice, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments"
    )

    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    status = models.CharField(
        max_length=12, choices=PaymentStatus.choices, default=PaymentStatus.CAPTURED, db_index=True
    )
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    currency = models.CharField(max_length=3, default="BDT")
    received_at = models.DateTimeField(default=timezone.now)

    reference = models.CharField(
        max_length=100, blank=True, help_text=_("Card last 4, wallet trx id, or slip number.")
    )
    provider = models.CharField(max_length=30, blank=True)
    provider_ref = models.CharField(max_length=120, blank=True, db_index=True)

    # goal.txt D16 — a retried checkout must not take the money twice.
    idempotency_key = models.CharField(max_length=64, blank=True, db_index=True)

    received_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                condition=models.Q(is_deleted=False) & ~models.Q(idempotency_key=""),
                name="uniq_payment_idempotency_key",
            )
        ]
        indexes = [models.Index(fields=["folio", "status"])]

    def __str__(self) -> str:
        return f"{self.get_method_display()} {self.amount} {self.currency}"


class BusinessDate(TenantOwnedModel):
    """The hotel's own date, rolled by the night audit.

    A hotel day does not end at midnight. Charges posted at 02:00 belong to the
    previous business day, and revenue reports that ignore this never match the
    front desk's numbers.
    """

    current_date = models.DateField()
    last_audit_at = models.DateTimeField(null=True, blank=True)
    last_audit_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    rooms_charged = models.PositiveIntegerField(default=0)
    revenue_posted = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("business date")
        verbose_name_plural = _("business dates")
        ordering = ("-current_date",)
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_deleted=False),
                name="uniq_business_date_per_hotel",
            )
        ]

    def __str__(self) -> str:
        return f"Business date {self.current_date}"
