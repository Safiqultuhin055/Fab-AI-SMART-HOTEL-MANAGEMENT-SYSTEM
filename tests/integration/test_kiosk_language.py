"""The whole kiosk is in one language, and it is the guest's.

Before this, a Bangla property got a Bangla *conversation* inside English
furniture: "Send", "Talk to a human", "Your booking", "Arriving / Nights /
Phone", "Not enabled", "Document OCR", and a paragraph about MRZ checksums. The
28 ``{% trans %}`` tags that were supposed to handle it never could — there is no
locale catalog in this project at all, so every one of them rendered English.

Two rules are tested here, and both matter more than any single string:

1. **Nothing guest-facing is written in a template or a script.** If a guest can
   read it, it comes from ``apps.reception.copy``.
2. **Both languages ship to the browser**, so tapping the chip re-labels the
   screen in the same second — including the parts drawn by JS later.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.urls import reverse

from apps.reception.copy import CHROME

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

KIOSK_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk.js"
DEVICES_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk-devices.js"
ENROL_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk-enrol.js"
PANEL = Path(__file__).parents[2] / "templates" / "reception" / "_kiosk_panel.html"

#: Same string in both languages on purpose: a pure placeholder pattern, and the
#: tile identifiers and icons, which are not words.
SAME_IN_BOTH = {"numbered", "key", "icon"}

#: English chrome that must not appear in the MARKUP of a Bangla kiosk. The page
#: body legitimately contains English inside the both-languages JSON blob, which
#: is why every test here strips those attributes first.
ENGLISH_CHROME = [
    "AI Reception Kiosk",
    "Talk to a human",
    ">Send<",
    "Your booking",
    "in progress",
    "Not enabled",
    "Waiting for a guest",
    "Document OCR",
    "Object Detection",
    "Guest Photo",
    "Tap to speak",
    "Your words will appear here",
    "You can ask me anything",
]


@pytest.fixture
def bangla_hotel(hotel):
    hotel.kiosk_language = "bn"
    hotel.save(update_fields=["kiosk_language"])
    return hotel


def page(client, hotel) -> str:
    return client.get(f"{reverse('reception:kiosk')}?hotel={hotel.code}").content.decode()


def markup(body: str) -> str:
    """The page with the copy payloads removed.

    Those attributes carry BOTH languages by design, so a naive "is English on
    this page" assertion passes and fails for the wrong reasons. What matters is
    the markup a guest actually reads.
    """
    return re.sub(r'data-(copy|copy-all|panels)="[^"]*"', "", body)


def blob(body: str, attribute: str) -> dict:
    raw = re.search(rf'{attribute}="([^"]*)"', body).group(1)
    return json.loads(raw.replace("&quot;", '"').replace("&#x27;", "'").replace("&amp;", "&"))


def leaves(node, path=()):
    """Every string in the copy tree, with the path that reached it."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from leaves(value, (*path, index))
    else:
        yield path, node


class TestTheTwoSidesCannotDrift:
    """The guarantee that makes the rest safe: adding a key to one language and
    forgetting the other fails here rather than on a lobby screen."""

    def test_both_languages_have_exactly_the_same_keys(self):
        english = {path for path, _ in leaves(CHROME["en"])}
        bangla = {path for path, _ in leaves(CHROME["bn"])}

        assert english - bangla == set(), "in English, missing from Bangla"
        assert bangla - english == set(), "in Bangla, missing from English"

    def test_every_string_is_actually_translated(self):
        """A key copied across untranslated is the failure this catches — it looks
        done in the diff and reads as English on the screen."""
        untranslated = [
            ".".join(str(part) for part in path)
            for (path, value), (_, other) in zip(
                leaves(CHROME["bn"]), leaves(CHROME["en"]), strict=True
            )
            if value == other and str(path[-1]) not in SAME_IN_BOTH
        ]
        assert untranslated == []

    def test_no_string_is_left_empty(self):
        for language, words in CHROME.items():
            blanks = [path for path, value in leaves(words) if isinstance(value, str) and not value]
            assert blanks == [], (language, blanks)


