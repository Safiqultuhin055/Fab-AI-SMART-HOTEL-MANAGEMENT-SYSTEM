"""Posting money.

Every charge and every payment in ASHOS goes through this module. Four rules
it enforces:

*Lines are append-only.* Voiding posts a reversing line. The original stays
visible, which is the difference between a ledger and a spreadsheet.

*Totals are re-derived, never incremented.* ``Folio.recalculate`` re-sums the
lines. A running total that drifts is worse than no total.

*Payments are idempotent.* A retried checkout with the same key returns the
original payment instead of taking the money twice (goal.txt D16).

*The AI proposes, a human posts.* ``ai_suggested`` records which lines the AI
put forward; ``posted_by`` records the person who accepted them (goal.txt D11).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.accounts.audit import record as audit
from apps.accounts.models import AuditAction
from apps.billing.models import (
    BusinessDate,
    ChargeType,
    Folio,
    FolioLine,
    FolioStatus,
    Invoice,
    Payment,
    PaymentStatus,
)
from apps.core.exceptions import Conflict, ValidationError
from apps.core.utils import money

if TYPE_CHECKING:  # pragma: no cover
    from apps.booking.models import Reservation
    from apps.tenants.models import Hotel


# ==============================================================================
# Business date
# ==============================================================================


def business_date(hotel: Hotel) -> date:
    """The hotel's own date. Charges after midnight belong to yesterday."""
    row = BusinessDate.all_objects.filter(tenant=hotel, is_deleted=False).first()
    return row.current_date if row else timezone.localdate()


def ensure_business_date(hotel: Hotel) -> BusinessDate:
    row = BusinessDate.all_objects.filter(tenant=hotel, is_deleted=False).first()
    if row is None:
        row = BusinessDate.objects.create(tenant=hotel, current_date=timezone.localdate())
    return row


# ==============================================================================
# Folio
# ==============================================================================


def open_folio(reservation: Reservation) -> Folio:
    existing = Folio.all_objects.filter(reservation=reservation, is_deleted=False).first()
    if existing:
        return existing

    return Folio.objects.create(
        tenant=reservation.tenant,
        reservation=reservation,
        guest=reservation.guest,
        currency=reservation.tenant.currency if reservation.tenant else "BDT",
    )


@transaction.atomic
def post_charge(
    folio: Folio,
    *,
    charge_type: str,
    description: str,
    amount: Decimal | float | str,
    quantity: Decimal | int = 1,
    unit_price: Decimal | float | str | None = None,
    on_date: date | None = None,
    source_module: str = "",
    source_ref: str = "",
    user=None,
    ai_suggested: bool = False,
) -> FolioLine:
    if folio.status != FolioStatus.OPEN:
        raise Conflict("This folio is closed; charges can no longer be posted to it.")

    value = money(amount)
    if value == 0:
        raise ValidationError("A zero-value charge is not a charge.")

    line = FolioLine.objects.create(
        tenant=folio.tenant,
        folio=folio,
        charge_type=charge_type,
        description=description[:200],
        business_date=on_date or business_date(folio.tenant),
        quantity=Decimal(str(quantity)),
        unit_price=money(unit_price if unit_price is not None else value),
        amount=value,
        source_module=source_module[:30],
        source_ref=str(source_ref)[:64],
        posted_by=user,
        ai_suggested=ai_suggested,
    )
    folio.recalculate()

    if charge_type in {ChargeType.DISCOUNT, ChargeType.ADJUSTMENT, ChargeType.DAMAGE}:
        # Money-affecting judgement calls get an audit row; routine room and
        # restaurant postings would just be noise.
        audit(
            AuditAction.UPDATE,
            summary=f"{charge_type} {value} posted to folio {folio.number}",
            obj=line,
            hotel_id=str(folio.tenant_id),
        )
    return line


@transaction.atomic
def void_line(line: FolioLine, *, reason: str, user=None) -> FolioLine:
    """Reverse a posted charge without deleting it."""
    if line.is_voided:
        raise Conflict("That line has already been voided.")

    reversal = FolioLine.objects.create(
        tenant=line.tenant,
        folio=line.folio,
        charge_type=line.charge_type,
        description=f"VOID: {line.description} ({reason})"[:200],
        business_date=business_date(line.tenant),
        quantity=line.quantity,
        unit_price=-line.unit_price,
        amount=-line.amount,
        source_module=line.source_module,
        source_ref=str(line.pk),
        posted_by=user,
    )
    line.is_voided = True
    line.voided_by_line = reversal
    line.save(update_fields=["is_voided", "voided_by_line", "updated_at"])

    # The reversal must not itself count as a live charge.
    reversal.is_voided = False
    line.folio.recalculate()

    audit(
        AuditAction.UPDATE,
        summary=f"voided {line.amount} on folio {line.folio.number}: {reason}",
        obj=line,
        hotel_id=str(line.tenant_id),
    )
    return reversal


