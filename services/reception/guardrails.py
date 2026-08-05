"""Pre- and post-checks around every AI turn.

The AI is not trusted to police itself. Each check below exists because of a
specific way an unguarded hotel concierge fails:

* a guest asks for medical or legal advice and gets it
* a guest repeats the same question because the answer was useless, and the bot
  cheerfully repeats itself forever
* an unbounded conversation quietly burns the day's token budget
* an answer with no source is indistinguishable from an invented one
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings

from apps.reception.models import HandoffReason

if TYPE_CHECKING:  # pragma: no cover
    from apps.ai_center.models import SafetyPolicy
    from apps.reception.models import Conversation

# Phrases that mean "stop talking to the robot". Matched loosely on purpose:
# a guest who wants a person should never have to phrase it correctly, in
# either language.
HUMAN_REQUEST = re.compile(
    r"\b(human|person|staff|manager|receptionist|real (?:person|human)|"
    r"talk to someone|speak to someone|agent)\b"
    r"|(মানুষের\s*সাথে|মানুষ\s*চাই|একজন\s*মানুষ|কর্মীর\s*সাথে|স্টাফের\s*সাথে"
    r"|ম্যানেজার|রিসেপশনিস্ট|আসল\s*মানুষ|কারো\s*সাথে\s*কথা)",
    re.IGNORECASE,
)

# "I don't know", in the shapes the prompt asks for — in both languages.
#
# This is not cosmetic. The model was saying, in Bangla, "I don't know, I am
# connecting you to a staff member" while no handoff was actually queued: the
# guest was promised a person who was never called. An English-only pattern on
# a Bangla kiosk is a broken promise, not a missing translation.
NON_ANSWER = re.compile(
    r"(don'?t have that information|do not have that information|"
    r"i don'?t know|cannot help with that|unable to answer)"
    r"|(জানি\s*না|জানা\s*নেই|তথ্য\s*নেই|বলতে\s*পারছি\s*না|সাহায্য\s*করতে\s*পারছি\s*না"
    r"|কর্মীকে\s*(ডেকে|যুক্ত|জানিয়ে)|কর্মী\s*আপনাকে\s*সাহায্য"
    r"|একজন\s*(মানব\s*)?কর্মী)",
    re.IGNORECASE,
)


# An answer that says somebody is being brought in.
#
# Deliberately separate from NON_ANSWER, and deliberately wider. NON_ANSWER asks
# "did the model dodge the question", and on a staffed channel the answer to that
# is a handoff — fine. This asks a different question: "did the model just promise
# a person?" On a channel with no staff behind it that sentence is false however
# well the rest of the answer was sourced, and it has to be caught on its own.
#
# It is wide because the promise was found in the wild in a shape NON_ANSWER did
# not have: "আমি একজন মানব স্টাফ সদস্যকে যুক্ত করছি" — স্টাফ, not কর্মী. The
# answer carried a citation, so it scored 0.8, no handoff was queued at all, and
# the guest on the booking page sat waiting for a member of staff nobody had told
# about. A pattern that only knows one word for "staff" is a pattern that catches
# the promise it was written for and no other.
PROMISES_HUMAN = re.compile(
    r"(connect(ing)? you (to|with)|transfer(ring)? you|put(ting)? you through"
    r"|(a|our) (member of )?(staff|colleague|receptionist|team member)\b[^.]{0,40}"
    r"(will|is going to|is on)|(get|fetch|call)(ting)? (a|one of our|someone)"
    r"|someone will (be with|contact|call) you)"
    r"|((স্টাফ|কর্মী|সহকর্মী|রিসেপশনিস্ট|লোক|প্রতিনিধি)[^।]{0,25}"
    r"(যুক্ত|ডেকে|ডাকছি|পাঠাচ্ছি|জানিয়ে|জানাচ্ছি|আসছেন|যোগাযোগ\s*করবেন|সাহায্য\s*করবেন))",
    re.IGNORECASE,
)


def promises_a_human(answer: str) -> bool:
    """Does this answer tell the guest that a person is coming?"""
    return bool(PROMISES_HUMAN.search(answer or ""))


@dataclass
class Verdict:
    allowed: bool
    handoff: bool = False
    reason: str = ""
    message: str = ""


#: Everything the guardrails can say to a guest, in every language the kiosk
#: opens in. Hardcoding English here meant a Bangla kiosk answered in Bangla
#: right up until something went wrong, and then switched language mid-sentence.
MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "empty": "Please say or type something first.",
        "too_long": "That message is too long for me. Could you shorten it?",
        "human": "Of course — I am calling a member of staff for you now.",
        "blocked": (
            "That is something our staff should help you with directly. I am letting them know."
        ),
        "turn_limit": (
            "We have been talking for a while — let me bring in a colleague who can help properly."
        ),
        "token_cap": "Let me hand you to a colleague so we can sort this out faster.",
        "repeated": "Let me get a colleague — I do not seem to be answering that well.",
        "error": "I did not catch that. Let me get a colleague to help you.",
        "ai_offline": (
            "Our digital receptionist is offline right now — a colleague will be with you."
        ),
        "handing_over": "A member of our team is on the way. Please take a seat.",
    },
    "bn": {
        "empty": "অনুগ্রহ করে কিছু বলুন বা লিখুন।",
        "too_long": "লেখাটি একটু বড় হয়ে গেছে। আরেকটু সংক্ষেপে বলবেন?",
        "human": "অবশ্যই — আমি এখনই একজন কর্মীকে ডেকে দিচ্ছি।",
        "blocked": "এ বিষয়ে আমাদের কর্মী সরাসরি সাহায্য করবেন। আমি তাঁদের জানিয়ে দিচ্ছি।",
        "turn_limit": ("আমরা অনেকক্ষণ কথা বলছি — একজন সহকর্মীকে ডাকছি, তিনি ভালোভাবে সাহায্য করতে পারবেন।"),
        "token_cap": "একজন সহকর্মীর কাছে দিচ্ছি, তাতে দ্রুত সমাধান হবে।",
        "repeated": "একজন সহকর্মীকে ডাকছি — মনে হচ্ছে আমি ঠিকভাবে উত্তর দিতে পারছি না।",
        "error": "দুঃখিত, বুঝতে পারিনি। একজন সহকর্মীকে ডেকে দিচ্ছি।",
        "ai_offline": "আমাদের ডিজিটাল রিসেপশন এই মুহূর্তে বন্ধ — একজন কর্মী আসছেন।",
        "handing_over": "আমাদের একজন কর্মী আসছেন। অনুগ্রহ করে একটু বসুন।",
    },
}


def say(key: str, language: str = "en") -> str:
    lang = "bn" if (language or "").startswith("bn") else "en"
    return MESSAGES[lang][key]


def policy_for(hotel) -> SafetyPolicy | None:
    from apps.ai_center.models import SafetyPolicy

    if hotel is None:
        return None
    return SafetyPolicy.all_objects.filter(tenant=hotel, is_deleted=False).first()


def _limit(policy, attr: str, fallback_key: str):
    if policy is not None:
        return getattr(policy, attr)
    return settings.AI[fallback_key]


def check_inbound(conversation: Conversation, text: str, policy=None) -> Verdict:
    """Run before the model is called. Cheap, deterministic, no tokens spent."""
    cleaned = text.strip()
    lang = getattr(conversation, "language", "en") or "en"

    if not cleaned:
        return Verdict(allowed=False, message=say("empty", lang))

    if len(cleaned) > 2000:
        return Verdict(allowed=False, message=say("too_long", lang))

    if HUMAN_REQUEST.search(cleaned):
        return Verdict(
            allowed=False,
            handoff=True,
            reason=HandoffReason.GUEST_REQUEST,
            message=say("human", lang),
        )

    blocked = list(getattr(policy, "blocked_topics", None) or [])
    lowered = cleaned.lower()
    for topic in blocked:
        if topic.lower() in lowered:
            return Verdict(
                allowed=False,
                handoff=True,
                reason=HandoffReason.BLOCKED_TOPIC,
                message=say("blocked", lang),
            )

    max_turns = _limit(policy, "max_conversation_turns", "MAX_CONVERSATION_TURNS")
    if conversation.turn_count >= max_turns:
        return Verdict(
            allowed=False,
            handoff=True,
            reason=HandoffReason.TURN_LIMIT,
            message=say("turn_limit", lang),
        )

    token_cap = _limit(policy, "session_token_cap", "SESSION_TOKEN_CAP")
    if conversation.total_tokens >= token_cap:
        return Verdict(
            allowed=False,
            handoff=True,
            reason=HandoffReason.TURN_LIMIT,
            message=say("token_cap", lang),
        )

    return Verdict(allowed=True)


def repeated_question(conversation: Conversation, text: str, threshold: int = 2) -> bool:
    """Has the guest effectively asked this already?

    Exact-ish matching on normalised text. Crude, but it catches the real case —
    a guest rephrasing barely at all because the first answer did not land — and
    it costs nothing. Semantic similarity arrives with embeddings in P2.
    """
    from apps.reception.models import MessageRole

    norm = _normalise(text)
    if not norm:
        return False

    previous = (
        conversation.messages.filter(role=MessageRole.GUEST)
        .order_by("-created_at")
        .values_list("content", flat=True)[:6]
    )
    matches = sum(1 for item in previous if _normalise(item) == norm)
    return matches >= threshold


def check_outbound(answer: str, language: str = "en") -> Verdict:
    """Run on the model's reply before the guest sees it."""
    if not answer.strip():
        return Verdict(
            allowed=False,
            handoff=True,
            reason=HandoffReason.ERROR,
            message=say("error", language),
        )

    if NON_ANSWER.search(answer):
        # Not a failure — the prompt asked for exactly this when context is
        # missing. It is, however, the moment to offer a human.
        return Verdict(
            allowed=True,
            handoff=True,
            reason=HandoffReason.LOW_CONFIDENCE,
            message=answer,
        )

    return Verdict(allowed=True, message=answer)


def confidence_of(answer: str, citations: list) -> float:
    """A blunt, explainable score.

    Not a model-reported probability: those are poorly calibrated and would give
    a false sense of precision. This measures what the product actually cares
    about — is the answer sourced, and did it dodge the question.
    """
    if NON_ANSWER.search(answer):
        return 0.2
    if citations:
        return min(1.0, 0.7 + 0.1 * len(citations))
    return 0.5


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9ঀ-৿ ]+", "", text.lower()).strip()
