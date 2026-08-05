"""Structured booking conversation — the kiosk equivalent of taking an order.

Same shape as a POS voice agent: every turn the model returns a *reply* plus the
**complete current draft**, and the server validates that draft against live
data before anything is believed. What differs is what a wrong answer costs. A
canteen mis-ordering a samosa is a refund; a hotel mis-selling a room that is
already occupied is a guest standing in a corridor at midnight.

So the division of labour is deliberate:

    the model owns   : language, intent, what the guest meant, what to ask next
    the server owns  : dates, availability, occupancy limits, prices, the write

The model never sees a price it can echo back as fact and never decides that a
room is free. It nominates a room type by ``code``; :func:`_finalize` re-prices
it through ``services.rooms.pricing`` and re-checks stock through
``services.booking.availability`` — the same code paths the front desk uses, so
a kiosk quote and a folio charge cannot diverge.

``ready_to_confirm`` from the model is a *request*, never authority. The real
decision is made in :func:`confirm`, inside a transaction, where the exclusion
constraint on ``ReservationRoom`` has the last word (goal.txt D11: the AI may
create a held booking, but it never moves money).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from apps.core.exceptions import ValidationError
from services.ai import gateway
from services.ai.base import ChatMessage, Role

if TYPE_CHECKING:  # pragma: no cover
    from apps.booking.models import Reservation
    from apps.reception.models import Conversation
    from apps.tenants.models import Hotel

logger = logging.getLogger("ashos.reception")

BN = "bn"

#: How far ahead the kiosk will take a booking. Beyond this the rate calendar is
#: usually unpublished, so a quote would be a guess.
MAX_NIGHTS = 30
MAX_ADVANCE_DAYS = 365
MAX_ROOMS = 5

#: Turns of history the booking model sees. Enough to follow "make it three
#: nights instead", short enough that a rambling kiosk session stays cheap.
HISTORY_TURNS = 8


# ==============================================================================
# Intent
# ==============================================================================

#: Entering booking mode costs a bigger prompt, so it is gated on intent rather
#: than run on every turn. Bangla and English in one pattern because guests here
#: mix the two mid-sentence.
BOOKING_INTENT = re.compile(
    r"(রুম\s*(বুক|লাগবে|দরকার|চাই|নেব|নিব|নিতে)|বুকিং|বুক\s*কর|থাকতে\s*চাই|থাকার\s*ব্যবস্থা"
    r"|রাত\s*থাকব|কয়\s*রাত|রিজার্ভ"
    r"|book (a |the )?room|make a booking|reserve|reservation|i(?: would like|'?d like| want)"
    r" (a|to book)|check in tomorrow|stay (for|tonight)|need a room|want a room)",
    re.IGNORECASE,
)

#: Backing out. Must be honoured immediately and in either language — a guest
#: who says "থাক, লাগবে না" and is asked for their phone number again has been
#: trapped by the machine.
CANCEL_INTENT = re.compile(
    r"(বাতিল|থাক\b|লাগবে\s*না|আর\s*না|বাদ\s*দাও|করব\s*না"
    r"|cancel|never ?mind|forget it|stop|not now|no thanks)",
    re.IGNORECASE,
)


def wants_booking(text: str) -> bool:
    return bool(BOOKING_INTENT.search(text or ""))


def wants_out(text: str) -> bool:
    return bool(CANCEL_INTENT.search(text or ""))


#: The few sentences the *server* says during a booking, as opposed to the model.
#: These fire at exactly the moments the model must not be trusted to narrate —
#: a confirmed booking, a lost room, a dropped draft.
TEXT = {
    "en": {
        "confirmed": "Your booking is confirmed. The reference is {code}. "
        "Please show it at the desk when you arrive.",
        "taken": "I am sorry — that room was taken while we were talking. "
        "Shall I look for another one for you?",
        "dropped": "No problem, I have cancelled that. Anything else I can help with?",
        "failed": "I could not complete that booking. Let me get a colleague to finish it "
        "with you.",
    },
    "bn": {
        "confirmed": "আপনার বুকিং কনফার্ম হলো। বুকিং নম্বর {code}। পৌঁছে রিসেপশনে এই নম্বরটি দেখাবেন।",
        "taken": "দুঃখিত — কথা বলতে বলতেই রুমটি বুক হয়ে গেছে। আপনার জন্য আরেকটি রুম দেখব কি?",
        "dropped": "ঠিক আছে, বুকিংটি বাতিল করলাম। আর কিছু লাগবে?",
        "failed": "বুকিংটি সম্পন্ন করতে পারলাম না। একজন সহকর্মীকে ডেকে দিচ্ছি, তিনি শেষ করে দেবেন।",
    },
}


def say(key: str, language: str = "en", **fmt) -> str:
    lang = BN if (language or "").startswith(BN) else "en"
    return TEXT[lang][key].format(**fmt)


# ==============================================================================
# Live snapshot
# ==============================================================================


@dataclass(frozen=True)
class RoomOffer:
    code: str
    name: str
    max_occupancy: int
    view: str
    available: int
    total_price: Decimal
    nightly: Decimal
    currency: str
    room_type_id: str


def room_snapshot(
    hotel: Hotel,
    check_in: date | None = None,
    nights: int = 1,
    rooms: int = 1,
) -> list[RoomOffer]:
    """Every sellable room type for these dates, priced and counted.

    This is the booking equivalent of handing a POS agent today's menu with live
    stock: the model is never asked to remember what a room costs, and cannot
    invent one, because the real number is already in front of it.
    """
    from services.booking import availability
    from services.rooms import pricing

    start = check_in or timezone.localdate()
    end = start + timedelta(days=max(1, nights))

    offers: list[RoomOffer] = []
    for row in availability.by_type(hotel, start, end):
        room_type = row.room_type
        try:
            quote = pricing.quote(
                hotel=hotel,
                room_type=room_type,
                check_in=start,
                check_out=end,
                adults=room_type.base_occupancy,
                rooms=max(1, rooms),
            )
            total, nightly = quote.grand_total, quote.average_nightly
        except Exception:  # noqa: BLE001 - a pricing gap must not blank the menu
            logger.warning("could not price %s", room_type.code, exc_info=True)
            total, nightly = room_type.base_rate, room_type.base_rate

        offers.append(
            RoomOffer(
                code=room_type.code,
                name=room_type.name,
                max_occupancy=room_type.max_occupancy,
                view=room_type.view or "",
                available=row.available,
                total_price=total,
                nightly=nightly,
                currency=hotel.currency,
                room_type_id=str(room_type.pk),
            )
        )
    return offers


def _snapshot_json(offers: list[RoomOffer]) -> str:
    """Slim rows for the prompt. Fewer tokens in, faster answer out."""
    return json.dumps(
        [
            {
                "code": o.code,
                "name": o.name,
                "sleeps": o.max_occupancy,
                "view": o.view,
                "free": o.available,
                "total": float(o.total_price),
                "per_night": float(o.nightly),
            }
            for o in offers
        ],
        ensure_ascii=False,
    )


# ==============================================================================
# Prompt
# ==============================================================================

_SYSTEM_BN = """তুমি "{hotel}"-এর ডিজিটাল রিসেপশনিস্ট। শুদ্ধ, ভদ্র, সাবলীল বাংলায় কথা বলো — \
একজন অভিজ্ঞ রিসেপশন কর্মীর মতো উষ্ণ ও পেশাদার, যন্ত্রের মতো নয়।
গ্রাহককে "স্যার/ম্যাডাম" সম্বোধন করো। ধৈর্য ধরো, তাড়া দিও না, তর্ক করো না।

