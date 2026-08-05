"""The assistant panel's context — everything ``reception/_kiosk_panel.html`` needs.

Its own module because three pages embed that widget: the lobby terminal, the staff
console and the public booking page. It used to live in ``views.py``, which meant
the public page imported another app's VIEW module at boot — and a URLconf that
imports a view module inherits everything that module imports. A public page should
not be able to take the whole site down on its way up.

``lobby`` decides whether a camera is on the table at all: True for the terminal
in a lobby, False for a receptionist's laptop and for a guest booking from home.
The standing microphone is a separate question, answered by ``channel`` — see
:data:`HANDS_FREE_CHANNELS`.
"""

from __future__ import annotations

import json

from apps.reception import copy
from services.ai import gateway
from services.reception import guidance

# Which pages open the microphone by themselves, so nothing has to be pressed.
#
# The channel and not ``lobby``, because the two questions are different ones.
# ``lobby`` asks "is there a machine with a camera in front of a stranger"; this
# asks "is the person here to talk to the assistant". A guest on /book/ opened a
# page whose whole content is a receptionist — making them find and press a
# button first is a step nobody wants — while a receptionist with the widget in
# the corner of their console is working on something else, and an always-open
# microphone on their own laptop is nobody's idea of a feature.
#
# Voice is still not forced on anyone: the browser's own permission bubble gates
# the microphone on both, and the property switch (Settings → AI, "hands-free
# microphone") turns it off for both.
HANDS_FREE_CHANNELS = ("kiosk", "website")

#: Seconds of silence before the assistant asks the next question itself, per
#: channel. Absent means never.
#:
#: 45 on the booking page and 60 in the lobby, both from the same reasoning: long
#: enough that somebody reading the room list or typing a name is not interrupted,
#: short enough to catch the guest who stopped rather than the one who left. The
#: lobby waits longer because a guest standing at a terminal can see it is there;
#: a browser tab gives no such reassurance.
NUDGE_SECONDS = {"website": 45, "kiosk": 60}


def panel_context(hotel, *, lobby: bool = False, channel: str = "web") -> dict:
    """Everything ``reception/_kiosk_panel.html`` needs, for any page that embeds it.

    Three pages now include that widget — the lobby terminal, the staff console and
    the public booking page — and each one used to build its own context. A third
    copy is a third place to forget ``data-panels`` and get a rail that never
    relabels.

    ``lobby`` decides whether a camera is on the table at all; ``channel`` decides
    whether the microphone opens by itself (:data:`HANDS_FREE_CHANNELS`).
    """
    chrome = _chrome(hotel, channel)
    return {
        "channel": channel,
        "kiosk_copy": chrome,
        "kiosk_copy_json": json.dumps(chrome, ensure_ascii=False),
        "kiosk_copy_all_json": copy.chrome_json(channel),
        "progress_steps": list(chrome["progress_steps"].items()),
        "tiles": chrome["tiles"],
        "vision_panels": _vision_panels(hotel, chrome, lobby=lobby),
        "vision_panels_json": _vision_panels_json(hotel, lobby=lobby),
        "ai_status": _ai_status(hotel),
        "ai_model": _ai_model_name(hotel),
        "speech": gateway.speech_status(
            str(hotel.pk) if hotel else None,
            language=hotel.kiosk_language if hotel else "en",
        ),
        "voice": _voice_preference(hotel),
        "kiosk": _kiosk_settings(hotel, lobby=lobby, channel=channel),
        "enrol": _enrolment_context(hotel, lobby=lobby),
    }


def _chrome(hotel, channel: str = "") -> dict:
    """The chrome for this property's language, named for the page it is on.

    The channel decides what the assistant calls itself: the lobby terminal and the
    staff console are AI Reception, and the public booking page is Online Booking —
    which is what a guest opening a hotel's website came for, and what the browser tab
    has to be findable by.
    """
    return copy.chrome(hotel.kiosk_language if hotel else None, channel)


