"""Strip everything internal out of what a guest reads.

A receptionist does not say "the Twin Economy is free [15]". The numbers in that
sentence are ours: they index the CONTEXT block the model was given, and they exist
so the server can score whether an answer was sourced at all
(``guardrails.confidence_of``). They were never meant to leave the building, and
they were reaching the guest twice over — inline as ``[15]`` and again as a
"তথ্যসূত্র:" footer under the bubble.

So the split is explicit:

    the model writes them      the prompt still asks for [1], [2] markers
    the server reads them      citations and confidence are computed from them
    the guest never sees them  this module removes them at the HTTP boundary
    the record keeps them      Message.content and Message.citations are unchanged,
                               because "which fact did that answer come from" is a
                               question an operator has to be able to answer later

Applied at the boundary rather than at each of the dozen places a reply is built:
there is one way out to a guest, and one place is a place that cannot be forgotten.
The same stripped text is what gets spoken, since the client reads the payload.

Room numbers, telephone numbers and email addresses are deliberately untouched —
those are the guest's own business and the rules name them as allowed.
"""

from __future__ import annotations

import re

#: ``[15]``, ``[16, 17]``, ``[১৫]`` — the citation markers themselves.
#:
#: Bengali digits are in here because the model writes them in Bangla answers: it was
#: producing "স্বাগতম [১]" and an ASCII-only pattern left that one on screen.
_MARKER = re.compile(r"\s*\[[\d০-৯]+(?:\s*[,–-]\s*[\d০-৯]+)*\]")

#: A "sources:" footer, if a model writes one into the body of its answer. The UI no
#: longer renders the citation list, but a model that decides to append its own line
#: would put it back.
_SOURCE_LINE = re.compile(
    r"(?im)^\s*(?:sources?|references?|তথ্যসূত্র|সূত্র)\s*[:：].*$",
)

#: Identifiers that only mean something inside the system: chunk and vector ids,
#: document and record ids, embedding ids, and bare UUIDs. A model that has been
#: handed retrieved text can quote the wrapper around it as easily as the text.
_INTERNAL_ID = re.compile(
    r"(?i)\b(?:chunk|vector|embedding|doc(?:ument)?|record|row|node|source|kb)"
    r"[\s_-]*(?:id|no|number)?\s*[:#=]?\s*[0-9a-f-]{2,}\b"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)

#: Left behind once a marker in the middle of a sentence is removed: " ।" or " ," and
#: the double spaces around the hole.
_ORPHAN_PUNCT = re.compile(r"\s+([।,.!?;:])")
_DOUBLE_SPACE = re.compile(r"[ \t]{2,}")
_BLANK_LINES = re.compile(r"\n{3,}")


def for_guest(text: str) -> str:
    """The answer as a person should read it.

    Order matters: the source line goes first (it is mostly markers, and removing
    those first would leave a bare "তথ্যসূত্র:"), then the inline markers, then the
    punctuation those markers were sitting in front of.
    """
    if not text:
        return text

    cleaned = _SOURCE_LINE.sub("", text)
    cleaned = _INTERNAL_ID.sub("", cleaned)
    cleaned = _MARKER.sub("", cleaned)
    cleaned = _ORPHAN_PUNCT.sub(r"\1", cleaned)
    cleaned = _DOUBLE_SPACE.sub(" ", cleaned)
    cleaned = _BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def has_internal_reference(text: str) -> bool:
    """Would :func:`for_guest` have to remove something? For tests and for logging."""
    return for_guest(text) != (text or "").strip()