# ==============================================================================
# Payments
# ==============================================================================


@transaction.atomic
def post_payment(
    folio: Folio,
    *,
    method: str,
    amount: Decimal | float | str,
    reference: str = "",
    idempotency_key: str = "",
    user=None,
    notes: str = "",
) -> Payment:
    """Record money received.

    Cash and desk-taken card are captured immediately — the money is already in
    the drawer. A future online gateway will create the row ``PENDING`` and
    capture it on callback, which is why status is a field rather than implied.
    """
    value = money(amount)
    if value <= 0:
        raise ValidationError("Payment amount must be positive.")

    if idempotency_key:
        existing = Payment.all_objects.filter(
            tenant=folio.tenant, idempotency_key=idempotency_key, is_deleted=False
        ).first()
        if existing:
            # Not an error: the client retried. Return what was already taken.
            return existing

    payment = Payment.objects.create(
        tenant=folio.tenant,
        folio=folio,
        method=method,
        status=PaymentStatus.CAPTURED,
        amount=value,
        currency=folio.currency,
        reference=reference[:100],
        idempotency_key=idempotency_key[:64],
        received_by=user,
        notes=notes[:255],
    )
    folio.recalculate()

    audit(
        AuditAction.PAYMENT,
        summary=f"{method} {value} {folio.currency} on folio {folio.number}",
        obj=payment,
        hotel_id=str(folio.tenant_id),
    )
    return payment


# ==============================================================================
# Invoice
# ==============================================================================


@transaction.atomic
def issue_invoice(folio: Folio, *, user=None) -> Invoice:
    """Freeze the folio into a numbered document.

    The line detail and the hotel's tax identity are snapshotted into JSON.
    Re-rendering an old invoice from live data would silently rewrite history
    the next time the VAT rate or the hotel address changes.
    """
    folio.recalculate()
    hotel = folio.tenant
    # Voided lines and their reversals both appear: the guest should be able to
    # see that a charge was made and taken off again, and the pair nets to zero.
    lines = list(folio.lines.order_by("business_date", "created_at"))

    totals = {"tax": Decimal("0"), "service": Decimal("0"), "discount": Decimal("0")}
    subtotal = Decimal("0")
    for line in lines:
        if line.charge_type == ChargeType.TAX:
            totals["tax"] += line.amount
        elif line.charge_type == ChargeType.SERVICE:
            totals["service"] += line.amount
        elif line.charge_type == ChargeType.DISCOUNT:
            totals["discount"] += line.amount
        else:
            subtotal += line.amount

    invoice = Invoice.objects.create(
        tenant=hotel,
        number=_next_invoice_number(hotel),
        folio=folio,
        guest=folio.guest,
        currency=folio.currency,
        subtotal=money(subtotal),
        tax_amount=money(totals["tax"]),
        service_amount=money(totals["service"]),
        discount_amount=money(totals["discount"]),
        total=money(folio.charges_total),
        amount_paid=money(folio.payments_total),
        is_paid=folio.balance <= 0,
        bill_to_name=folio.guest.full_name,
        bill_to_address=folio.guest.address,
        tax_registration_no=hotel.tax_registration_no if hotel else "",
        issued_by=user,
        snapshot={
            "hotel": {
                "name": hotel.name if hotel else "",
                "address": hotel.address_line1 if hotel else "",
                "phone": hotel.phone if hotel else "",
                "tax_rate": str(hotel.tax_rate) if hotel else "0",
                "service_charge_rate": str(hotel.service_charge_rate) if hotel else "0",
            },
            "reservation": {
                "code": folio.reservation.code,
                "check_in": folio.reservation.check_in.isoformat(),
                "check_out": folio.reservation.check_out.isoformat(),
                "nights": folio.reservation.nights,
            },
            "lines": [
                {
                    "date": line.business_date.isoformat(),
                    "type": line.charge_type,
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "amount": str(line.amount),
                }
                for line in lines
            ],
        },
    )

    audit(
        AuditAction.CREATE,
        summary=f"invoice {invoice.number} issued for {invoice.total} {invoice.currency}",
        obj=invoice,
        hotel_id=str(hotel.pk) if hotel else None,
    )
    return invoice