def _ai_status(hotel) -> dict:
    """The AI posture, for the panel's ``data-ai-state``.

    No longer localised, because nothing on the kiosk renders the label any more:
    the scene's status badge was removed. Whether the provider is up is an
    operator's question, and the operator has it in the console topbar.
    """
    return gateway.status(str(hotel.pk) if hotel else None)


def _ai_model_name(hotel) -> str:
    """The model answering, for the header — the name only, never a key.

    Shown because a staff member setting a terminal up should be able to see what
    it is talking to without opening AI Center. Empty when nothing is configured,
    and the header then says the answers come from the hotel record instead of
    implying a brain that is not there.
    """
    if not gateway.is_available(str(hotel.pk) if hotel else None):
        return ""
    try:
        from services.ai import registry

        model = registry.resolve("llm", str(hotel.pk) if hotel else None)
    except Exception:  # noqa: BLE001 - a header line is never worth a 500
        return ""
    return model.model_name or ""


def _voice_preference(hotel) -> dict:
    """What kind of voice should read the answers out.

    Web Speech exposes no gender field, so the browser side matches on voice
    names; a server provider takes an exact id. Both are driven from the same
    property setting so the terminal does not change character when a key is
    added.
    """
    return {
        "gender": hotel.kiosk_voice_gender if hotel else "female",
        "name": hotel.kiosk_voice_name if hotel else "",
    }


def _kiosk_settings(hotel, *, lobby: bool = True, channel: str = "web") -> dict:
    """What the browser needs to know before it turns a camera on.

    Nothing here opens a camera at page load any more. The assistant greets and
    answers first; a camera is only reached through the post-booking consent
    screen, which is gated separately in :func:`_enrolment_context`.

    ``lobby`` is False for the staff console. The embedded kiosk there is for a
    receptionist to try a question; switching on the webcam of whatever laptop
    they happen to be using is not something anyone asked for.
    """
    return {
        # Kept because the property setting still exists and staff can see it,
        # but the lobby entry screen no longer uses it: presence detection was
        # the old camera-first gate, and the assistant is the front door now.
        "presence": lobby and bool(hotel and hotel.kiosk_presence_detection),
        "greeting_style": hotel.kiosk_greeting_style if hotel else "neutral",
        "hands_free": channel in HANDS_FREE_CHANNELS and bool(hotel and hotel.kiosk_hands_free),
        # Is there a member of staff who can be brought into this conversation?
        #
        # False on the public booking page: nobody is watching it, so the "talk to a
        # person" button is not drawn and the assistant never promises one. The
        # reception telephone number is in the page header instead, which is a way to
        # reach a person that does not depend on somebody watching a queue
        # (goal.txt R7).
        "staffed": not guidance.SELF_SERVE_CHANNELS.intersection({channel}),
        # Seconds of silence before the assistant asks the next question itself. 0
        # switches it off.
        #
        # A guest at a lobby terminal is standing in front of it and has a person ten
        # metres away; a guest with the booking page open in a tab has neither, and
        # silence there is how a half-finished booking is abandoned. The staff console
        # gets nothing: a receptionist's own screen asking them questions while they
        # work is an interruption, not service.
        "nudge_after": NUDGE_SECONDS.get(channel, 0),
    }


