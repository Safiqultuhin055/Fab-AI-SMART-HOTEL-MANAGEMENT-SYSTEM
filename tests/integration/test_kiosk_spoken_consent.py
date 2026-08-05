"""The photo-consent question is read aloud, and can be answered out loud.

A guest at a kiosk may have luggage in both hands. A consent question they can
only answer by touching the screen is one they will answer by walking away — so
the question is spoken in the language the conversation is in, and yes/no is
accepted by voice as well as by tap.

Two properties matter more than the feature:

*Silence is not consent.* An unheard answer leaves the screen up and the buttons
where they were. Nothing is captured because nobody said anything.

*One recogniser owns the microphone.* The consent screen asks the conversation's
voice engine for an answer rather than starting a second recogniser, because two
of them fighting for one device is how a working microphone becomes an unreliable
one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

KIOSK_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk.js"
ENROL_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk-enrol.js"


def enrol_copy(client, hotel) -> dict:
    """The consent screen's own copy blob.

    Anchored on #enrol-stage because the page carries two ``data-copy``
    attributes — the kiosk chrome has one too, and it comes first.
    """
    body = client.get(f"{reverse('reception:kiosk')}?hotel={hotel.code}").content.decode()
    stage = body[body.index('id="enrol-stage"') :]
    raw = re.search(r'data-copy="([^"]*)"', stage).group(1)
    both = json.loads(raw.replace("&quot;", '"').replace("&#x27;", "'").replace("&amp;", "&"))
    # Both languages travel, because the guest can switch between the booking and
    # the consent question; the caller asks for the one it is about to check.
    return both


@pytest.fixture
def bangla_hotel(hotel):
    hotel.kiosk_language = "bn"
    hotel.save(update_fields=["kiosk_language"])
    return hotel


class TestTheWordsComeFromTheServer:
    def test_bangla_yes_and_no_words_are_shipped(self, client, bangla_hotel):
        """A translator owns these alongside the question, not a regex in a script."""
        copy = enrol_copy(client, bangla_hotel)["bn"]

        assert "হ্যাঁ" in copy["yes_words"]
        assert "ঠিক আছে" in copy["yes_words"]
        assert "না" in copy["no_words"]
        assert "দরকার নেই" in copy["no_words"]

    def test_english_gets_english_words(self, client, hotel):
        copy = enrol_copy(client, hotel)["en"]

        assert "yes" in copy["yes_words"]
        assert "go ahead" in copy["yes_words"]
        assert "no" in copy["no_words"]
        assert "not now" in copy["no_words"]

    def test_the_question_itself_travels_with_them(self, client, bangla_hotel):
        """It has to be read out, so the text has to reach the client."""
        copy = enrol_copy(client, bangla_hotel)["bn"]

        assert "ছবি" in copy["title"]
        assert copy["body"]
        assert copy["accept"]
        assert copy["decline"]

    def test_both_languages_ship_so_a_switch_mid_booking_is_answerable(self, client, bangla_hotel):
        """A guest can change language between the booking and the consent
        question. Asking in Bangla and then listening for "yes" is how somebody
        says হ্যাঁ and is treated as though they said nothing."""
        both = enrol_copy(client, bangla_hotel)

        assert set(both) == {"en", "bn"}
        assert "যেস" not in both["bn"]["yes_words"]
        assert "হ্যাঁ" in both["bn"]["yes_words"]
        assert "yes" in both["en"]["yes_words"]


class TestOneRecogniserOwnsTheMicrophone:
    def test_the_conversation_offers_a_voice_api(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "window.ashosVoice = {" in source
        for method in ("language:", "say:", "suspend:", "resume:", "askYesNo:"):
            assert method in source, method

    def test_the_consent_screen_asks_through_it(self):
        """Rather than starting a recogniser of its own."""
        source = ENROL_JS.read_text(encoding="utf-8")
        assert "window.ashosVoice" in source
        assert "voice.askYesNo({" in source
        assert "SpeechRecognition" not in source

    def test_the_loop_is_suspended_while_the_question_is_up(self):
        """Otherwise the conversation answers on the guest's behalf: their "yes"
        would arrive as a chat turn."""
        enrol = ENROL_JS.read_text(encoding="utf-8")
        assert "voice.suspend();" in enrol
        assert "voice.resume();" in enrol

        kiosk = KIOSK_JS.read_text(encoding="utf-8")
        # ...and the loop actually honours it.
        assert "let suspended = false;" in kiosk
        for guard in ("|| autoListen || suspended", "|| listening || suspended"):
            assert guard in kiosk, guard


class TestSilenceIsNotConsent:
    def test_an_unheard_answer_decides_nothing(self):
        """askYesNo returns null, distinct from false, so a caller can tell "they
        said no" from "I did not hear them"."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("askYesNo: async") :]
        block = block[: block.index("/** One utterance")]

        assert "return null;" in block
        # Nothing usable heard -> keep listening, then give up without deciding.
        assert "if (!heard) continue;" in block

    def test_the_screen_stays_up_when_nothing_was_heard(self):
        enrol = ENROL_JS.read_text(encoding="utf-8")
        assert "if (answer === null" in enrol

    def test_no_is_checked_before_yes(self):
        """ "yes, no problem" contains both. Read as agreement it would capture a
        face nobody agreed to."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("askYesNo: async") :]
        assert block.index("match(heard, no)") < block.index("match(heard, yes)")

    def test_the_buttons_are_never_taken_away(self):
        """Voice is in addition to the taps, not instead of them."""
        enrol = ENROL_JS.read_text(encoding="utf-8")
        assert "el.accept.addEventListener('click', accept);" in enrol
        assert "el.decline.addEventListener('click', declineNow);" in enrol
        # The question is asked without blocking them.
        assert "askOutLoud();" in enrol

    def test_a_tap_during_the_question_wins(self):
        """Whichever the guest does first should settle it, with no double action."""
        enrol = ENROL_JS.read_text(encoding="utf-8")
        assert "stage.classList.contains('is-hidden') || running" in enrol


class TestTheListenerCannotHangTheScreen:
    def test_a_session_that_never_ends_is_cut_off(self):
        """Chrome usually closes it; when it does not, a consent screen must not
        wait forever."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const listenOnce") :]
        assert "once.abort();" in block
        assert "9000" in block

    def test_it_does_not_talk_into_its_own_microphone(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const listenOnce") :]
        assert "stopSpeaking();" in block[: block.index("once.start()")]


class TestTheOpeningIsSequential:
    """The second line starts only after the first has finished.

    This was broken by a detail of the audio API: ``play()`` resolves when playback
    STARTS, not when it ends. Returning there made every caller believe the answer
    had been read out while it was still on its first syllable, so the two halves
    of the bilingual opening spoke over the top of each other.
    """

    def test_playback_resolves_on_ended_not_on_play(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("if (serverTts) {") :]
        block = block[: block.index("if (browserTts &&")]

        assert "audio.onended = () => settle(true);" in block
        # The call that starts playback must not be the thing the caller awaits.
        assert "await audio.play();\n        return finished;" in block
        assert "return true;\n        return finished" not in block

    def test_the_browser_path_already_waited_for_the_end(self):
        """It resolved on utterance.onend, which is why English sounded fine and
        only the server path overlapped."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "utterance.onend = () => done(true);" in source

    def test_a_stalled_element_cannot_leave_a_caller_waiting(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("if (serverTts) {") :]
        block = block[: block.index("if (browserTts &&")]

        assert "playbackWatchdog" in block
        # Sized from the real duration once the browser knows it.
        assert "audio.duration * 1000 + 2000" in block

    def test_interrupting_speech_settles_the_promise(self):
        """Cutting a sentence short — a language switch, a guest walking away, the
        microphone opening — must not leave the awaiting caller on a promise
        nothing will ever resolve."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const stopSpeaking") :]
        block = block[: block.index("const speakInBrowser")]

        assert "if (finishSpeaking) finishSpeaking(false);" in block

    def test_the_pause_comes_after_the_line_finishes(self):
        """Order in the loop: speak the line to the end, wait, then the next one."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const speakOpening = async") :]
        block = block[: block.index("/** Speak one line")]

        spoke = block.index("await speakIn(part.language, part.text)")
        paused = block.index("await wait(gap)")
        assert spoke < paused

    def test_a_stale_utterance_cannot_settle_a_newer_one(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "if (finishSpeaking !== settle) return;" in source
