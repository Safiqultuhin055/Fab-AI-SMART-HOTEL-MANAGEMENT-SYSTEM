"""Online booking: what a guest can do without a receptionist.

Same three questions the desk asks, in the same order — when, which room, who —
and every answer is checked against the same services the desk uses. Nothing here
prices a room or holds inventory itself:

    availability.by_type      what is actually free for those dates
    pricing.quote             what it costs, night by night, with service and VAT
    reservations.create       the hold and the folio, atomically

That matters more than it sounds. A public booking page that keeps its own copy of
"what a Deluxe costs" is a page that will quote yesterday's rate the first time
somebody changes a price, and a page that counts its own availability is how a
room gets sold twice.

Money is not taken. ``goal.txt`` D11: the assistant may create a held booking, it
never moves money — and the same rule applies to an unattended web page, which has
no card reader and no operator standing behind it. The slip says where to pay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from apps.core.exceptions import ValidationError

if TYPE_CHECKING:  # pragma: no cover
    from apps.booking.models import Reservation
    from apps.tenants.models import Hotel

#: The same ceilings the kiosk uses. Beyond these the rate calendar is usually
#: unpublished, so a quote would be a guess dressed up as a price.
MAX_NIGHTS = 30
MAX_ADVANCE_DAYS = 365
MAX_ROOMS = 5
MAX_GUESTS = 12


@dataclass(frozen=True)
class Stay:
    """The validated "when and how many" of a search."""

    check_in: date
    nights: int
    adults: int
    children: int
    rooms: int

    @property
    def check_out(self) -> date:
        return self.check_in + timedelta(days=self.nights)


@dataclass(frozen=True)
class Offer:
    """One room type, free for this stay, priced for it."""

    room_type: Any
    available: int
    quote: Any
    photo: dict[str, str] | None

    @property
    def code(self) -> str:
        return self.room_type.code

    @property
    def nightly(self) -> Decimal:
        return self.quote.average_nightly


def parse_stay(data, *, today: date | None = None) -> Stay:
    """Read a stay out of query parameters, or say why it cannot be.

    Everything a browser sends is a string somebody can edit in the address bar,
    so each field is bounded here rather than trusted. The errors are the ones a
    guest can act on: a date in the past, a stay longer than the rate calendar
    covers, more rooms than a person books online.
    """
    today = today or timezone.localdate()

    raw_date = (data.get("check_in") or "").strip()
    try:
        check_in = date.fromisoformat(raw_date) if raw_date else today + timedelta(days=1)
    except ValueError as exc:
        raise ValidationError("That arrival date is not a date.") from exc

    if check_in < today:
        raise ValidationError("That arrival date has already passed.")
    if check_in > today + timedelta(days=MAX_ADVANCE_DAYS):
        raise ValidationError(f"We take online bookings up to {MAX_ADVANCE_DAYS} days ahead.")

    def number(field: str, default: int, low: int, high: int) -> int:
        raw = (data.get(field) or "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValidationError(
                f"{field.replace('_', ' ').capitalize()} must be a number."
            ) from exc
        return max(low, min(high, value))

    return Stay(
        check_in=check_in,
        nights=number("nights", 1, 1, MAX_NIGHTS),
        adults=number("adults", 2, 1, MAX_GUESTS),
        children=number("children", 0, 0, MAX_GUESTS),
        rooms=number("rooms", 1, 1, MAX_ROOMS),
    )


def offers(hotel: Hotel, stay: Stay) -> list[Offer]:
    """Every room type that can take this stay, priced for it.

    Occupancy is a filter, not a warning: a room type that sleeps two is not an
    answer to a party of four, and offering it so the list looks fuller is how a
    family arrives to a bed they cannot all sleep in.
    """
    from services.booking import availability
    from services.rooms import media, pricing

    rows = [
        row
        for row in availability.by_type(hotel, stay.check_in, stay.check_out)
        if row.available >= stay.rooms
        and row.room_type.max_occupancy * stay.rooms >= stay.adults + stay.children
    ]
    photos = media.gallery([row.room_type.pk for row in rows], limit=1)

    out: list[Offer] = []
    for row in rows:
        quote = pricing.quote(
            hotel=hotel,
            room_type=row.room_type,
            check_in=stay.check_in,
            check_out=stay.check_out,
            adults=stay.adults,
            rooms=stay.rooms,
        )
        gallery = photos.get(str(row.room_type.pk)) or []
        out.append(
            Offer(
                room_type=row.room_type,
                available=row.available,
                quote=quote,
                photo=gallery[0] if gallery else None,
            )
        )
    return out


def find_offer(hotel: Hotel, stay: Stay, code: str) -> Offer | None:
    """The offer for one room code, re-priced and re-counted right now.

    Called again on the page that shows the bill and again before the write, so a
    guest who left the tab open over a price change is quoted the new price rather
    than the one in their URL.
    """
    wanted = (code or "").strip().upper()
    if not wanted:
        return None
    return next((offer for offer in offers(hotel, stay) if offer.code.upper() == wanted), None)


def book(
    hotel: Hotel,
    stay: Stay,
    *,
    room_code: str,
    guest_name: str,
    guest_phone: str,
    guest_email: str = "",
    requests: str = "",
) -> Reservation:
    """Hold the room. Money is not taken and no payment is recorded.

    The offer is looked up one more time inside this call rather than trusted from
    the form: between the guest reading the price and pressing the button, the desk
    may have sold the last room of that type.
    """
    from apps.booking.models import BookingSource
    from services.booking import reservations

    name = " ".join((guest_name or "").split())
    phone = _clean_phone(guest_phone)
    if len(name) < 2:
        raise ValidationError("Please give the name the booking is for.")
    if len(phone) < 6:
        raise ValidationError("Please give a mobile number we can reach you on.")

    offer = find_offer(hotel, stay, room_code)
    if offer is None:
        raise ValidationError("That room is no longer available for those dates.")

    return reservations.create(
        hotel=hotel,
        guest=_guest_for(hotel, name, phone, guest_email),
        check_in=stay.check_in,
        check_out=stay.check_out,
        room_type=offer.room_type,
        rooms=stay.rooms,
        adults=stay.adults,
        children=stay.children,
        source=BookingSource.WEBSITE,
        special_requests=requests[:500],
    )


def slip(hotel: Hotel, code: str) -> dict[str, Any]:
    """Everything a confirmation slip shows, for one reference.

    The charges come off the folio the booking opened, not off a recalculation:
    the slip and the bill the guest settles at the desk have to be the same
    numbers, and the folio is the one that is true.
    """
    from apps.billing.models import Folio
    from apps.booking.models import Reservation

    reservation = (
        Reservation.all_objects.filter(tenant=hotel, code=(code or "").strip().upper())
        .select_related("guest", "rate_plan")
        .prefetch_related("allocations__room", "allocations__room_type")
        .first()
    )
    if reservation is None:
        return {}

    folio = (
        Folio.all_objects.filter(tenant=hotel, reservation=reservation)
        .prefetch_related("lines")
        .first()
    )
    return {
        "reservation": reservation,
        "folio": folio,
        "lines": [line for line in folio.lines.all() if not line.is_voided] if folio else [],
        "allocations": list(reservation.allocations.all()),
    }


def _guest_for(hotel, name: str, phone: str, email: str):
    """Reuse the guest record if this phone has booked before.

    Matching on phone rather than name, for the reason the kiosk does: two
    Rahmans is normal, two Rahmans on one mobile is one person, and a duplicate
    splits their stay history.
    """
    from apps.guests.models import Guest

    existing = Guest.all_objects.filter(tenant=hotel, phone=phone, is_deleted=False).first()
    if existing is not None:
        if email and not existing.email:
            existing.email = email
            existing.save(update_fields=["email"])
        return existing

    first, _, last = name.partition(" ")
    return Guest.objects.create(
        tenant=hotel,
        first_name=first[:80] or name[:80],
        last_name=last[:80],
        phone=phone,
        email=email[:254],
    )


#: Bengali digits, so a number typed on a Bangla keypad is stored as one a
#: receptionist can dial.
_BENGALI = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def _clean_phone(value: str) -> str:
    digits = (value or "").translate(_BENGALI)
    return "".join(character for character in digits if character.isdigit() or character == "+")