class TestABanglaPropertyGetsABanglaScreen:
    def test_none_of_the_english_chrome_survives(self, client, bangla_hotel):
        body = markup(page(client, bangla_hotel))
        leaked = [phrase for phrase in ENGLISH_CHROME if phrase in body]
        assert leaked == []

    def test_the_buttons_and_the_titles_are_bangla(self, client, bangla_hotel):
        body = markup(page(client, bangla_hotel))
        words = CHROME["bn"]

        for key in ("send", "human", "booking_title", "not_enabled", "turns", "brand_sub"):
            assert words[key] in body, key

    def test_the_vision_rail_is_bangla_including_the_paragraph(self, client, bangla_hotel):
        """The longest English text on the screen was a note about MRZ checksums,
        sitting at a guest's eye level."""
        body = markup(page(client, bangla_hotel))

        assert CHROME["bn"]["panels"]["ocr_title"] in body
        assert "MRZ" in body, "the acronym is the acronym in both languages"
        assert "arrives in Phase 2" not in body

    def test_the_removed_ai_badge_left_no_copy_behind(self):
        """The scene's AI-health badge is gone, so the words it read are gone too.
        A key nothing renders is a key the next person translates for nothing."""
        for language in ("en", "bn"):
            words = CHROME[language]
            assert "assistant" not in words
            assert not [key for key in words if key.startswith("ai_")]

    def test_the_tiles_ask_their_question_in_bangla_too(self, client, bangla_hotel):
        """Tapping a tile sends its prompt as the guest's own message. An English
        prompt asks the model the question in the wrong language."""
        body = markup(page(client, bangla_hotel))

        for tile in CHROME["bn"]["tiles"]:
            assert tile["label"] in body, tile["key"]
            assert tile["prompt"] in body, tile["key"]
        assert "I would like to check in" not in body

    def test_screen_reader_names_are_bangla_as_well(self, client, bangla_hotel):
        """A guest using a screen reader on a Bangla kiosk is still a Bangla
        speaker. An English aria-label is the same bug as an English button, only
        harder to notice."""
        body = markup(page(client, bangla_hotel))

        assert f'aria-label="{CHROME["bn"]["aria_message"]}"' in body
        assert f'aria-label="{CHROME["bn"]["aria_conversation"]}"' in body
        assert f'aria-label="{CHROME["bn"]["devices"]["aria_mic"]}"' in body

    def test_the_page_title_and_the_brand_line_follow(self, client, bangla_hotel):
        body = page(client, bangla_hotel)
        assert f"<title>{CHROME['bn']['page_title']}" in body


class TestAnEnglishPropertyIsUnchanged:
    def test_english_chrome_is_still_english(self, client, hotel):
        body = markup(page(client, hotel))

        for key in ("send", "human", "booking_title", "brand_sub"):
            assert CHROME["en"][key] in body, key

    def test_no_bangla_leaks_onto_an_english_screen(self, client, hotel):
        body = markup(page(client, hotel))
        for key in ("send", "human", "booking_title"):
            assert CHROME["bn"][key] not in body, key


class TestSwitchingHappensWithoutAReload:
    def test_both_languages_reach_the_browser(self, client, bangla_hotel):
        both = blob(page(client, bangla_hotel), "data-copy-all")

        assert set(both) == {"en", "bn"}
        assert both["bn"]["send"] == CHROME["bn"]["send"]
        assert both["en"]["send"] == CHROME["en"]["send"]

    def test_the_rail_notes_arrive_ready_formatted_in_both(self, client, bangla_hotel):
        """The face note carries the frame count and the retention period. Two
        languages of sentence assembly in a script is how one of them ends up
        reading like a machine wrote it."""
        panels = blob(page(client, bangla_hotel), "data-panels")

        assert set(panels) == {"en", "bn"}
        assert set(panels["bn"]) == {"face", "recognition", "scan", "ocr", "verify", "payment"}
        assert "{frames}" not in panels["bn"]["face"]["note"]
        assert panels["bn"]["ocr"]["note"] != panels["en"]["ocr"]["note"]

    def test_one_call_relabels_the_whole_screen(self):
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "const applyChrome = () =>" in source
        # Run at load as well, so the load path and the switch path are the same
        # code and a key only the switch sets cannot go missing on arrival.
        assert "applyChrome();\n  buildWave();" in source
        assert "const setChromeLanguage = (language) =>" in source

    def test_the_chip_relabels_before_it_waits_for_the_server(self):
        """A tap that changes nothing for a second and a half reads as a control
        that did not work, and the guest taps it again."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        pick = source.index("el.langPick.addEventListener")
        handler = source[pick : pick + 1400]

        assert "setChromeLanguage(pinnedLanguage);" in handler
        assert handler.index("setChromeLanguage") < handler.index("await post(API.chat")

    def test_the_parts_drawn_after_the_switch_are_redrawn(self):
        """The booking card and the gallery are built from a payload, so they keep
        the language they were built in until something redraws them."""
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "let lastBooking = null;" in source
        assert "if (lastBooking) renderBooking(lastBooking);" in source

    def test_the_sibling_scripts_are_told(self):
        """The device bar and the consent overlay are separate files with their own
        copy. One event rather than three globals."""
        assert "new CustomEvent('ashos:language'" in KIOSK_JS.read_text(encoding="utf-8")
        assert "relabel(words)" in DEVICES_JS.read_text(encoding="utf-8")
        assert "ashos:language" in ENROL_JS.read_text(encoding="utf-8")

    def test_the_booking_card_labels_come_from_the_copy(self):
        """These were English literals in the script, so a Bangla booking was read
        back as "Arriving / Nights / Phone"."""
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "const rowLabel = (key) =>" in source
        for label in ("'Arriving'", "'Nights'", "'Phone'"):
            assert label not in source, label


class TestWhatMustNotBeTranslated:
    def test_the_template_has_no_trans_tags_left(self):
        """Django resolves those against the request's locale, and the language
        here belongs to the conversation — a guest taps a chip and it changes
        mid-session, with no request in sight."""
        assert "{% trans" not in PANEL.read_text(encoding="utf-8")

    def test_a_property_that_wrote_its_own_hint_keeps_those_words(self, client, bangla_hotel):
        """It is in whatever language the hotelier wrote it. Overwriting it with a
        translation of the default is losing their words, not localising them."""
        bangla_hotel.kiosk_hint = "Ask me about the rooftop pool."
        bangla_hotel.save(update_fields=["kiosk_hint"])

        body = markup(page(client, bangla_hotel))

        assert "Ask me about the rooftop pool." in body
        assert 'data-custom="true"' in body
        assert "if (hint && hint.dataset.custom !== " in KIOSK_JS.read_text(encoding="utf-8")

    def test_a_device_s_own_name_is_left_alone(self):
        """ "Logitech StreamCam" is the operating system's string, not a word."""
        source = DEVICES_JS.read_text(encoding="utf-8")
        assert "device.label ||" in source


