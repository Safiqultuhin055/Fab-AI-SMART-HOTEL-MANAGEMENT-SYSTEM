"""Keeping a booking moving, on a page with nobody behind it.

Two jobs, one file, because they are the same job seen from two sides:

**What to ask next.** A guest who has gone quiet has usually stopped at a step,
not left. The lobby terminal can afford to wait — somebody is standing at it — but
a booking page cannot: the tab stays open, the assistant says nothing, and the
booking is abandoned in silence. So the assistant asks. The question is derived
from the *validated draft*, in the order a booking is actually taken, which means
it is deterministic: no tokens, no model, and it cannot ask for something the
guest already gave.

**What to say when there is no human.** In a lobby, "let me fetch a colleague" is
a real offer: there is a desk ten metres away and the handoff queue lights up on
their screen. On the public booking page it is a promise nobody can keep — no
member of staff is watching that chat, and the guest sits waiting for someone who
is never coming. That was live: a guest asked whether they could settle the bill
on arrival and was told a staff member was being connected.

So on a self-serve channel the assistant says what it can do instead, gives the
desk's own number for anything that genuinely needs a person, and then asks the
next question. Escalation becomes progress rather than a dead end.

Both halves are language-aware and neither invents a fact: the questions are
fixed sentences, the room names come from the priced snapshot, and the payment
terms come from :mod:`services.billing.payment_policy`.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from apps.reception.models import Channel, HandoffReason

if TYPE_CHECKING:  # pragma: no cover
    from apps.reception.models import Conversation
    from apps.tenants.models import Hotel

BN = "bn"

#: Channels where nobody is watching the conversation, so nothing may promise a
#: person. The public booking page is the whole of it today.
#:
#: The lobby terminal is NOT here, and neither is the staff console: both sit in
#: front of a working reception desk, and "let me get a colleague" there is the
#: correct answer to a question the assistant cannot handle (goal.txt R7).
SELF_SERVE_CHANNELS = frozenset({Channel.WEBSITE})

#: How many rooms to name when asking which one to hold. Reading a list of eight
#: down a phone-shaped screen is not a question, it is a catalogue.
NAMED_ROOMS = 3


def is_self_serve(conversation: Conversation | None) -> bool:
    """Is this a conversation no member of staff can be called into?"""
    if conversation is None:
        return False
    return conversation.channel in SELF_SERVE_CHANNELS


def _lang(language: str) -> str:
    return BN if (language or "").startswith(BN) else "en"


# ==============================================================================
# What to ask next
# ==============================================================================

#: The questions, in the order a booking is taken. Fixed sentences rather than a
#: model call: this runs when the guest has said nothing, and spending tokens to
#: re-ask a question we already know the shape of is paying for a lookup.
_ASK: dict[str, dict[str, str]] = {
    "en": {
        "start": "Would you like me to check what we have free? Tell me the date you are "
        "arriving and I will show you the rooms and the real prices.",
        "check_in": "Which date are you arriving? “tonight” or “tomorrow” is fine.",
        "nights": "How many nights will you be staying?",
        "room_code": "Which room shall I hold for you — {rooms}?",
        "room_code_bare": "Which of the rooms would you like me to hold?",
        "guest_name": "What name should I put the booking in?",
        "guest_phone": "And a mobile number, so reception can reach you about the room?",
        "confirm": "I have everything I need. Shall I confirm the booking now?",
        "booked": "Your room is held. Is there anything else you would like me to arrange?",
    },
    "bn": {
        "start": "খালি রুম দেখে দিই? কোন তারিখে আসছেন বললেই রুম আর আসল দাম দুটোই বলে দিচ্ছি।",
        "check_in": "কোন তারিখে আসছেন? “আজ” বা “কাল” বললেও চলবে।",
        "nights": "কয় রাত থাকবেন?",
        "room_code": "কোন রুমটি রেখে দিই — {rooms}?",
        "room_code_bare": "রুমগুলোর মধ্যে কোনটি রেখে দিই?",
        "guest_name": "বুকিংটি কার নামে করব?",
        "guest_phone": "একটি মোবাইল নম্বর দিন — রুমের ব্যাপারে রিসেপশন যোগাযোগ করতে পারবে।",
        "confirm": "সব তথ্য পেয়ে গেছি। এখনই বুকিং কনফার্ম করে দিই?",
        "booked": "রুম রাখা হয়ে গেছে। আর কিছু ঠিক করে দিতে হবে?",
    },
}

#: The draft fields a booking cannot be confirmed without, in the order to ask
#: for them. ``rooms``, ``adults`` and ``children`` are absent on purpose: the
#: server defaults them to 1/1/0 and a guest who wants two rooms says so. Asking
#: "how many children?" of somebody who never mentioned children is an
#: interrogation, not service.
_REQUIRED_STEPS = ("check_in", "nights", "room_code", "guest_name", "guest_phone")


def next_step(conversation: Conversation) -> str:
    """Which step the booking is stopped at: a key in :data:`_ASK`."""
    draft = dict(conversation.booking_draft or {})

    if not draft:
        # Nothing being taken. Either they have already booked — in which case the
        # draft was cleared at confirm — or they have not started.
        return "booked" if conversation.reservations.exists() else "start"

    for step in _REQUIRED_STEPS:
        if not draft.get(step):
            return step
    return "confirm"


def next_question(conversation: Conversation, *, hotel: Hotel | None = None) -> str:
    """The one sentence to say to a guest who has gone quiet."""
    hotel = hotel or conversation.tenant
    language = _lang(conversation.language or (hotel.kiosk_language if hotel else "en"))
    step = next_step(conversation)
    words = _ASK[language]

    if step != "room_code":
        return words[step]

    # Name the rooms rather than asking "which room?" of somebody who has not been
    # shown a list yet. Straight from the priced snapshot — the same query the
    # booking agent uses — so this cannot offer a room type the hotel does not
    # sell, and cannot quote a price at all.
    names = _room_names(conversation, hotel)
    return words["room_code"].format(rooms=names) if names else words["room_code_bare"]


def _draft_date(value) -> date | None:
    """The draft's check-in as a ``date``.

    The draft holds it as an ISO string — that is what the booking agent writes
    after validating it — but a draft written by an older build, or edited by hand
    in the admin, can hold anything. A malformed date means "no date yet", which is
    a question to ask rather than an exception to raise.
    """
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _room_names(conversation: Conversation, hotel: Hotel | None) -> str:
    if hotel is None:
        return ""
    from services.reception import booking_agent

    draft = dict(conversation.booking_draft or {})
    try:
        offers = booking_agent.room_snapshot(
            hotel,
            check_in=_draft_date(draft.get("check_in")),
            nights=int(draft.get("nights") or 1),
            rooms=int(draft.get("rooms") or 1),
        )
    except Exception:  # noqa: BLE001 - a nudge is never worth a 500
        return ""

    names = [offer.name for offer in offers if offer.available][:NAMED_ROOMS]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    joiner = " বা " if _lang(conversation.language) == BN else " or "
    return ", ".join(names[:-1]) + joiner + names[-1]


# ==============================================================================
# What to say instead of fetching a human
# ==============================================================================

_INSTEAD: dict[str, dict[str, str]] = {
    "en": {
        # A guest who typed "I want to talk to a person". Answered honestly: this
        # chat has no one else in it. Never pretend the request was queued.
        "guest_request": "There is no member of staff in this chat — I am the one who can "
        "help you here, and I can take the booking through to the end myself.",
        "guest_request_phone": "If you would rather speak to a person, reception is on {phone}.",
        # Medical, legal, complaints — things a property answers in person.
        "blocked": "That is something the hotel should answer in person rather than "
        "through this page.",
        "unknown": "I do not have that in the hotel's information, so I will not guess at it.",
        "limit": "We have covered a lot in one conversation.",
        "offline": "I cannot reach my own service right now, so I am not able to answer "
        "that here.",
        "form": "The booking form further down this page works without me and takes the "
        "same rooms at the same prices.",
        "call": "Reception is on {phone} if you need a person.",
    },
    "bn": {
        "guest_request": "এই চ্যাটে আর কোনো কর্মী নেই — আমিই আপনাকে সাহায্য করছি, এবং বুকিংটা "
        "শেষ পর্যন্ত আমি নিজেই করে দিতে পারি।",
        "guest_request_phone": "তবু কোনো মানুষের সাথে কথা বলতে চাইলে রিসেপশনের নম্বর {phone}।",
        "blocked": "এ বিষয়টি এই পেজে নয়, হোটেল সরাসরি জানানোই ভালো।",
        "unknown": "হোটেলের তথ্যে এটি নেই, তাই অনুমান করে বলব না।",
        "limit": "আমরা এক আলাপেই অনেকটা কথা বলে ফেলেছি।",
        "offline": "এই মুহূর্তে আমার নিজের সেবাটিতে পৌঁছাতে পারছি না, তাই এখানে উত্তর দিতে পারছি না।",
        "form": "এই পেজের নিচের বুকিং ফর্মটি আমাকে ছাড়াও কাজ করে — একই রুম, একই দাম।",
        "call": "মানুষের সাথে কথা বলতে চাইলে রিসেপশনের নম্বর {phone}।",
    },
}

#: Which sentence answers which escalation reason. Everything not listed reads as
#: "I do not have that" — the honest default, and the one that must never become
#: "a colleague is coming".
#:
#: Keyed by the plain string value: the reason reaches here from a serialised Turn
#: as often as from the enum, and a dict keyed on the enum member misses every one
#: of those.
_REASON_TEXT: dict[str, str] = {
    str(HandoffReason.GUEST_REQUEST): "guest_request",
    str(HandoffReason.BLOCKED_TOPIC): "blocked",
    str(HandoffReason.TURN_LIMIT): "limit",
    str(HandoffReason.AI_UNAVAILABLE): "offline",
}

#: Reasons where the assistant cannot carry on, so the guest is pointed at the
#: deterministic form and the telephone instead of being asked another question.
_DEAD_END = frozenset({str(HandoffReason.AI_UNAVAILABLE), str(HandoffReason.TURN_LIMIT)})


def instead_of_a_human(
    conversation: Conversation,
    reason: str,
    *,
    hotel: Hotel | None = None,
) -> str:
    """What a self-serve channel says where a lobby would call somebody over.

    Three parts, in this order: what is true, how to reach a person if they still
    want one, and the next question. The last part is the point — a guest who
    asked something unanswerable is still mid-booking, and ending on "I cannot
    help with that" abandons a booking over one bad question.
    """
    hotel = hotel or conversation.tenant
    language = _lang(conversation.language or (hotel.kiosk_language if hotel else "en"))
    words = _INSTEAD[language]
    phone = (hotel.phone if hotel else "") or ""

    reason = str(reason or "")
    parts = [words[_REASON_TEXT.get(reason, "unknown")]]

    if reason == str(HandoffReason.GUEST_REQUEST) and phone:
        parts.append(words["guest_request_phone"].format(phone=phone))
    elif reason in _DEAD_END:
        parts.append(words["form"])
        if phone:
            parts.append(words["call"].format(phone=phone))
    elif phone:
        parts.append(words["call"].format(phone=phone))

    if reason not in _DEAD_END:
        parts.append(next_question(conversation, hotel=hotel))

    return " ".join(part for part in parts if part)
