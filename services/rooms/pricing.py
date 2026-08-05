"""What a stay costs, night by night.

Priced per night rather than as ``nights × rate`` because that is what hotels
actually do: a Friday costs more than a Tuesday, a season boundary can fall
mid-stay, and a guest who is quoted one number and billed another will notice.

Resolution order for a given night:
  1. the highest-priority ``RoomRate`` whose range covers the date and whose
     weekday mask allows it
  2. the rate plan's percentage discount off the room type's base rate
  3. the room type's base rate

Tax and service charge come from the hotel record and are applied to the room
subtotal, never to each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from apps.core.utils import money

if TYPE_CHECKING:  # pragma: no cover
    from apps.rooms.models import RatePlan, RoomType
    from apps.tenants.models import Hotel


@dataclass
class NightRate:
    day: date
    amount: Decimal
    source: str


@dataclass
class Quote:
    nights: int
    per_night: list[NightRate] = field(default_factory=list)
    room_total: Decimal = Decimal("0")
    extra_person_total: Decimal = Decimal("0")
    service_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    grand_total: Decimal = Decimal("0")
    currency: str = "BDT"

    @property
    def average_nightly(self) -> Decimal:
        return money(self.room_total / self.nights) if self.nights else Decimal("0")

    def as_dict(self) -> dict:
        return {
            "nights": self.nights,
            "currency": self.currency,
            "room_total": str(self.room_total),
            "extra_person_total": str(self.extra_person_total),
            "service_amount": str(self.service_amount),
            "tax_amount": str(self.tax_amount),
            "grand_total": str(self.grand_total),
            "average_nightly": str(self.average_nightly),
            "breakdown": [
                {"date": night.day.isoformat(), "amount": str(night.amount), "source": night.source}
                for night in self.per_night
            ],
        }


def nightly_rate(room_type: RoomType, rate_plan: RatePlan | None, day: date) -> tuple[Decimal, str]:
    """Price for one night, plus where the number came from.

    The source string is returned so a receptionist can answer "why is Friday
    more expensive" without reading the database.
    """
    from apps.rooms.models import RoomRate

    candidates = RoomRate.all_objects.filter(
        room_type=room_type,
        valid_from__lte=day,
        valid_to__gte=day,
        is_deleted=False,
    ).order_by("-priority", "valid_from")

    if rate_plan is not None:
        candidates = candidates.filter(rate_plan=rate_plan)

    for rate in candidates:
        if rate.applies_on(day):
            return money(rate.price), rate.label or f"rate: {rate.rate_plan.name}"

    base = Decimal(room_type.base_rate)
    if rate_plan is not None and rate_plan.discount_percent:
        discounted = base * (Decimal("100") - Decimal(rate_plan.discount_percent)) / Decimal("100")
        return money(discounted), f"{rate_plan.name} ({rate_plan.discount_percent}% off base)"

    return money(base), "base rate"


def quote(
    *,
    hotel: Hotel,
    room_type: RoomType,
    check_in: date,
    check_out: date,
    rate_plan: RatePlan | None = None,
    adults: int = 1,
    rooms: int = 1,
) -> Quote:
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")

    nights: list[NightRate] = []
    day = check_in
    while day < check_out:  # departure day is not charged
        amount, source = nightly_rate(room_type, rate_plan, day)
        nights.append(NightRate(day=day, amount=amount, source=source))
        day += timedelta(days=1)

    room_total = money(sum(night.amount for night in nights) * rooms)

    # Extra-person charges apply per night, per head over the standard
    # occupancy — not once per stay.
    extra_heads = max(0, adults - room_type.base_occupancy)
    extra_total = money(
        Decimal(extra_heads) * Decimal(room_type.extra_person_rate) * len(nights) * rooms
    )

    subtotal = money(room_total + extra_total)
    service = money(subtotal * Decimal(hotel.service_charge_rate) / Decimal("100"))
    # VAT is charged on the service-inclusive amount, which is how a Bangladesh
    # hotel bill is assembled.
    tax = money((subtotal + service) * Decimal(hotel.tax_rate) / Decimal("100"))

    return Quote(
        nights=len(nights),
        per_night=nights,
        room_total=room_total,
        extra_person_total=extra_total,
        service_amount=service,
        tax_amount=tax,
        grand_total=money(subtotal + service + tax),
        currency=hotel.currency,
    )
