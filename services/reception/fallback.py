"""Deterministic answers, drawn straight from the database.

This is the path that runs when no LLM is reachable — no key yet, kill switch
on, budget cap tripped, or the internet down, which for a hotel in Cox's Bazar
is a Tuesday (goal.txt D12, D13). It also runs when a provider is failing, so a
wrong API key degrades the concierge instead of breaking it.

It matches a question to a *topic*, then reads the answer out of the live fact
set that ``context`` assembled from the hotel record and today's room
availability. It cannot invent a price because it has no way to produce one:
every number it says came from a query.

Bangla and English both, because "রুম আছে?" and "do you have a room?" are the
same question and a guest should not have to know which one the kiosk speaks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from services.reception.context import Fact

BN = "bn"


@dataclass(frozen=True)
class Rule:
    """One question shape, and which fact topics answer it."""

    name: str
    pattern: re.Pattern[str]
    topics: tuple[str, ...]


#: Patterns carry Bangla and English alternatives in one expression, so a guest
#: mixing the two mid-sentence — which is normal here — still matches.
RULES: tuple[Rule, ...] = (
    Rule(
        "rooms_available",
        re.compile(
            r"(রুম\s*(আছে|খালি|পাওয়া|লাগবে|দরকার|চাই)|খালি\s*রুম|থাকার\s*ব্যবস্থা|সিট\s*আছে"
            r"|room available|any rooms?|free rooms?|vacan|need a room|want a room"
            r"|book a room|do you have (a )?room)",
            re.I,
        ),
        ("room_type", "availability"),
    ),
    Rule(
        "room_price",
        re.compile(
            r"(রুম.*(দাম|ভাড়া|খরচ|রেট|কত)|(দাম|ভাড়া|রেট).*(রুম|কত)|কত\s*টাকা"
            r"|room (rate|price|cost|charge)|how much.*(room|night|stay)|price list|tariff)",
            re.I,
        ),
        ("room_type",),
    ),
    Rule(
        "room_types",
        re.compile(
            r"(কি\s*ধরনের\s*রুম|কী\s*ধরনের\s*রুম|রুমের\s*ধরন|কি\s*কি\s*রুম|স্যুট|ডিলাক্স"
            r"|what (kind|type)s? of room|room types?|suite|deluxe)",
            re.I,
        ),
        ("room_type",),
    ),
    Rule(
        "checkout",
        re.compile(
            r"(চেক\s*আউট|চেকআউট|কখন\s*ছাড়|রুম\s*ছাড়|check[\s-]?out|checkout|vacate|departure)",
            re.I,
        ),
        ("checkout",),
    ),
    Rule(
        "checkin",
        re.compile(r"(চেক\s*ইন|চেকইন|কখন\s*ঢুক|কখন\s*উঠ|check[\s-]?in|checkin|arriv)", re.I),
        ("checkin",),
    ),
    Rule(
        "rooms_count",
        re.compile(
            r"(কয়টা\s*রুম|কতগুলো\s*রুম|মোট\s*রুম|how many rooms|number of rooms|total rooms)",
            re.I,
        ),
        ("rooms_count",),
    ),
    Rule(
        "tax",
        # NOT a bare "কর". Bangla has no regex word boundary, so "কর" matches
        # inside "করে", "করবেন", "করা" — every second sentence — and a guest
        # asking to arrange a helicopter got quoted the VAT rate.
        re.compile(
            r"(ভ্যাট|ট্যাক্স|করের\s*হার|সার্ভিস\s*চার্জ|vat|tax|service charge|surcharge)",
            re.I,
        ),
        ("tax",),
    ),
    Rule(
        "currency",
        re.compile(r"(কোন\s*মুদ্রা|ডলার|currency|which money)", re.I),
        ("currency",),
    ),
    # Payment, before "currency" would have swallowed it: "টাকায়" and "pay in"
    # matched the currency rule, so "টাকা কি সেখানে গিয়ে দেবো?" answered "prices
    # are quoted in BDT" — true, and not the question.
    #
    # Ordered above address/phone too, because "বিল কোথায় দেবো" contains "কোথায়".
    Rule(
        "payment",
        re.compile(
            r"(বিল|পেমেন্ট|পরিশোধ|টাকা\s*(দিব|দেব|দিতে|দেওয়া|পাঠা)|অগ্রিম|অ্যাডভান্স"
            r"|বিকাশ|নগদে|কার্ডে|ক্যাশ|ভাড়া\s*(দিব|দেব|দিতে)"
            r"|payment|pay(ing|ment)?\b|how (do|can) i pay|advance|deposit|prepay"
            r"|bkash|nagad|credit card|debit card|cash)",
            re.I,
        ),
        ("payment",),
    ),
    Rule(
        "address",
        re.compile(
            r"(ঠিকানা|কোথায়|অবস্থান|লোকেশন|কিভাবে\s*যাব|address|where are you|location|directions)",
            re.I,
        ),
        ("address",),
    ),
    Rule(
        "phone",
        re.compile(r"(ফোন|নম্বর|নাম্বার|কল|phone|telephone|contact number|call you)", re.I),
        ("phone",),
    ),
    Rule("email", re.compile(r"(ইমেইল|মেইল|e-?mail)", re.I), ("email",)),
    Rule("website", re.compile(r"(ওয়েবসাইট|সাইট|website|web site|online)", re.I), ("website",)),
    Rule(
        "identity",
        re.compile(
            r"(হোটেলের\s*নাম|কোন\s*হোটেল|কয়\s*তারকা"
            r"|which hotel|what hotel|star rating|how many stars)",
            re.I,
        ),
        ("identity",),
    ),
    Rule(
        "staff",
        re.compile(
            r"(রিসেপশন|কেউ\s*আছে|লোক\s*আছে|কর্মী|reception desk|is anyone|someone there|staff)",
            re.I,
        ),
        ("staff",),
    ),
)

GREETING = re.compile(r"^\s*(hi|hello|hey|সালাম|আসসালাম|assalam|নমস্কার|হ্যালো|হাই|শুভ)", re.I)
THANKS = re.compile(r"(thank|thanks|ধন্যবাদ|শুকরিয়া)", re.I)

TEXT = {
    "en": {
        "greeting": "Hello! How can I help you?",
        "thanks": "You are very welcome. Anything else?",
        "unavailable": (
            "I can only answer a few basic questions at the moment, and that is not one "
            "of them. Let me fetch a member of staff for you."
        ),
    },
    "bn": {
        "greeting": "জি, বলুন। আমি কীভাবে সাহায্য করতে পারি?",
        "thanks": "আপনাকেও ধন্যবাদ। আর কিছু লাগবে?",
        "unavailable": (
            "দুঃখিত, এই মুহূর্তে আমি শুধু কিছু সাধারণ তথ্য দিতে পারছি। "
            "আমি একজন কর্মীকে ডেকে দিচ্ছি, তিনি আপনাকে সাহায্য করবেন।"
        ),
    },
}


@dataclass
class Answer:
    text: str
    citations: list[dict[str, str]]
    confidence: float


def answer(facts: list[Fact], question: str, language: str = "en") -> Answer | None:
    """Best deterministic answer, or None if a human is needed."""
    lang = BN if language.startswith(BN) else "en"
    strings = TEXT[lang]

    if GREETING.search(question):
        return Answer(strings["greeting"], [], 0.9)
    if THANKS.search(question):
        return Answer(strings["thanks"], [], 0.9)

    for rule in RULES:
        if not rule.pattern.search(question):
            continue
        matched = _facts_for(facts, rule.topics)
        if matched:
            return Answer(_render(matched), _cite(matched), 0.75)

    return None


def unavailable_notice(language: str = "en") -> str:
    """What the kiosk says when it genuinely cannot answer offline."""
    return TEXT[BN if language.startswith(BN) else "en"]["unavailable"]


def _facts_for(facts: list[Fact], topics: tuple[str, ...]) -> list[tuple[int, Fact]]:
    wanted = set(topics)
    return [(index, fact) for index, fact in enumerate(facts, 1) if fact.topic in wanted]


def _cite(matched: list[tuple[int, Fact]]) -> list[dict[str, str]]:
    return [
        {"index": str(index), "text": fact.text, "source": fact.source} for index, fact in matched
    ]


def _render(matched: list[tuple[int, Fact]]) -> str:
    # Same [n] citation markers the LLM path produces, so the UI renders both
    # identically. Both are sourced; that is the point.
    return " ".join(f"{fact.text} [{index}]" for index, fact in matched)