def _enrolment_context(hotel, *, lobby: bool = True) -> dict:
    """Whether the kiosk may offer face capture, and exactly what it will say.

    Both flags must be on (``services.vision.enrolment.is_enabled``), and the
    staff console never offers it at all — enrolling a guest's face through a
    receptionist's laptop webcam is not a flow anybody designed.
    """
    from services.vision import enrolment

    status = enrolment.status(hotel)
    enabled = lobby and status["enabled"]

    def resolved(language: str) -> dict:
        words = copy.enrol(language)
        return {
            **words,
            "bullets": [
                line.format(frames=status["frames"], days=status["retention_days"])
                for line in words["bullets"]
            ],
        }

    both = {language: resolved(language) for language in (copy.EN, copy.BN)}
    return {
        "enabled": enabled,
        "frames": status["frames"],
        "retention_days": status["retention_days"],
        "stores_images": status["stores_images"],
        # The active language, for the markup the server renders.
        "copy": both[copy.resolve(hotel.kiosk_language if hotel else None)],
        # Both languages, for the capture loop — pose prompts, the progress line,
        # the two failure messages, and the words that count as yes and no. Both
        # rather than one because the guest can switch language between the
        # booking and the consent question, and the question has to follow: asking
        # in Bangla and then listening for "yes" is how a guest says হ্যাঁ and gets
        # treated as though they said nothing.
        "copy_json": json.dumps(
            {
                language: {
                    key: words[key]
                    for key in (
                        "title",
                        "body",
                        "bullets",
                        "accept",
                        "decline",
                        "cancel",
                        "capturing",
                        "done",
                        "failed",
                        "camera_blocked",
                        "poses",
                        "yes_words",
                        "no_words",
                    )
                }
                for language, words in both.items()
            },
            ensure_ascii=False,
        ),
    }


def _vision_panels_json(hotel, *, lobby: bool = True) -> str:
    """Both languages of the rail, resolved and formatted, for the switch.

    The face note carries the frame count, the retention period and the storage
    phrase. Working those into a sentence is the server's job in either language —
    a script assembling that from fragments is how one of the two ends up reading
    like a machine wrote it.
    """
    return json.dumps(
        {
            language: {
                panel["key"]: {"title": panel["title"], "note": panel["note"]}
                for panel in _vision_panels(hotel, copy.chrome(language), lobby=lobby)
            }
            for language in (copy.EN, copy.BN)
        },
        ensure_ascii=False,
    )


def _vision_panels(hotel, chrome: dict, *, lobby: bool = True) -> list[dict]:
    """The rail: what the terminal knows about this guest, card by card.

    Seven steps of an arrival — the photo, the recognition, the document scan, the
    OCR, the verification, and what is owed. Exactly two of them are built. The
    other five say so and carry their phase, because a mocked-up "verified ✓" is
    how a stakeholder comes to believe a compliance feature exists, and a guest
    comes to believe their passport was checked (goal.txt §2.3, D10).

    Every word comes from ``chrome["panels"]``: this rail sits on the lobby screen
    in the guest's eyeline, so an English paragraph about MRZ checksums on a Bangla
    kiosk is the same bug as an English button.
    """
    from services.vision import enrolment

    status = enrolment.status(hotel)
    capture_on = lobby and status["enabled"]
    words = chrome["panels"]

    if capture_on:
        face_note = words["face_note_on"].format(
            frames=status["frames"],
            days=status["retention_days"],
            storage=(
                chrome["storage_encrypted"]
                if status["stores_images"]
                else chrome["storage_not_stored"]
            ),
        )
    else:
        face_note = words["face_note_off"]

    return [
        {
            "key": "face",
            "title": words["face_title_consent"] if capture_on else words["face_title"],
            "enabled": capture_on,
            "phase": "P3",
            "note": face_note,
            # The rail never shows a live feed. The only camera in the product
            # opens inside the post-booking consent overlay.
            "camera": True,
        },
        {
            "key": "recognition",
            "title": words["recognition_title"],
            "enabled": False,
            "phase": "P3",
            "note": words["recognition_note"],
        },
        {
            "key": "scan",
            "title": words["scan_title"],
            "enabled": False,
            "phase": "P2",
            "note": words["scan_note"],
        },
        {
            "key": "ocr",
            "title": words["ocr_title"],
            "enabled": False,
            "phase": "P2",
            "note": words["ocr_note"],
        },
        {
            "key": "verify",
            "title": words["verify_title"],
            "enabled": False,
            "phase": "P2",
            "note": words["verify_note"],
        },
        {
            "key": "payment",
            "title": words["payment_title"],
            "enabled": False,
            "phase": "P2",
            "note": words["payment_note"],
        },
    ]