def _next_invoice_number(hotel) -> str:
    """Sequential per hotel per year: ``INV-2026-000123``.

    Auditors expect an unbroken sequence they can follow. A random id is fine
    for a primary key and useless on a printed bill.
    """
    year = timezone.localdate().year
    prefix = f"INV-{year}-"
    last = (
        Invoice.all_objects.filter(tenant=hotel, number__startswith=prefix)
        .order_by("-number")
        .values_list("number", flat=True)
        .first()
    )
    nxt = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{nxt:06d}"


def settle(folio: Folio, *, user=None) -> Invoice:
    """Close the folio. Refuses while money is still owed."""
    folio.recalculate()
    if folio.balance > 0:
        raise Conflict(
            f"Cannot settle: {folio.balance} {folio.currency} still outstanding. "
            "Take payment or post an adjustment first."
        )

    invoice = issue_invoice(folio, user=user)
    folio.status = FolioStatus.SETTLED
    folio.settled_at = timezone.now()
    folio.save(update_fields=["status", "settled_at", "updated_at"])
    return invoice


# ==============================================================================
# Night audit
# ==============================================================================


@transaction.atomic
def run_night_audit(hotel: Hotel, *, user=None) -> dict:
    """Post one night's room charges for every in-house stay, then roll the date.

    Idempotent per business date: a second run posts nothing, because each room
    charge carries the business date it covers and is checked for first. Night
    audit gets re-run by hand more often than anyone admits.
    """
    from apps.booking.models import Reservation, ReservationStatus

    state = ensure_business_date(hotel)
    audit_date = state.current_date

    in_house = (
        Reservation.all_objects.filter(
            tenant=hotel, status=ReservationStatus.CHECKED_IN, is_deleted=False
        )
        .select_related("guest", "rate_plan")
        .prefetch_related("allocations__room_type")
    )

    charged = 0
    revenue = Decimal("0")

    for reservation in in_house:
        # A guest leaving this morning is not charged for tonight.
        if reservation.check_out <= audit_date:
            continue

        folio = open_folio(reservation)
        # Any room line for this date, voided or not, means the night is done.
        # Re-posting it because someone reversed a charge would be worse than
        # leaving the correction alone.
        already = folio.lines.filter(charge_type=ChargeType.ROOM, business_date=audit_date).exists()
        if already:
            continue

        for allocation in reservation.allocations.all():
            from services.rooms import pricing

            rate, source = pricing.nightly_rate(
                allocation.room_type, reservation.rate_plan, audit_date
            )
            label = allocation.room.number if allocation.room else allocation.room_type.name

            post_charge(
                folio,
                charge_type=ChargeType.ROOM,
                description=f"Room {label} — {audit_date:%d %b} ({source})",
                amount=rate,
                on_date=audit_date,
                source_module="night_audit",
                source_ref=str(allocation.pk),
                user=user,
            )
            service = money(rate * Decimal(hotel.service_charge_rate) / Decimal("100"))
            if service:
                post_charge(
                    folio,
                    charge_type=ChargeType.SERVICE,
                    description=f"Service charge {hotel.service_charge_rate}%",
                    amount=service,
                    on_date=audit_date,
                    source_module="night_audit",
                    user=user,
                )
            tax = money((rate + service) * Decimal(hotel.tax_rate) / Decimal("100"))
            if tax:
                post_charge(
                    folio,
                    charge_type=ChargeType.TAX,
                    description=f"VAT {hotel.tax_rate}%",
                    amount=tax,
                    on_date=audit_date,
                    source_module="night_audit",
                    user=user,
                )

            charged += 1
            revenue += rate + service + tax

    state.current_date = audit_date + timedelta(days=1)
    state.last_audit_at = timezone.now()
    state.last_audit_by = user
    state.rooms_charged = charged
    state.revenue_posted = money(revenue)
    state.save()

    return {
        "business_date": audit_date,
        "next_business_date": state.current_date,
        "rooms_charged": charged,
        "revenue_posted": money(revenue),
    }
