"""What a guest is told about paying — one sentence, one source.

Four surfaces have to say the same thing about money: the concierge answering
"can I pay when I arrive?", the booking page's fallback form, the confirmation
slip, and the assistant's own confirmation sentence. Four copies of that text is
four chances to promise a payment method the property does not take.

So the property's terms are read from the ``Hotel`` row here and rendered here, in
both languages, and everything else imports it.

Two things this module deliberately does NOT do:

* it never moves money. Nothing in the guest-facing product charges anything
  (goal.txt D11); a booking is a held room, and the ``Payment`` row is written by
  a member of staff at the desk or, later, by a gateway webhook.
* it never invents terms. A property that has configured nothing gets the honest
  default — settled at the desk, cash and card — which is what the page said in
  hardcoded English before this existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from apps.tenants.models import Hotel

BN = "bn"

#: Method labels, in the guest's language. The wallet brands are proper nouns and
#: stay in Latin script in both — "বিকাশ" is written bKash on every shop sign in
#: the country, including in Bangla sentences.
_METHOD_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "cash": "cash",
        "card": "card",
        "bkash": "bKash",
        "nagad": "Nagad",
    },
    "bn": {
        "cash": "নগদ টাকা",
        "card": "কার্ড",
        "bkash": "bKash",
        "nagad": "Nagad",
    },
}

_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "on_arrival": "Nothing is charged when you book. The room is held for you and "
        "the bill is settled at the reception desk.",
        "on_arrival_methods": "At the desk you can pay by {methods}.",
        "advance": "This property asks for an advance before the room is held.",
        "advance_wallet": "Send it to {wallet} {number} and keep the transaction id — "
        "reception will match it to your booking.",
        "no_card_online": "This page never asks for card details.",
        "tax": "The total shown already includes {vat}% VAT and a {service}% service charge.",
        "ask": "How would you like to pay — at the desk, or an advance now?",
        "at_desk_short": "Pay at the desk",
        "advance_short": "Advance required",
    },
    "bn": {
        "on_arrival": "বুকিং করার সময় কোনো টাকা নেওয়া হয় না। রুমটি আপনার জন্য রাখা থাকবে, "
        "বিল রিসেপশনেই পরিশোধ করবেন।",
        "on_arrival_methods": "রিসেপশনে {methods} — যেভাবে সুবিধা, সেভাবে দিতে পারবেন।",
        "advance": "এই হোটেলে রুম নিশ্চিত করার আগে কিছু টাকা অগ্রিম দিতে হয়।",
        "advance_wallet": "{wallet} {number} নম্বরে পাঠিয়ে ট্রানজেকশন আইডিটি রাখবেন — "
        "রিসেপশন সেটি আপনার বুকিংয়ের সাথে মিলিয়ে নেবে।",
        "no_card_online": "এই পেজে কখনো কার্ডের তথ্য চাওয়া হয় না।",
        "tax": "দেখানো মোট টাকার মধ্যেই {vat}% ভ্যাট ও {service}% সার্ভিস চার্জ ধরা আছে।",
        "ask": "বিলটি কীভাবে দিতে চান — রিসেপশনে, নাকি এখনই কিছু অগ্রিম?",
        "at_desk_short": "রিসেপশনে পরিশোধ",
        "advance_short": "অগ্রিম লাগবে",
    },
}


def _lang(language: str) -> str:
    return BN if (language or "").startswith(BN) else "en"


@dataclass(frozen=True)
class Policy:
    """One property's payment terms, resolved."""

    advance_required: bool
    methods: tuple[str, ...]
    wallet: str
    wallet_number: str
    note: str
    vat_rate: str
    service_rate: str
    currency: str


def for_hotel(hotel: Hotel | None) -> Policy:
    """The property's terms, with the honest defaults when nothing is configured."""
    from apps.tenants.models import AdvanceWallet, PaymentTiming

    if hotel is None:
        return Policy(
            advance_required=False,
            methods=("cash", "card"),
            wallet="",
            wallet_number="",
            note="",
            vat_rate="0",
            service_rate="0",
            currency="BDT",
        )

    methods = tuple(
        name
        for name, on in (
            ("cash", hotel.accepts_cash),
            ("card", hotel.accepts_card),
            ("bkash", hotel.accepts_bkash),
            ("nagad", hotel.accepts_nagad),
        )
        if on
    )

    wallet = hotel.advance_wallet or AdvanceWallet.NONE
    # An advance nobody can send is not an advance. A property that ticked
    # "advance" and never filled the number in would otherwise have the assistant
    # demanding money with no way to send it, which reads as a scam.
    advance = hotel.payment_timing == PaymentTiming.ADVANCE and bool(
        wallet and hotel.advance_wallet_number
    )

    return Policy(
        advance_required=advance,
        methods=methods or ("cash",),
        wallet=wallet if advance else "",
        wallet_number=hotel.advance_wallet_number if advance else "",
        note=hotel.payment_note,
        vat_rate=_rate(hotel.tax_rate),
        service_rate=_rate(hotel.service_charge_rate),
        currency=hotel.currency,
    )


