"""Which language is the guest speaking, this turn?

A hotel receptionist does not ask a guest to fill in a form before answering
them. They open in one language, hear which one comes back, and continue in that
one. This module is that behaviour.

Three rules, in order of authority:

*A named language is a decision.* "বাংলা" or "English" as an answer to "which
language?" is a request, not a hint, and it outranks everything below. It has to
be handled separately because "বাংলা" is only four letters and would otherwise
fall under the length floor — the very word a guest uses to ask for Bangla.

*Script beats guessing.* Bangla and English share no alphabet, so counting which
one the letters belong to is exact: no model, no library, no latency. It also
cannot be fooled by vocabulary — "রুম", "চেক-আউট" and "ভ্যাট" are Bangla words in
Bangla script whatever their origin.

*Short input decides nothing.* "ok", "yes", "geee", a phone number, a room code —
none of that is evidence about language, and flipping the conversation on it is
how a guest ends up answered in the wrong language mid-sentence. Below a floor of
real letters the current language stands.

Code-mixing is the normal case here rather than an edge case: "আমার একটা deluxe
room লাগবে" is one sentence in two scripts. A clear majority wins, and a genuine
50/50 leaves things as they are.
"""

from __future__ import annotations

import re

BN = "bn"
EN = "en"

# ==============================================================================
# A guest naming the language they want
# ==============================================================================

# Kept separate from the counting heuristic below, and answered first, because a
# name is a *request* and a request outranks a guess.
#
# The marks matter here. Chandrabindu, anusvara and visarga (U+0981-0983) were
# once missing from the letter class, which made "বাংলা" score four letters,
# putting it under the length floor — so the one word a guest would say to choose
# Bangla was read as English. "চাঁদ" and "দুঃখিত" were miscounted the same way.
_WANTS_BANGLA = re.compile(
    r"(বাংলা|বাঙলা|বাংলায়|বাঙ্গলা)|\b(bangla|bengali|bangali|bn)\b",
    re.IGNORECASE,
)
_WANTS_ENGLISH = re.compile(
    r"(ইংলিশ|ইংরেজি|ইংরাজি|ইংরেজী)|\b(english|eng|en)\b",
    re.IGNORECASE,
)

# "Speak in X", as opposed to merely mentioning X. This is what lets a language
# name count inside a long sentence: "I would like to continue in English" is a
# decision, while "do you have a Bengali newspaper" is a question about newspapers.
_REQUEST_SHAPE = re.compile(
    r"\b(in|speak|talk|switch|use|reply|answer|please)\b"
    r"|(বলুন|বলো|বলবেন|কথা\s*বল|দাও|করুন|চাই|তে)",
    re.IGNORECASE,
)

#: How many words still counts as a bare answer to "which language?".
BARE_ANSWER_WORDS = 3

# ==============================================================================
# Counting the script
# ==============================================================================

#: Bengali letters, marks AND vowel signs, because a fair count needs all three.
#: A vowel sign is part of the letter to a reader, so it is part of the letter
#: here.
#:
#: Digits and the taka sign sit outside the class on purpose. A phone number is
#: not a sentence, and a guest reading their number out is not telling us which
#: language they want.
_BANGLA = re.compile(
    "["
    "ঁ-ঃ"  # chandrabindu, anusvara, visarga
    "অ-হ"  # independent vowels and consonants
    "়-ৌ"  # nukta, vowel signs, virama
    "ৎ"  # khanda ta
    "ৗ"  # au length mark
    "ড়-ৡ"  # rra, rha, yya, vocalic RR/LL
    "ৰ-ৱ"  # ra/va with middle diagonal
    "]"
)
_LATIN = re.compile(r"[A-Za-z]")

#: Below this many letters, nothing is decided. Five clears "hello" and a short
#: Bangla word while still refusing to act on "ok", "yes" or a mistyped "geee".
#:
#: A guest naming a language is exempt — :func:`choose` answers first and is
#: subject to no length floor at all.
MIN_LETTERS = 5

#: How lopsided the count has to be. 0.6 tolerates the ordinary code-mixed
#: sentence while still refusing to flip on one borrowed word.
MAJORITY = 0.6


def choose(text: str) -> str | None:
    """The guest naming the language they want, or ``None``.

    Two shapes are accepted and nothing else:

    * a bare answer — "বাংলা", "English", "bangla please" — which is what somebody
      says when they have just been asked to pick;
    * an explicit request at any length — "please continue in English",
      "ইংরেজিতে বলুন" — which is a decision rather than a passing mention.

    Everything else falls through to :func:`detect`, so "do you have a Bengali
    newspaper" gets answered instead of being treated as a language switch.
    """
    body = (text or "").strip()
    if not body:
        return None

    wants_bn = bool(_WANTS_BANGLA.search(body))
    wants_en = bool(_WANTS_ENGLISH.search(body))
    # Both at once is not a choice, it is a question about languages — which is
    # exactly what the opening prompt itself looks like.
    if wants_bn == wants_en:
        return None

    bare = len(body.split()) <= BARE_ANSWER_WORDS
    if not bare and not _REQUEST_SHAPE.search(body):
        return None

    return BN if wants_bn else EN


def detect(text: str, *, fallback: str = EN) -> str:
    """The language of this message, or ``fallback`` when the text does not say.

    ``fallback`` is the conversation's current language, so "no evidence" means
    "carry on as we were" rather than "revert to English".
    """
    named = choose(text)
    if named is not None:
        return named

    bangla = len(_BANGLA.findall(text or ""))
    latin = len(_LATIN.findall(text or ""))
    total = bangla + latin

    if total < MIN_LETTERS:
        return fallback
    if bangla / total >= MAJORITY:
        return BN
    if latin / total >= MAJORITY:
        return EN
    return fallback


def is_supported(code: str) -> bool:
    return (code or "").split("-")[0] in {BN, EN}


def normalise(code: str, *, fallback: str = EN) -> str:
    """Reduce a tag like ``bn-BD`` to a language this reception speaks.

    Pass ``fallback=""`` to be told "not one of ours" instead of being handed a
    guess — which is what a caller validating an explicit request needs.
    """
    base = (code or "").split("-")[0].lower()
    return base if base in {BN, EN} else fallback
