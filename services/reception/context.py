"""Build the CONTEXT block the concierge is allowed to answer from.

The reception prompt is strict: answer only from CONTEXT, otherwise escalate
(goal.txt R6). That rule is worthless if CONTEXT is empty, so this assembles a
live snapshot of the things guests actually ask about — hotel policy, and what
rooms are free tonight at what price.

Same idea as a POS sending the model today's menu with live stock: the model is
never asked to remember or invent a price. It gets the numbers, and
``services.rooms.pricing`` remains the only thing that computes them.

Every fact is generated in the guest's language, because a fact block written
in English produces an English-flavoured Bangla answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone

if TYPE_CHECKING:  # pragma: no cover
    from apps.tenants.models import Hotel

BN = "bn"


@dataclass(frozen=True)
class Fact:
    text: str
    source: str
    #: Lets the offline answerer find the right facts without matching on prose.
    topic: str = ""


def _clock(value) -> str:
    """Render a TimeField as HH:MM whether it is a ``time`` or a raw string.

    An unsaved model instance can still hold whatever was passed in, and a guest
    asking about check-out is not the moment to raise a formatting error.
    """
    return f"{value:%H:%M}" if hasattr(value, "hour") else str(value)[:5]


def hotel_facts(hotel: Hotel | None, language: str = "en") -> list[Fact]:
    """Ground truth from the hotel record."""
    if hotel is None:
        return []

    bn = language.startswith(BN)
    source = "হোটেল তথ্য" if bn else "Hotel profile"
    facts: list[Fact] = []

    def add(text: str, topic: str, src: str = "") -> None:
        facts.append(Fact(text=text, source=src or source, topic=topic))

    if bn:
        add(f"এই হোটেলের নাম {hotel.name}, এটি {hotel.star_rating} তারকা মানের।", "identity")
        add(f"চেক-ইনের নির্ধারিত সময় {_clock(hotel.check_in_time)}।", "checkin")
        add(f"চেক-আউটের নির্ধারিত সময় {_clock(hotel.check_out_time)}।", "checkout")
        add(f"হোটেলে মোট {hotel.total_rooms} টি রুম আছে।", "rooms_count")
        add(f"সব দাম {hotel.currency} মুদ্রায় বলা হয়।", "currency")
        add(
            f"বিলের উপর {hotel.tax_rate}% ভ্যাট এবং {hotel.service_charge_rate}% সার্ভিস চার্জ যোগ হয়।",
            "tax",
            "হিসাব বিভাগ",
        )
    else:
        add(f"The hotel is {hotel.name}, a {hotel.star_rating}-star property.", "identity")
        add(f"Standard check-in time is {_clock(hotel.check_in_time)}.", "checkin")
        add(f"Standard check-out time is {_clock(hotel.check_out_time)}.", "checkout")
        add(f"The hotel has {hotel.total_rooms} rooms in total.", "rooms_count")
        add(f"Prices are quoted in {hotel.currency}.", "currency")
        add(
            f"VAT of {hotel.tax_rate}% and a service charge of "
            f"{hotel.service_charge_rate}% apply to the bill.",
            "tax",
            "Finance settings",
        )

    location = ", ".join(
        part for part in (hotel.address_line1, hotel.address_line2, hotel.city, hotel.state) if part
    )
    if location:
        add(f"হোটেলের ঠিকানা {location}।" if bn else f"The hotel address is {location}.", "address")
    if hotel.phone:
        add(
            (
                f"রিসেপশনের ফোন নম্বর {hotel.phone}।"
                if bn
                else f"The reception telephone number is {hotel.phone}."
            ),
            "phone",
        )
    if hotel.email:
        add(
            f"হোটেলের ইমেইল {hotel.email}।" if bn else f"The hotel email address is {hotel.email}.",
            "email",
        )
    if hotel.website:
        add(
            (
                f"হোটেলের ওয়েবসাইট {hotel.website}।"
                if bn
                else f"The hotel website is {hotel.website}."
            ),
            "website",
        )

    add(
        (
            "গ্রাহক চাইলে যেকোনো সময় রিসেপশনে একজন কর্মী সরাসরি সহায়তা করতে প্রস্তুত।"
            if bn
            else "A human staff member is always available at the reception desk if the guest "
            "prefers to speak to a person."
        ),
        "staff",
        "সেবা নীতি" if bn else "Service policy",
    )

    # How to pay. One sentence per term rather than one long fact, because a guest
    # asks two different questions — "when do I pay?" and "do you take bKash?" —
    # and an answer that cites a paragraph to answer either of them is a wall of
    # text with a number buried in it.
    #
    # This block is why the concierge stopped escalating "can I settle the bill
    # when I get there?": it had no payment terms to cite, so it said it could not
    # confirm and offered a member of staff — on the booking page, where nobody is
    # coming. An unanswerable question is a missing fact, not a missing human.
    from services.billing import payment_policy

    for line in payment_policy.lines(hotel, language):
        add(line, "payment", "পেমেন্ট নীতি" if bn else "Payment terms")
    return facts


def room_facts(hotel: Hotel | None, language: str = "en", nights: int = 1) -> list[Fact]:
    """Live room types, tonight's availability, and tonight's real price.

    This is the snapshot that makes "do you have a room?" answerable. Prices come
    from ``services.rooms.pricing`` — the same code that prices an actual
    booking — so what the guest is told and what they are charged cannot drift.
    """
    if hotel is None:
        return []

    from services.booking import availability
    from services.rooms import pricing

    bn = language.startswith(BN)
    source = "আজকের রুম ও দাম" if bn else "Live room availability"
    today = timezone.localdate()
    checkout = today + timedelta(days=nights)

    facts: list[Fact] = []
    try:
        rows = availability.by_type(hotel, today, checkout)
    except Exception:  # noqa: BLE001 - a reporting failure must not kill the turn
        return []

    free_total = 0
    for row in rows:
        room_type = row.room_type
        free_total += row.available
        try:
            quote = pricing.quote(
                hotel=hotel,
                room_type=room_type,
                check_in=today,
                check_out=checkout,
                adults=room_type.base_occupancy,
            )
            price = quote.grand_total
        except Exception:  # noqa: BLE001
            price = room_type.base_rate

        if bn:
            state = f"{row.available} টি খালি আছে" if row.available else "আজ কোনোটি খালি নেই"
            view = f", {room_type.view} ভিউ" if room_type.view else ""
            text = (
                f"{room_type.name} — সর্বোচ্চ {room_type.max_occupancy} জন{view}, "
                f"আজ রাতের ভাড়া সব মিলিয়ে {price:,.0f} {hotel.currency}। {state}।"
            )
        else:
            state = f"{row.available} available tonight" if row.available else "none free tonight"
            view = f", {room_type.view} view" if room_type.view else ""
            text = (
                f"{room_type.name} — sleeps up to {room_type.max_occupancy}{view}, "
                f"{price:,.0f} {hotel.currency} for tonight including tax and service. {state}."
            )
        facts.append(Fact(text=text, source=source, topic="room_type"))

    if facts:
        facts.append(
            Fact(
                text=(
                    f"আজ রাতে মোট {free_total} টি রুম খালি আছে।"
                    if bn
                    else f"{free_total} rooms are free tonight in total."
                ),
                source=source,
                topic="availability",
            )
        )
    return facts


def retrieve(
    hotel: Hotel | None,
    question: str,  # noqa: ARG001 - see docstring
    language: str = "en",
) -> list[Fact]:
    """Everything the concierge may answer this question from.

    ``question`` is accepted and currently ignored: the fact set is small enough
    to pass whole, and pretending to rank twenty items would be theatre. In P2
    this gains a hybrid pgvector + full-text branch over ``kb_chunk`` and the
    signature does not have to change.
    """
    return hotel_facts(hotel, language) + room_facts(hotel, language)


def render(facts: list[Fact]) -> str:
    """Numbered block. The numbers are what the model cites as [1], [2]."""
    if not facts:
        return "(no verified information available)"
    return "\n".join(
        f"[{i}] {fact.text}  (source: {fact.source})" for i, fact in enumerate(facts, 1)
    )


#: Matches [3], [9, 10], [2,4] and [1 2] — every grouping a model actually
#: writes. Looking only for the exact string "[n]" missed "[9, 10]" entirely,
#: which read as an uncited answer and got perfectly good replies escalated to
#: a human for low confidence.
_CITE_RE = re.compile(r"\[([\d\s,]+)\]")


def cited_indexes(answer: str) -> set[int]:
    found: set[int] = set()
    for group in _CITE_RE.findall(answer or ""):
        for part in re.split(r"[,\s]+", group.strip()):
            if part.isdigit():
                found.add(int(part))
    return found


def citations(facts: list[Fact], answer: str) -> list[dict[str, str]]:
    """Return the sources the answer actually referenced.

    Only citations the model really used are recorded. Attaching all of them
    would make every answer look sourced, which defeats the point of showing
    citations at all.
    """
    referenced = cited_indexes(answer)
    return [
        {"index": str(index), "text": fact.text, "source": fact.source}
        for index, fact in enumerate(facts, 1)
        if index in referenced
    ]