def _rate(value) -> str:
    """A percentage the way a person writes it: 15, 7.5 — never 15.00.

    ``f"{value:g}"`` is the obvious way and the wrong one: on a ``Decimal`` the ``g``
    format keeps the stored scale, so a DecimalField(decimal_places=2) holding 15
    renders "15.00% VAT" in a sentence read by guests. Formatting a float instead
    would fix the zeros and introduce 7.000000000000001.
    """
    number = Decimal(str(value or 0)).normalize()
    if number == number.to_integral():
        number = number.to_integral()
    return f"{number:f}"


def lines(hotel: Hotel | None, language: str = "en") -> list[str]:
    """The terms as separate sentences, for a bulleted panel.

    Ordered the way a guest asks: when, how, what is already included, and the
    property's own words last — a hotel that wrote a note wrote it as an addition,
    not as a replacement for the terms above it.
    """
    policy = for_hotel(hotel)
    words = _TEXT[_lang(language)]
    method_words = _METHOD_WORDS[_lang(language)]
    out: list[str] = []

    if policy.advance_required:
        out.append(words["advance"])
        out.append(
            words["advance_wallet"].format(
                wallet=method_words.get(policy.wallet, policy.wallet),
                number=policy.wallet_number,
            )
        )
    else:
        out.append(words["on_arrival"])

    if policy.methods:
        named = [method_words[name] for name in policy.methods if name in method_words]
        if named:
            out.append(words["on_arrival_methods"].format(methods=_join(named, language)))

    out.append(words["tax"].format(vat=policy.vat_rate, service=policy.service_rate))
    out.append(words["no_card_online"])
    if policy.note:
        out.append(policy.note)
    return out


def summary(hotel: Hotel | None, language: str = "en") -> str:
    """The terms as one paragraph, for the assistant's fact set."""
    return " ".join(lines(hotel, language))


def on_confirmation(hotel: Hotel | None, language: str = "en") -> str:
    """The payment sentence to say the moment a booking is confirmed.

    One or two sentences, not the full terms: a guest who has just been given a
    reference number needs to know whether money is owed now and how it will be
    taken, and reading them the VAT rate at that moment buries it.

    It is said unprompted because the alternative is what used to happen — the guest
    asks, the assistant has nothing to cite, and a booking ends on "I am connecting
    a member of staff".
    """
    policy = for_hotel(hotel)
    words = _TEXT[_lang(language)]
    method_words = _METHOD_WORDS[_lang(language)]

    if policy.advance_required:
        return " ".join(
            (
                words["advance"],
                words["advance_wallet"].format(
                    wallet=method_words.get(policy.wallet, policy.wallet),
                    number=policy.wallet_number,
                ),
            )
        )

    parts = [words["on_arrival"]]
    named = [method_words[name] for name in policy.methods if name in method_words]
    if named:
        parts.append(words["on_arrival_methods"].format(methods=_join(named, language)))
    return " ".join(parts)


def ask(language: str = "en") -> str:
    """The question the assistant puts to a guest whose booking is confirmed."""
    return _TEXT[_lang(language)]["ask"]


def badge(hotel: Hotel | None, language: str = "en") -> str:
    """Three or four words, for a pill on the slip."""
    policy = for_hotel(hotel)
    words = _TEXT[_lang(language)]
    return words["advance_short"] if policy.advance_required else words["at_desk_short"]


def _join(items: list[str], language: str) -> str:
    """"a, b or c" — and the Bangla equivalent, which is not the same shape."""
    if len(items) == 1:
        return items[0]
    last = items[-1]
    head = ", ".join(items[:-1])
    return f"{head} বা {last}" if _lang(language) == BN else f"{head} or {last}"