class TestNoScriptWritesItsOwnWords:
    """The rule that keeps this from rotting.

    A single bare literal is enough to break the promise, and it is invisible in
    review: a fully Bangla kiosk still said "Tap to speak" under the microphone —
    the one control a guest looks at before they have said anything — because one
    of five assignments to that element skipped ``t()``.
    """

    #: A guest reads all of these.
    WRITES = re.compile(
        r"(?:textContent|placeholder|\.title|\.alt|innerHTML)\s*=\s*'([A-Z][^']{3,})'"
        r"|setAttribute\('aria-label',\s*'([A-Z][^']{3,})'\)"
    )

    def scripts(self):
        return {"kiosk.js": KIOSK_JS, "kiosk-devices.js": DEVICES_JS, "kiosk-enrol.js": ENROL_JS}

    def test_every_visible_string_goes_through_the_copy(self):
        offenders = []
        for name, path in self.scripts().items():
            source = path.read_text(encoding="utf-8")
            # A fallback inside t('key', 'Fallback') is fine — it only shows when
            # the payload is missing entirely. Drop those calls before looking.
            stripped = re.sub(r"t\((?:[^()]|\([^()]*\))*\)", "t()", source)
            offenders += [
                (name, match.group(1) or match.group(2)) for match in self.WRITES.finditer(stripped)
            ]
        assert offenders == []


class TestThingsThatLookLikeWordsButAreNot:
    def test_every_bed_type_has_a_word_in_both_languages(self):
        """The choice label is resolved against the request's locale, so "King"
        turned up on a Bangla screen. A bed type added later with no translation
        fails here."""
        from apps.rooms.models import BedType

        for language in ("en", "bn"):
            beds = CHROME[language]["beds"]
            missing = [value for value in BedType.values if value not in beds]
            assert missing == [], (language, missing)

    def test_the_payload_sends_the_code_not_a_label(self):
        from services.rooms import media

        source = (Path(__file__).parents[2] / "services" / "rooms" / "media.py").read_text(
            encoding="utf-8"
        )
        # The docstring names it to say why it is not used; the code must not call it.
        assert "room_type.get_bed_type_display()" not in source
        assert '"bed": room_type.bed_type' in source
        assert media  # imported to prove the module still loads

    def test_numerals_follow_the_script_the_screen_is_in(self):
        """A Bangla screen where the assistant says "৩১৬২৫ টাকা" while the card
        beside it says "31625.00" is half switched."""
        assert CHROME["en"]["digits"] == "0123456789"
        assert CHROME["bn"]["digits"] == "০১২৩৪৫৬৭৮৯"

        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "const digits = (value) =>" in source
        assert "escapeHtml(digits(booking[key]))" in source
        assert "digits(booking.total)" in source

    def test_the_booking_reference_stays_in_latin_digits(self):
        """A guest reads it out at the desk, and it goes into a field that only
        accepts what the server issued."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "confirmed ? booking.code : t('booking_draft'" in source
        assert "digits(booking.code)" not in source
