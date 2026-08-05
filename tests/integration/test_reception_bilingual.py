"""Ask in Bangla, get Bangla. Ask in English, get English. Same conversation.

The kiosk opens in whatever the property configured, but a guest is not obliged
to use it. A Bangladeshi hotel gets Bangla all day and then a foreign visitor
walks up and types in English — answering them in Bangla because the property is
set to Bangla is a worse failure than having no default at all.

So the language is decided per turn from the guest's own words. These tests pin
both halves of that: the switch happens when it should, and — the harder half —
it does NOT happen on "ok", a phone number or one borrowed English word in a
Bangla sentence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.urls import reverse

from apps.reception.models import Channel
from services.reception import orchestrator
from services.reception.language import detect

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

KIOSK_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk.js"


@pytest.fixture
def bangla_hotel(hotel):
    hotel.kiosk_language = "bn"
    hotel.save(update_fields=["kiosk_language"])
    return hotel


@pytest.fixture
def convo(bangla_hotel):
    return orchestrator.start(hotel=bangla_hotel, channel=Channel.KIOSK)


# ==============================================================================
# Detection
# ==============================================================================


class TestDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "আমার একটা রুম দরকার",
            "চেক আউট কখন?",
            "ভ্যাট কত শতাংশ?",
            "একটু সাহায্য করবেন",
        ],
    )
    def test_bangla_is_recognised(self, text):
        assert detect(text, fallback="en") == "bn"

    @pytest.mark.parametrize(
        "text",
        [
            "I need a room",
            "what time is check out",
            "do you have wifi",
            "hello there",
        ],
    )
    def test_english_is_recognised(self, text):
        assert detect(text, fallback="bn") == "en"

    @pytest.mark.parametrize("text", ["ok", "yes", "geee", "৳", "12", "০১৭১১০০০০০০", "", "   "])
    def test_short_or_wordless_input_decides_nothing(self, text):
        """Flipping the conversation on "ok" is how a guest gets answered in the
        wrong language mid-sentence."""
        assert detect(text, fallback="bn") == "bn"
        assert detect(text, fallback="en") == "en"

    def test_a_borrowed_english_word_does_not_flip_a_bangla_sentence(self):
        """Code-mixing is the normal case here, not an edge case."""
        assert detect("আমার একটা deluxe রুম লাগবে", fallback="bn") == "bn"
        assert detect("আগামীকাল থেকে দুই রাত", fallback="bn") == "bn"

    def test_a_bangla_word_in_an_english_sentence_does_not_flip_it(self):
        assert detect("I want a রুম for two nights please", fallback="en") == "en"

    def test_a_genuine_even_split_leaves_things_alone(self):
        mixed = "রুম room"
        assert detect(mixed, fallback="bn") == "bn"
        assert detect(mixed, fallback="en") == "en"

    def test_a_phone_number_is_not_evidence(self):
        """Bengali digits are digits. A guest reading their number out is not
        telling us which language they want."""
        assert detect("০১৭১১০০০০০০", fallback="en") == "en"


# ==============================================================================
# The conversation follows the guest
# ==============================================================================


class TestTheConversationFollows:
    def test_an_english_question_at_a_bangla_kiosk_is_answered_in_english(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}  # deterministic answerer
        turn = orchestrator.respond(convo, "what time is check out please")

        assert turn.language == "en"
        assert "check-out" in turn.reply.lower() or "check out" in turn.reply.lower()

    def test_a_bangla_question_is_answered_in_bangla(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "চেক আউট কখন?")

        assert turn.language == "bn"
        assert "চেক-আউট" in turn.reply

    def test_the_switch_is_remembered(self, convo, settings):
        """A guest who changes language should not have to prove it every turn."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        orchestrator.respond(convo, "what time is check out please")

        convo.refresh_from_db()
        assert convo.language == "en"

        # ...and a bare acknowledgement does not throw it back.
        turn = orchestrator.respond(convo, "ok")
        assert turn.language == "en"

    def test_it_can_switch_back(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        orchestrator.respond(convo, "what time is check out please")
        turn = orchestrator.respond(convo, "ভ্যাট কত শতাংশ?")

        assert turn.language == "bn"
        convo.refresh_from_db()
        assert convo.language == "bn"

    def test_the_greeting_still_uses_the_property_setting(self, bangla_hotel):
        """The kiosk opens in the hotel's language — nobody has said anything yet."""
        assert "আসসালামু আলাইকুম" in orchestrator.greeting(
            bangla_hotel, style="islamic"
        ) or "স্বাগতম" in orchestrator.greeting(bangla_hotel)

    def test_the_api_reports_the_language_of_the_turn(self, client, bangla_hotel, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}

        start = client.post(
            reverse("v1:reception_start"),
            {"channel": "kiosk"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=bangla_hotel.code,
        )
        conversation = start.json()["conversation"]
        assert start.json()["language"] == "bn"

        reply = client.post(
            reverse("v1:reception_chat"),
            {"conversation": conversation, "message": "what time is check out please"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=bangla_hotel.code,
        )
        assert reply.json()["language"] == "en"


# ==============================================================================
# What the guest sees and hears
# ==============================================================================


class TestKioskChrome:
    def kiosk(self, client, hotel):
        return client.get(f"{reverse('reception:kiosk')}?hotel={hotel.code}").content.decode()

    def test_the_state_labels_are_in_the_guests_language(self, client, bangla_hotel):
        """ "শুনছি… বলুন" while it hears you, "ভাবছি…" while it works. Silence
        with no label reads as broken and people tap the button again."""
        body = self.kiosk(client, bangla_hotel)
        copy = json.loads(body.split('data-copy="')[1].split('"')[0].replace("&quot;", '"'))

        assert copy["listening"] == "শুনছি… বলুন"
        assert copy["thinking"] == "ভাবছি…"
        assert copy["ready"] == "প্রস্তুত"

    def test_english_property_gets_english_chrome(self, client, hotel):
        body = self.kiosk(client, hotel)
        copy = json.loads(body.split('data-copy="')[1].split('"')[0].replace("&quot;", '"'))
        assert copy["thinking"] == "Thinking…"

    def test_the_input_doubles_as_the_transcript_box(self, client, bangla_hotel):
        body = self.kiosk(client, bangla_hotel)
        assert "এখানে আপনার কথা লেখা উঠবে" in body

    def test_every_state_label_comes_from_the_server(self):
        """A literal in the script would show in English under a Bangla answer.

        The English strings still in the file are last-resort defaults for a
        missing payload; what matters is that the server's copy is consulted
        first for every one of them.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "JSON.parse(root.dataset.copy" in source
        # Looked up per call, not frozen into a map at load: a map built once kept
        # the language the page loaded in, so the label under the orb stayed
        # English after the guest switched.
        assert "const stateLabel = (state)" in source
        assert "el.state.textContent = stateLabel(state);" in source
        block = source[source.index("const STATE_KEY = {") : source.index("let currentState")]
        for key in ("ready", "listening", "thinking", "speaking", "offline"):
            assert f"'{key}'" in block, key

    def test_the_client_retunes_speech_to_the_answers_language(self):
        """Answer in English and the next thing the guest says must be HEARD as
        English, or recognition returns nonsense and the guest gets blamed."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "retune(data.language);" in source
        assert "let speechLang" in source, "must not be const — it follows the guest"

    def test_a_female_voice_is_preferred(self, client, hotel):
        body = self.kiosk(client, hotel)
        assert 'data-voice-gender="female"' in body

        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "FEMALE_HINTS" in source
        # Web Speech exposes no gender field, so name matching is the only signal.
        assert "voiceGender === 'female'" in source

    def test_the_language_outranks_the_voice_preference(self):
        """A female English voice reading a Bangla answer is worse than a male
        Bangla one — and reading Bangla with an English voice at all is worse than
        staying quiet, which is what used to happen."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        pool = source.index("const pool = voicesFor(speechLang);")
        gender = source.index("if (voiceGender === 'female')")
        assert pool < gender

    def test_a_missing_voice_is_never_substituted_with_another_language(self):
        """The bug behind "it will not read Bangla": with no Bangla voice installed
        the candidate pool became EVERY voice, so an English one was handed Bengali
        script and produced silence or noise."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "const pool = sameLang.length ? sameLang : voices;" not in source
        assert "const pool = voicesFor(speechLang);" in source

        # speakInBrowser refuses rather than mispronouncing. The utterance itself is
        # built in speakChunk now — an answer is spoken one sentence at a time — so the
        # refusal has to come before the first chunk is ever handed over.
        block = source[source.index("const speakInBrowser") :]
        block = block[: block.index("\n  };")]
        assert block.index("if (!voice)") < block.index("speakChunk(")
        assert "return false;" in block
        # ...and the guest is told once, instead of being met with silence.
        assert "const warnNoVoice" in source
        assert "warnedLanguages.has(base)" in source

    def test_the_missing_voice_notice_is_bilingual(self, client, bangla_hotel):
        body = client.get(
            f"{reverse('reception:kiosk')}?hotel={bangla_hotel.code}"
        ).content.decode()
        assert "ভয়েস ইনস্টল করা নেই" in body

    def test_the_property_can_name_an_exact_provider_voice(self, client, hotel):
        hotel.kiosk_voice_name = "shimmer"
        hotel.save(update_fields=["kiosk_voice_name"])
        assert 'data-voice-name="shimmer"' in self.kiosk(client, hotel)


# ==============================================================================
# The opening: which language?
# ==============================================================================


class TestTheLanguageChooser:
    """A bilingual receptionist asks once, in both languages, then commits.

    This is the flow being copied: a guest walks up, is asked "Bangla or English?"
    in both, answers in one, and is served in that one from then on.
    """

    def test_the_bilingual_marks_are_counted(self):
        """The bug this class exists because of.

        Chandrabindu, anusvara and visarga were missing from the letter class, so
        "বাংলা" scored four letters, fell under the length floor, and was read as
        ENGLISH — the one word a guest would say to choose Bangla.
        """
        from services.reception.language import _BANGLA

        # The marks themselves are letters now.
        assert len(_BANGLA.findall("বাংলা")) == 5
        assert len(_BANGLA.findall("চাঁদ")) == 4
        assert len(_BANGLA.findall("দুঃখিত")) == 6

        # Which is what pushes them over the floor and out of English.
        assert detect("বাংলা", fallback="en") == "bn"
        assert detect("দুঃখিত", fallback="en") == "bn"
        # "চাঁদ" is still four letters, so it correctly decides nothing on its own.
        assert detect("চাঁদ", fallback="en") == "en"

    @pytest.mark.parametrize(
        ("said", "expected"),
        [
            ("বাংলা", "bn"),
            ("বাঙলা", "bn"),
            ("bangla", "bn"),
            ("bengali", "bn"),
            ("English", "en"),
            ("english", "en"),
            ("ইংলিশ", "en"),
            ("ইংরেজি", "en"),
            ("english please", "en"),
            ("বাংলা বলুন", "bn"),
        ],
    )
    def test_a_named_language_is_honoured(self, said, expected):
        from services.reception.language import choose

        assert choose(said) == expected

    @pytest.mark.parametrize(
        "said",
        [
            "do you have a Bengali newspaper",
            "বাংলা নাকি ইংলিশ?",
            "I need a room",
            "চেক আউট কখন?",
            "",
        ],
    )
    def test_merely_mentioning_a_language_is_not_a_choice(self, said):
        """ "Do you have a Bengali newspaper" is a question about newspapers."""
        from services.reception.language import choose

        assert choose(said) is None

    def test_an_explicit_request_wins_at_any_length(self):
        from services.reception.language import choose

        assert choose("I would like to please continue in English") == "en"
        assert choose("আপনি কি আমার সাথে ইংরেজিতে কথা বলবেন") == "en"

    def test_the_opening_offers_both_languages(self, bangla_hotel):
        parts = orchestrator.language_prompt(bangla_hotel)

        assert [p["language"] for p in parts] == ["bn", "en"]

        # Asserted against the copy itself rather than against a quoted sentence: the
        # wording of the first thing a guest hears is the property's to tune, and a
        # test that hardcodes it fails on an edit that changed nothing about the
        # behaviour. What must hold is that the question is asked in BOTH languages,
        # each in its own part, so each can be spoken by its own voice.
        assert parts[0]["text"] == orchestrator.LANGUAGE_PROMPT["bn"]
        assert parts[1]["text"] == orchestrator.LANGUAGE_PROMPT["en"]
        assert "বাংলা" in parts[0]["text"] and "ইংলিশ" in parts[0]["text"]
        assert "Bangla" in parts[1]["text"] and "English" in parts[1]["text"]

    def test_the_language_question_comes_before_the_welcome(self, client, bangla_hotel):
        """Welcoming somebody before they have said which language they read means
        guessing, and the welcome is the one sentence you least want to get wrong."""
        body = client.post(
            reverse("v1:reception_start"),
            {"channel": "kiosk"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=bangla_hotel.code,
        ).json()

        assert body["greeting"] == ""
        assert body["language_prompt"]

    def test_the_propertys_own_language_is_offered_first(self, hotel):
        """At a Dhaka property most guests want Bangla; making the majority listen
        to the other option first is a small rudeness a few hundred times a day."""
        assert orchestrator.language_prompt(hotel)[0]["language"] == "en"

        hotel.kiosk_language = "bn"
        hotel.save(update_fields=["kiosk_language"])
        assert orchestrator.language_prompt(hotel)[0]["language"] == "bn"

    def test_the_start_endpoint_carries_the_prompt(self, client, bangla_hotel):
        response = client.post(
            reverse("v1:reception_start"),
            {"channel": "kiosk"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=bangla_hotel.code,
        )
        parts = response.json()["language_prompt"]
        assert len(parts) == 2
        # Separate parts, because one utterance cannot be bilingual.
        assert {p["language"] for p in parts} == {"bn", "en"}

    def test_choosing_english_is_answered_with_the_english_welcome(
        self, convo, bangla_hotel, settings
    ):
        """The welcome finally arrives, in a language we know the guest reads."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "English")

        assert turn.language == "en"
        assert bangla_hotel.name in turn.reply
        assert "assist" in turn.reply.lower()
        # Deterministic: no tokens spent welcoming somebody, and the model cannot
        # welcome them in the wrong language.
        assert turn.ai_used is False

    def test_choosing_bangla_is_answered_with_the_bangla_welcome(
        self, convo, bangla_hotel, settings
    ):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "বাংলা")

        assert turn.language == "bn"
        assert "স্বাগতম" in turn.reply
        assert "সাহায্য" in turn.reply

    def test_the_conversation_continues_in_the_chosen_language(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        orchestrator.respond(convo, "English")
        turn = orchestrator.respond(convo, "চেক আউট কখন?")

        # ...and still follows the guest if they switch again, because that is what
        # a receptionist does.
        assert turn.language == "bn"

    def test_asking_a_question_straight_away_skips_the_choice(self, convo, settings):
        """Nagging somebody who is already talking is not service."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "what time is check out please")

        assert turn.language == "en"
        assert "check-out" in turn.reply.lower() or "check out" in turn.reply.lower()

        convo.refresh_from_db()
        assert convo.language_confirmed is True

    def test_the_choice_is_only_offered_once(self, convo, settings):
        """A guest who says "English" and then says "bangla" later is switching
        language, not answering the opening question again."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        orchestrator.respond(convo, "English")
        convo.refresh_from_db()
        assert convo.language_confirmed is True

        turn = orchestrator.respond(convo, "বাংলা")
        assert turn.language == "bn"
        # Answered as an ordinary turn rather than welcomed all over again.
        assert "স্বাগতম" not in turn.reply

    def test_each_half_of_the_prompt_is_spoken_in_its_own_voice(self):
        """Bangla read by an English voice is worse than not reading it out."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "const speakIn = async (language, text)" in source
        assert "await speakIn(part.language, part.text);" in source
        # ...and the running language is restored, or recognition would be left
        # listening in the wrong one.
        assert "speechLang = previous;" in source


class TestSwitchingWhenYouCannotBeHeard:
    """The dead end this class exists because of.

    Speech recognition listens in ONE language at a time. A guest speaking Bangla
    into an English-tuned recogniser gets back a transcript of Latin noise — so
    they cannot even be *heard* asking to switch, and the server, which reads the
    language off the text, has nothing true to read. Typing is a way out only if
    you have the other keyboard.

    So the kiosk carries a language control, and a pick from it outranks anything
    detected from the text.
    """

    def test_the_kiosk_has_a_language_control(self, client, bangla_hotel):
        body = client.get(
            f"{reverse('reception:kiosk')}?hotel={bangla_hotel.code}"
        ).content.decode()
        assert 'id="kiosk-lang-pick"' in body
        assert "বাংলা" in body
        assert ">English<" in body

    def test_a_pinned_language_beats_the_text(self, convo, settings):
        """The whole point: the text says one thing, the guest asked for another."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}

        turn = orchestrator.respond(convo, "what time is check out please", language="bn")
        assert turn.language == "bn"
        assert "চেক-আউট" in turn.reply

    def test_it_works_in_the_other_direction_too(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}

        turn = orchestrator.respond(convo, "চেক আউট কখন?", language="en")
        assert turn.language == "en"
        assert "check-out" in turn.reply.lower()

    def test_the_pin_is_remembered(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        orchestrator.respond(convo, "hello there", language="bn")

        convo.refresh_from_db()
        assert convo.language == "bn"

    def test_a_language_we_do_not_speak_is_ignored_not_obeyed(self, convo, settings):
        """A bad pin must fall back to reading the text, not to a wrong language."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}

        turn = orchestrator.respond(convo, "চেক আউট কখন?", language="fr")
        assert turn.language == "bn"

    def test_the_endpoint_accepts_the_pin(self, client, bangla_hotel, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        start = client.post(
            reverse("v1:reception_start"),
            {"channel": "kiosk"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=bangla_hotel.code,
        )
        reply = client.post(
            reverse("v1:reception_chat"),
            {
                "conversation": start.json()["conversation"],
                "message": "what time is check out",
                "language": "bn",
            },
            content_type="application/json",
            HTTP_X_HOTEL_CODE=bangla_hotel.code,
        )
        assert reply.json()["language"] == "bn"

    def test_the_client_sends_the_pin_on_every_turn(self):
        """A pin the server has to re-derive from each message is not a pin."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "language: pinnedLanguage," in source
        # ...and the microphone is retuned before the guest can reply.
        pick = source.index("el.langPick.addEventListener")
        assert "retune(pinnedLanguage);" in source[pick : pick + 600]

    def test_the_control_follows_a_switch_made_by_talking(self):
        """A guest who switched by speaking must not be shown a chip claiming the
        old language."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        # One call now moves the chip AND every label on the screen. The chip alone
        # was a screen that said বাংলা while reading "Send" and "Your booking".
        assert "setChromeLanguage(data.language);" in source
        assert "if (el.langPick) el.langPick.value = code;" in source