আজকের তারিখ: {today}
{stay_line}

এই তারিখে খালি রুম ও দাম (JSON — এটাই একমাত্র সত্য):
{rooms_json}

হোটেলের যাচাই করা তথ্য (এর বাইরে কিছু বলবে না):
{hotel_facts}

── কার্যপ্রণালি ──
1. প্রতিটি উত্তরে শুধু একটি বৈধ JSON object দেবে। কোনো markdown, কোড-ফেন্স (```) বা বাড়তি লেখা নয়।
2. `booking` সবসময় এখন পর্যন্ত জানা **পুরো** তথ্য — আগের টার্নের তথ্যসহ। আগেরটা ভুলে যাবে না।
3. একবারে একটাই জিনিস জিজ্ঞেস করো, ছোট বাক্যে। গ্রাহক এক বাক্যে অনেক কিছু বললে (যেমন \
"কাল থেকে দুই রাত, দুইজন") সবটা একসাথে বুঝে নাও — একটা একটা করে জিজ্ঞেস করে সময় নষ্ট করবে না।
4. যা যা লাগবে: চেক-ইনের তারিখ, কয় রাত, কয়জন অতিথি, কোন ধরনের রুম, নাম, মোবাইল নম্বর।
   এর কোনোটি বাকি থাকলে `needs_more_info` = true এবং `ready_to_confirm` = false।
5. রুম নির্বাচন করবে উপরের JSON-এর `code` দিয়ে। JSON-এ নেই এমন কোনো code লিখবে না।
   `free` শূন্য হলে ওই রুম দেওয়া যাবে না — দুঃখ প্রকাশ করে খালি আছে এমন একটি বিকল্প বলো।
   `sleeps` সংখ্যার বেশি অতিথি হলে বেশি রুম বা বড় রুম সাজেস্ট করো।
6. তারিখ সবসময় YYYY-MM-DD আকারে দেবে। "আজ", "কাল", "শুক্রবার" — আজকের তারিখ ধরে হিসাব করো।
7. দাম কখনো নিজে বানাবে না। উপরের JSON-এর `total` ছাড়া অন্য কোনো অঙ্ক বলবে না।
   দাম নিয়ে নিশ্চিত না হলে দাম বলা এড়িয়ে যাও।
7ক. ঠিকানা, ভ্যাট, চেক-ইন/আউটের সময় — এসব শুধু উপরের "যাচাই করা তথ্য" থেকে বলবে।
   ওখানে না থাকলে সোজা বলো তুমি জানো না এবং একজন কর্মীকে ডেকে দিতে চাও।
   কখনো কোনো ঠিকানা, শতাংশ বা সময় অনুমান করে বলবে না।
8. সব তথ্য পাওয়ার পর সংক্ষেপে যোগফল বলো — তারিখ, কয় রাত, রুম, মোট টাকা — এবং কনফার্ম করতে \
বলো। তখনো `ready_to_confirm` = false।
9. গ্রাহক স্পষ্টভাবে রাজি হলে ("কনফার্ম", "হ্যাঁ করে দিন", "ঠিক আছে বুক করুন") তখনই \
`ready_to_confirm` = true — এবং কেবল তখনই, যখন উপরের সবকিছু জানা আছে।
10. গ্রাহক বাতিল করতে চাইলে বিনয়ের সাথে মেনে নাও, `cancelled` = true দাও।
11. উত্তর এক-দুই বাক্যে রাখো — এটি ভয়েসে পড়ে শোনানো হবে। সংখ্যা শব্দে বলো \
("দুই রাত", "সাত হাজার টাকা")। কোনো ইমোজি বা তালিকা-চিহ্ন নয়।

── JSON স্কিমা ──
{{"reply": "বাংলায় ছোট উত্তর",
  "booking": {{"check_in": "YYYY-MM-DD বা null", "nights": সংখ্যা বা null,
    "room_code": "কোড বা null", "rooms": সংখ্যা, "adults": সংখ্যা, "children": সংখ্যা,
    "guest_name": "নাম বা খালি", "guest_phone": "নম্বর বা খালি"}},
  "needs_more_info": true/false, "ready_to_confirm": true/false, "cancelled": false}}
"""

_SYSTEM_EN = """You are the digital receptionist at "{hotel}". Warm, professional, \
unhurried — like an experienced front-desk agent, not a machine.

Today's date: {today}
{stay_line}

Rooms free on these dates, with prices (JSON — this is the only source of truth):
{rooms_json}

Verified hotel information (say nothing beyond this):
{hotel_facts}

── How to work ──
1. Every reply is a single valid JSON object. No markdown, no code fences, no extra text.
2. `booking` always carries the **complete** picture so far, including earlier turns. \
Never drop something the guest already told you.
3. Ask for one thing at a time, briefly. If the guest gives several facts in one \
sentence ("two nights from tomorrow, two of us"), take them all at once.
4. You need: check-in date, number of nights, number of guests, room type, name, mobile \
number. While any is missing, `needs_more_info` = true and `ready_to_confirm` = false.
5. Choose a room by the `code` in the JSON above. Never invent a code that is not there. \
If `free` is 0 that room cannot be sold — apologise and offer one that is free. If the \
party is larger than `sleeps`, suggest more rooms or a bigger type.
6. Dates always as YYYY-MM-DD. Resolve "today", "tomorrow", "Friday" against today's date.
7. Never invent a price. Quote only the `total` from the JSON above; if unsure, do not \
mention a price at all.
7a. Address, VAT, check-in and check-out times come ONLY from the verified hotel \
information above. If something is not there, say plainly that you do not know and offer \
to fetch a staff member. Never guess an address, a percentage or a time — a guest who is \
mid-booking still asks ordinary questions, and a wrong answer to one of those is a \
promise the hotel has to keep.
8. Once everything is known, summarise briefly — dates, nights, room, total — and ask the \
guest to confirm. `ready_to_confirm` stays false at that point.
9. Only when the guest clearly agrees ("confirm", "yes please", "go ahead") set \
`ready_to_confirm` = true, and only if everything above is known.
10. If the guest backs out, accept it politely and set `cancelled` = true.
11. Keep replies to one or two sentences — they are read aloud. No emoji, no bullet points.

── JSON schema ──
{{"reply": "short reply",
  "booking": {{"check_in": "YYYY-MM-DD or null", "nights": number or null,
    "room_code": "code or null", "rooms": number, "adults": number, "children": number,
    "guest_name": "name or empty", "guest_phone": "number or empty"}},
  "needs_more_info": true/false, "ready_to_confirm": true/false, "cancelled": false}}
"""


def _hotel_facts_block(hotel: Hotel, language: str) -> str:
    """The hotel's own policies, for the booking prompt.

    Without this the booking model has only the room list, so a guest who asks
    about VAT or the address mid-booking gets an invented answer — and it was
    inventing them: a hotel on Gulshan Avenue was placed "beside Beach Road", and
    a bill with 15% VAT added was described as tax-inclusive. Neither carried a
    citation, because there was nothing to cite.
    """
    from services.reception import context as ctx

    facts = ctx.hotel_facts(hotel, language)
    return "\n".join(f"- {fact.text}" for fact in facts)


def _system_prompt(hotel: Hotel, language: str, offers: list[RoomOffer], draft: dict) -> str:
    bn = language.startswith(BN)
    today = timezone.localdate()
    check_in = draft.get("check_in")
    nights = draft.get("nights")

    if check_in and nights:
        stay_line = (
            f"এই দামগুলো {check_in} থেকে {nights} রাতের জন্য।"
            if bn
            else f"These prices are for {nights} night(s) from {check_in}."
        )
    else:
        stay_line = (
            "তারিখ এখনো জানা যায়নি — নিচের দাম আজ রাতের এক রাতের।"
            if bn
            else "No dates yet — the prices below are for one night tonight."
        )

    template = _SYSTEM_BN if bn else _SYSTEM_EN
    return template.format(
        hotel=hotel.name,
        today=today.isoformat(),
        stay_line=stay_line,
        rooms_json=_snapshot_json(offers),
        hotel_facts=_hotel_facts_block(hotel, language),
    )


# ==============================================================================
# One turn
# ==============================================================================


@dataclass
class Draft:
    """The validated result of one booking turn."""

    reply: str
    booking: dict[str, Any] = field(default_factory=dict)
    offers: list[RoomOffer] = field(default_factory=list)
    quote_total: Decimal | None = None
    currency: str = ""
    needs_more_info: bool = True
    ready_to_confirm: bool = False
    cancelled: bool = False
    issues: list[str] = field(default_factory=list)
    latency_ms: int = 0

    @property
    def is_complete(self) -> bool:
        b = self.booking
        return bool(
            b.get("check_in")
            and b.get("nights")
            and b.get("room_code")
            and b.get("guest_name")
            and b.get("guest_phone")
        )


#: Room cards the kiosk will picture at once. A property with eleven room types
#: should not turn the lobby screen into a catalogue the guest has to scroll.
GALLERY_ROOMS = 4


def room_cards(draft: Draft, *, limit: int = GALLERY_ROOMS) -> list[dict[str, Any]]:
    """The rooms to show pictures of, for this point in the conversation.

    Once the guest has settled on a room type, that one alone — a gallery still
    offering three alternatives beside the room they just chose reads as though
    the choice did not register. Before that, the shortlist they are choosing
    from, so "which one is that" is answerable by looking rather than by asking.

    Built from ``draft.offers``, which is the priced-and-counted snapshot the
    server made this turn. Nothing here comes from what the model wrote, so the
    kiosk cannot picture a room type the hotel does not sell.
    """
    from apps.rooms.models import RoomType
    from services.rooms import media

    chosen = str(draft.booking.get("room_code") or "").strip()
    offers = [o for o in draft.offers if o.code == chosen] or list(draft.offers)[:limit]
    if not offers:
        return []

    ids = [o.room_type_id for o in offers]
    photos = media.gallery(ids)
    types = {
        str(rt.pk): rt
        for rt in RoomType.all_objects.filter(pk__in=ids, is_deleted=False).only(
            "code", "name", "view", "bed_type", "max_occupancy", "size_sqm"
        )
    }

    cards: list[dict[str, Any]] = []
    for offer in offers:
        room_type = types.get(offer.room_type_id)
        card = (
            media.describe(room_type, photos.get(offer.room_type_id, []))
            if room_type is not None
            # Deleted between the snapshot and here. The offer still carries
            # enough to label a card, and dropping it silently would leave the
            # guest looking at a gallery missing the room they just asked about.
            else {
                "code": offer.code,
                "name": offer.name,
                "view": offer.view,
                "bed": "",
                "sleeps": offer.max_occupancy,
                "size_sqm": None,
                "photos": photos.get(offer.room_type_id, []),
            }
        )
        card["chosen"] = bool(chosen) and offer.code == chosen
        cards.append(card)
    return cards


def run_turn(conversation: Conversation, text: str, *, hotel: Hotel | None = None) -> Draft:
    """One booking exchange: prompt the model, then disbelieve it politely."""
    hotel = hotel or conversation.tenant
    if hotel is None:  # pragma: no cover - guarded by the caller
        raise ValidationError("A booking needs a hotel.")

    language = conversation.language or hotel.kiosk_language
    draft = dict(conversation.booking_draft or {})

    # Price the snapshot for the dates already agreed, so the model quotes the
    # stay it is actually selling rather than tonight's rate.
    offers = room_snapshot(
        hotel,
        check_in=_as_date(draft.get("check_in")),
        nights=int(draft.get("nights") or 1),
        rooms=int(draft.get("rooms") or 1),
    )

    messages = [
        ChatMessage(Role.SYSTEM, _system_prompt(hotel, language, offers, draft)),
        *_history(conversation),
    ]
    if draft:
        # State the server is holding, in the same shape the model must return.
        # Without this a model that loses the thread silently drops a field the
        # guest already gave, and the kiosk asks for it twice.
        messages.append(
            ChatMessage(
                Role.USER,
                "KNOWN SO FAR (carry all of it forward): " + json.dumps(draft, ensure_ascii=False),
            )
        )
    messages.append(ChatMessage(Role.USER, text))

    result = gateway.chat(
        messages,
        module="reception_booking",
        tenant_id=str(hotel.pk),
        conversation_id=str(conversation.pk),
    )
    raw = _parse_json(result.text)
    out = _finalize(raw, hotel=hotel, offers=offers, previous=draft, language=language)
    out.latency_ms = result.latency_ms
    return out


def _history(conversation: Conversation) -> list[ChatMessage]:
    from apps.reception.models import MessageRole

    rows = list(
        conversation.messages.filter(role__in=[MessageRole.GUEST, MessageRole.ASSISTANT]).order_by(
            "-created_at"
        )[: HISTORY_TURNS * 2]
    )[::-1]
    # The turn's own guest message is recorded before this runs; it is appended
    # explicitly below, so drop it here rather than sending it twice.
    if rows and rows[-1].role == MessageRole.GUEST:
        rows = rows[:-1]
    return [
        ChatMessage(Role.USER if r.role == MessageRole.GUEST else Role.ASSISTANT, r.content)
        for r in rows
    ]


def _parse_json(text: str) -> dict[str, Any]:
    """Pull one JSON object out of whatever the model returned.

    Models fence their JSON, prefix it with "Sure!", or trail a sentence after
    it. None of that is worth failing a guest over, so the object is extracted
    rather than demanded. A genuine non-JSON reply raises.
    """
    body = (text or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body).strip()
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        pass

    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(body[start : end + 1])
        except (ValueError, TypeError):
            pass

    logger.error("booking agent returned non-JSON: %s", body[:400])
    raise ValidationError("The booking assistant returned an unreadable answer.")


# ==============================================================================
# Validation — where the model stops being trusted
# ==============================================================================


def _finalize(
    raw: dict[str, Any],
    *,
    hotel: Hotel,
    offers: list[RoomOffer],
    previous: dict[str, Any],
    language: str,
) -> Draft:
    bn = language.startswith(BN)
    said = raw.get("booking") or {}
    issues: list[str] = []

    # Merge, do not replace. A model that omits a field it was told about must
    # not be able to erase it — the guest said it once and should not be asked
    # again because the model had a lapse.
    merged: dict[str, Any] = {**previous}
    for key in (
        "check_in",
        "nights",
        "room_code",
        "rooms",
        "adults",
        "children",
        "guest_name",
        "guest_phone",
    ):
        value = said.get(key)
        if value not in (None, "", []):
            merged[key] = value

    today = timezone.localdate()

    check_in = _as_date(merged.get("check_in"))
    if check_in is None:
        merged.pop("check_in", None)
    elif check_in < today:
        # Almost always a year the model carried over from a previous turn.
        issues.append("সেই তারিখ পেরিয়ে গেছে" if bn else "that date has already passed")
        merged.pop("check_in", None)
        check_in = None
    elif check_in > today + timedelta(days=MAX_ADVANCE_DAYS):
        issues.append("এত আগে বুকিং নেওয়া যায় না" if bn else "that is too far ahead to book here")
        merged.pop("check_in", None)
        check_in = None
    else:
        merged["check_in"] = check_in.isoformat()

    nights = _as_int(merged.get("nights"))
    if nights is not None and not 1 <= nights <= MAX_NIGHTS:
        issues.append(f"সর্বোচ্চ {MAX_NIGHTS} রাত" if bn else f"a maximum of {MAX_NIGHTS} nights")
        nights = min(max(nights, 1), MAX_NIGHTS)
    if nights is None:
        merged.pop("nights", None)
    else:
        merged["nights"] = nights

    merged["rooms"] = min(max(_as_int(merged.get("rooms")) or 1, 1), MAX_ROOMS)
    merged["adults"] = max(_as_int(merged.get("adults")) or 1, 1)
    merged["children"] = max(_as_int(merged.get("children")) or 0, 0)
    merged["guest_name"] = str(merged.get("guest_name") or "").strip()[:150]
    merged["guest_phone"] = _clean_phone(merged.get("guest_phone"))

    # --- room type: must exist, must be free, must fit the party --------------
    by_code = {o.code.upper(): o for o in offers}
    code = str(merged.get("room_code") or "").strip().upper()
    offer = by_code.get(code)

    if code and offer is None:
        # A hallucinated code. Drop it rather than 500 at confirm time.
        issues.append("ওই ধরনের রুম আমাদের নেই" if bn else "we do not have that room type")
        merged.pop("room_code", None)
        offer = None
    elif offer is not None:
        wanted = merged["rooms"]
        if merged["adults"] + merged["children"] > offer.max_occupancy * wanted:
            issues.append(
                f"{offer.name} সর্বোচ্চ {offer.max_occupancy} জনের"
                if bn
                else f"{offer.name} sleeps {offer.max_occupancy}"
            )
            merged.pop("room_code", None)
            offer = None
        else:
            # Count for the dates the guest just named, not the dates the
            # snapshot was built for. The model routinely names a date and a
            # room in the same breath, and checking tonight's stock against next
            # week's stay is how a sold-out room gets drafted.
            free = (
                _free_count(hotel, offer, check_in, nights)
                if check_in and nights
                else offer.available
            )
            if free < wanted:
                issues.append(
                    f"{offer.name} ওই তারিখে {free} টি খালি"
                    if bn
                    else f"only {free} {offer.name} free on those dates"
                )
                merged.pop("room_code", None)
                offer = None

    # --- price: recomputed, never taken from the model ------------------------
    total = None
    if offer is not None and check_in and nights:
        total = _repricing(hotel, offer, check_in, nights, merged)

    complete = bool(
        merged.get("check_in")
        and merged.get("nights")
        and merged.get("room_code")
        and merged.get("guest_name")
        and merged.get("guest_phone")
    )
    ready = bool(raw.get("ready_to_confirm")) and complete and not issues

    return Draft(
        reply=str(raw.get("reply") or "").strip(),
        booking=merged,
        offers=offers,
        quote_total=total,
        currency=hotel.currency,
        needs_more_info=bool(raw.get("needs_more_info")) or not complete,
        ready_to_confirm=ready,
        cancelled=bool(raw.get("cancelled")),
        issues=issues,
    )


def _free_count(hotel, offer: RoomOffer, check_in: date, nights: int) -> int:
    from apps.rooms.models import RoomType
    from services.booking import availability

    check_out = check_in + timedelta(days=nights)
    room_type = RoomType.all_objects.filter(pk=offer.room_type_id, is_deleted=False).first()
    if room_type is None:  # pragma: no cover - deleted mid-conversation
        return 0
    try:
        return availability.available_rooms(hotel, check_in, check_out, room_type=room_type).count()
    except Exception:  # noqa: BLE001 - never turn a stock query into a guest-facing crash
        logger.warning("availability re-check failed for %s", offer.code, exc_info=True)
        return 0


def _repricing(
    hotel, offer: RoomOffer, check_in: date, nights: int, merged: dict
) -> Decimal | None:
    from apps.rooms.models import RoomType
    from services.rooms import pricing

    room_type = RoomType.all_objects.filter(pk=offer.room_type_id, is_deleted=False).first()
    if room_type is None:  # pragma: no cover - deleted mid-conversation
        return None
    try:
        quote = pricing.quote(
            hotel=hotel,
            room_type=room_type,
            check_in=check_in,
            check_out=check_in + timedelta(days=nights),
            adults=merged["adults"],
            rooms=merged["rooms"],
        )
    except Exception:  # noqa: BLE001 - a quote failure is not a guest-facing crash
        logger.warning("re-quote failed for %s", offer.code, exc_info=True)
        return None
    return quote.grand_total


# ==============================================================================
# Writing the booking
# ==============================================================================


def confirm(conversation: Conversation, *, user=None) -> Reservation:
    """Turn the agreed draft into a real held reservation.

    Re-validated from scratch: whatever the model said two seconds ago, the room
    may have gone since. ``reservations.create`` re-checks availability inside a
    transaction and the exclusion constraint is the final arbiter, so the worst
    case is a clean ``Conflict`` rather than a double-booked room.

    No money moves here. A confirmed booking with an open folio is the whole of
    what the AI is allowed to do (goal.txt D11); payment is a human action at
    the desk.
    """
    from apps.rooms.models import RoomType
    from services.booking import reservations

    hotel = conversation.tenant
    draft = dict(conversation.booking_draft or {})
    if hotel is None:
        raise ValidationError("A booking needs a hotel.")

    check_in = _as_date(draft.get("check_in"))
    nights = _as_int(draft.get("nights"))
    code = str(draft.get("room_code") or "").strip()
    name = str(draft.get("guest_name") or "").strip()
    phone = _clean_phone(draft.get("guest_phone"))

    missing = [
        label
        for label, value in (
            ("check-in date", check_in),
            ("number of nights", nights),
            ("room type", code),
            ("guest name", name),
            ("phone number", phone),
        )
        if not value
    ]
    if missing:
        raise ValidationError(f"Cannot confirm yet — still missing: {', '.join(missing)}.")

    room_type = RoomType.all_objects.filter(
        tenant=hotel, code__iexact=code, is_deleted=False
    ).first()
    if room_type is None:
        raise ValidationError(f"Room type {code} no longer exists.")

    guest = _guest_for(hotel, name, phone, conversation.language)

    reservation = reservations.create(
        hotel=hotel,
        guest=guest,
        check_in=check_in,
        check_out=check_in + timedelta(days=nights),
        room_type=room_type,
        rooms=int(draft.get("rooms") or 1),
        adults=int(draft.get("adults") or 1),
        children=int(draft.get("children") or 0),
        source=_source_for(conversation),
        special_requests=str(draft.get("notes") or "")[:500],
        user=user,
        conversation=conversation,
    )

    conversation.booking_draft = {}
    conversation.save(update_fields=["booking_draft", "updated_at"])
    return reservation


def _source_for(conversation) -> str:
    """Which front door this booking came through.

    It was hardcoded to KIOSK, which was true when the kiosk was the only place
    the assistant lived. The same agent now answers on the public booking page, and
    a manager asking what the website brought in should not be told it was a lobby
    terminal.
    """
    from apps.booking.models import BookingSource
    from apps.reception.models import Channel

    return {
        Channel.KIOSK: BookingSource.KIOSK,
        Channel.WEBSITE: BookingSource.WEBSITE,
        Channel.PWA: BookingSource.WEBSITE,
        Channel.PHONE: BookingSource.PHONE,
    }.get(conversation.channel, BookingSource.WALK_IN)


def _guest_for(hotel, name: str, phone: str, language: str):
    """Reuse the guest record if this phone has stayed before.

    Matching on phone rather than name: two Rahmans is normal, two Rahmans on
    the same mobile is the same person. Creating a duplicate would split their
    stay history and quietly break loyalty tier and spend totals.
    """
    from apps.guests.models import Guest

    existing = Guest.all_objects.filter(tenant=hotel, phone=phone, is_deleted=False).first()
    if existing is not None:
        return existing

    first, _, last = name.partition(" ")
    return Guest.objects.create(
        tenant=hotel,
        first_name=first[:80] or name[:80],
        last_name=last[:80],
        phone=phone,
        language=language or "en",
    )


# ==============================================================================
# Small helpers
# ==============================================================================


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_phone(value) -> str:
    """Keep digits and a leading +. Voice input arrives as "০১৭..." or
    "zero one seven"; the digits are what matters for matching a returning
    guest."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Bengali digits, which a Bangla STT pass emits verbatim.
    raw = raw.translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
    plus = "+" if raw.startswith("+") else ""
    digits = re.sub(r"\D", "", raw)
    return (plus + digits)[:32]
