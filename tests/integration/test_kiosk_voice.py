"""Voice in and voice out at the kiosk.

Two bugs are pinned here.

The kiosk told the server its language was whatever Django's active locale
happened to be, so a hotel configured for Bangla greeted in Bangla and then
answered every question in English — the client was overriding the property
setting on every single conversation start.

And voice was treated as available only when a paid speech provider was
configured. On a hotel with a working chat model and no speech key the microphone
was disabled and nothing was ever spoken aloud, even though the browser can do
both for free and speaks bn-BD. A lobby terminal that cannot be talked to is the
wrong trade.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.urls import reverse

from services.ai import gateway

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

KIOSK_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk.js"


def kiosk_html(client, hotel):
    return client.get(f"{reverse('reception:kiosk')}?hotel={hotel.code}").content.decode()


def attr(body: str, name: str) -> str:
    match = re.search(rf'{name}="([^"]*)"', body)
    return match.group(1) if match else ""


@pytest.fixture
def bangla_hotel(hotel):
    hotel.kiosk_language = "bn"
    hotel.save(update_fields=["kiosk_language"])
    return hotel


# ==============================================================================
# Language
# ==============================================================================


class TestTheKioskSendsTheHotelsLanguage:
    def test_a_bangla_property_is_advertised_as_bangla(self, client, bangla_hotel):
        """The regression: this used to be Django's LANGUAGE_CODE, so the browser
        posted "en" and silently overrode the property."""
        assert attr(kiosk_html(client, bangla_hotel), "data-language") == "bn"

    def test_an_english_property_stays_english(self, client, hotel):
        assert attr(kiosk_html(client, hotel), "data-language") == "en"

    def test_the_speech_tag_carries_a_region(self, client, bangla_hotel):
        """Browser speech engines reject a bare "bn"; the region is mandatory."""
        assert attr(kiosk_html(client, bangla_hotel), "data-speech-lang") == "bn-BD"

    def test_english_gets_a_region_too(self, client, hotel):
        assert attr(kiosk_html(client, hotel), "data-speech-lang") == "en-US"

    @pytest.mark.parametrize(
        ("language", "expected"),
        [("bn", "bn-BD"), ("en", "en-US"), ("hi", "hi-IN"), ("ar", "ar-SA"), ("bn-BD", "bn-BD")],
    )
    def test_every_supported_language_maps_to_a_real_tag(self, hotel, language, expected):
        assert gateway.speech_status(str(hotel.pk), language=language)["bcp47"] == expected

    def test_an_unknown_language_does_not_break_the_page(self, hotel):
        status = gateway.speech_status(str(hotel.pk), language="xx")
        assert status["bcp47"] == "en-US"


# ==============================================================================
# Availability
# ==============================================================================


class TestVoiceWithoutAKey:
    @pytest.fixture
    def no_speech_provider(self, settings):
        """Test settings pin every capability to the keyless "fake" provider,
        which counts as configured. Take it away to see the real default."""
        from services.ai import registry

        settings.AI = {
            **settings.AI,
            "STT": {**settings.AI["STT"], "provider": "openai_compatible", "api_key": ""},
            "TTS": {**settings.AI["TTS"], "provider": "openai_compatible", "api_key": ""},
        }
        registry.invalidate()
        yield settings
        registry.invalidate()

    def test_the_page_still_reports_no_server_provider(self, client, hotel, no_speech_provider):
        """The flags keep their meaning — they select the engine, they do not
        decide whether voice exists."""
        body = kiosk_html(client, hotel)
        assert attr(body, "data-voice") == "false"
        assert attr(body, "data-tts") == "false"

    def test_the_client_falls_back_to_the_browser_engines(self):
        """Without this the mic is dead and nothing is ever read aloud on a hotel
        that has no speech key — which is every hotel by default."""
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "window.SpeechRecognition || window.webkitSpeechRecognition" in source
        assert "'speechSynthesis' in window" in source
        assert "SpeechSynthesisUtterance" in source
        # Availability must be the union of the two engines, not the server alone.
        assert "const voiceEnabled = serverStt || browserStt;" in source
        assert "const ttsEnabled = serverTts || browserTts;" in source

    def test_the_misleading_hint_is_gone(self):
        """ "Voice not configured" was shown to a guest whose browser could have
        handled it perfectly well."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "Voice not configured" not in source

    def test_a_configured_provider_still_takes_precedence(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "if (serverTts) {" in source
        assert "if (serverStt) await startRecording();" in source

    def test_the_speaker_never_talks_into_the_open_microphone(self):
        """Browser recognition captures the same device the speaker feeds, so the kiosk
        would transcribe its own last answer.

        It used to guarantee that by NOT SPEAKING when the microphone was open — the
        wrong half of the trade. The microphone is ours to close; the answer is the
        guest's to hear. Silence also fell on every question the assistant asks on its
        own initiative, because nothing on that path had closed the microphone first.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const speak = async (text)") :]
        block = block[: block.index("//: Which languages")]

        assert "if (listening) return true;" not in block
        assert "pauseForAnswer()" in block
        assert source.count("stopSpeaking()") >= 3

    def test_a_dropped_utterance_cannot_hang_the_greeting(self):
        """Chrome can refuse a pre-gesture utterance without firing onend or
        onerror; the greeting awaits that promise."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "watchdog" in source
        assert "window.speechSynthesis.speaking || window.speechSynthesis.pending" in source


class TestServerSpeechWhenConfigured:
    def test_flags_flip_once_a_key_exists(self, client, hotel):
        from apps.ai_center.models import ModelConfig, ModelKind, Provider

        for kind in (ModelKind.STT, ModelKind.TTS):
            ModelConfig.all_objects.create(
                tenant=hotel,
                kind=kind,
                name=f"{kind} key",
                provider=Provider.OPENAI_COMPATIBLE,
                model_name="whisper-1",
                api_key="sk-speech",
                is_default=True,
                is_active=True,
            )
        from services.ai import registry

        registry.invalidate()

        body = kiosk_html(client, hotel)
        assert attr(body, "data-voice") == "true"
        assert attr(body, "data-tts") == "true"


# ==============================================================================
# Device selection
# ==============================================================================

DEVICES_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk-devices.js"
ENROL_JS = Path(__file__).parents[2] / "static" / "js" / "kiosk-enrol.js"


@pytest.fixture
def console_user(db, hotel):
    """Somebody who can actually open the staff console.

    The shared ``receptionist`` fixture builds a role with no permissions
    attached, so it 403s here; seeding the real matrix is both correct and what
    the console is guarded by.
    """
    import io

    from django.core.management import call_command

    from apps.accounts.backends import invalidate_permission_cache
    from apps.accounts.models import Role, RoleCode, User
    from apps.core.context import set_request_context
    from apps.tenants.models import HotelMembership

    call_command("seed_roles", stdout=io.StringIO())
    user = User.objects.create_user(
        email="console@ashos.local", password="Demo@12345", full_name="Console"
    )
    HotelMembership.objects.create(
        user=user,
        hotel=hotel,
        role=Role.objects.get(code=RoleCode.AI_RECEPTION),
        is_default=True,
    )
    set_request_context(tenant_id=str(hotel.pk))
    invalidate_permission_cache(str(user.pk), str(hotel.pk))
    return user


class TestDeviceBar:
    def test_the_three_chips_are_on_the_page(self, client, hotel):
        body = kiosk_html(client, hotel)
        for anchor in ("kiosk-devices", "device-camera", "device-mic", "device-speaker"):
            assert f'id="{anchor}"' in body

    def test_they_sit_in_the_header_not_between_guest_and_answer(self, client, hotel):
        """Terminal setup, not part of the conversation. It must not push the
        conversation down the screen.

        On the terminal that header is now the PAGE header — hotel, devices, model,
        clock — and the widget's own title bar is gone, because two header bars
        above one conversation is 55px of nothing.
        """
        body = kiosk_html(client, hotel)
        header = body.index("kiosk-header")
        scene = body.index('class="scene')
        assert header < body.index('id="kiosk-devices"') < scene

    def test_the_bar_appears_exactly_once_on_a_page(self, client, hotel):
        """The ids are what kiosk-devices.js binds to. Two of them is a second
        dropdown nobody is listening to."""
        body = kiosk_html(client, hotel)
        for anchor in ('id="kiosk-devices"', 'id="device-camera"', 'id="device-mic"'):
            assert body.count(anchor) == 1, anchor

    def test_the_caveats_are_a_tooltip_not_lobby_small_print(self, client, hotel):
        """Three lines of small print across a lobby screen is noise a guest
        never needs to read; the words stay available to a screen reader."""
        body = kiosk_html(client, hotel)
        marker = body.index('id="device-note"')
        assert "visually-hidden" in body[marker - 120 : marker]
        assert "bar.title = text;" in DEVICES_JS.read_text(encoding="utf-8")

    def test_the_native_select_chrome_is_fully_stripped(self, client):
        """All three appearance properties, or the browser keeps its own white box
        and arrow — which is exactly what a stale stylesheet looks like."""
        css = (Path(__file__).parents[2] / "static" / "css" / "kiosk.css").read_text(
            encoding="utf-8"
        )
        block = css[css.index(".device-chip select {") : css.index(".device-chip select:focus")]
        for rule in ("appearance: none", "-webkit-appearance: none", "-moz-appearance: none"):
            assert rule in block
        # Long names must ellipsise rather than push the header apart.
        assert "text-overflow: ellipsis" in block

    def test_it_is_on_the_staff_console_too(self, client, console_user):
        """Same panel, same rig problem — a console laptop also has two cameras."""
        client.force_login(console_user)
        body = client.get(reverse("reception:home")).content.decode()
        assert 'id="kiosk-devices"' in body

    def test_the_script_loads_before_the_clients_that_read_it(self, client, hotel):
        """kiosk.js and kiosk-enrol.js call window.ashosDevices as soon as they
        open a device."""
        body = kiosk_html(client, hotel)
        assert body.index("kiosk-devices.js") < body.index("kiosk.js")

    def test_the_console_no_longer_loads_the_deleted_script(self, client, console_user):
        """kiosk-face.js was removed with the camera-first flow; a stale tag was
        a 404 on every console page load."""
        client.force_login(console_user)
        body = client.get(reverse("reception:home")).content.decode()
        assert "kiosk-face.js" not in body

    def test_the_choice_survives_a_reload(self):
        source = DEVICES_JS.read_text(encoding="utf-8")
        assert "localStorage.setItem" in source
        # Per property: two terminals in one hotel can have different hardware.
        assert "root.dataset.hotel" in source

    def test_the_chosen_devices_are_actually_used(self):
        """A picker that does not reach getUserMedia is decoration."""
        assert "audioConstraint()" in KIOSK_JS.read_text(encoding="utf-8")
        assert "videoConstraint(base)" in ENROL_JS.read_text(encoding="utf-8")

    def test_the_chosen_speaker_is_applied_before_playback(self):
        """Switching the sink mid-playback drops the opening syllable."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        # `audio`, not `player`: the element is held in a local as well as on the
        # shared reference, because stopSpeaking() now nulls the shared one and these
        # three lines must keep talking about the element they created.
        # play() is started rather than awaited bare now — it can hang where there is
        # no audio output — but the sink still has to be chosen before it begins.
        assert source.index("await routeOutput(audio);") < source.index(
            "const started = audio.play();"
        )

    def test_an_unplugged_device_does_not_stay_selected(self):
        source = DEVICES_JS.read_text(encoding="utf-8")
        assert "stillThere" in source
        assert "localStorage.removeItem" in source

    def test_labels_are_never_bought_with_a_permission_prompt(self):
        """Prompting a guest for their microphone so a dropdown looks tidier is
        not a trade worth making."""
        source = DEVICES_JS.read_text(encoding="utf-8")
        assert "navigator.mediaDevices.getUserMedia" not in source

    def test_it_reacts_to_hardware_being_plugged_in(self):
        source = DEVICES_JS.read_text(encoding="utf-8")
        assert "'devicechange'" in source

    def test_browser_only_limits_are_stated_rather_than_faked(self):
        """setSinkId is Chrome/Edge only, and browser speech cannot be routed at
        all — a control that silently does nothing is worse than one that says
        so."""
        source = DEVICES_JS.read_text(encoding="utf-8")
        assert "setSinkId" in source
        # The sentences themselves live with the rest of the guest's words, in both
        # languages — the bar sits on a lobby screen, so an English caveat on a
        # Bangla kiosk is the same bug as an English button.
        assert "t('note_browser_tts')" in source
        assert "t('note_browser_stt')" in source

        from apps.reception.copy import CHROME

        assert "system default output" in CHROME["en"]["devices"]["note_browser_tts"]
        assert "system default microphone" in CHROME["en"]["devices"]["note_browser_stt"]
        assert (
            CHROME["bn"]["devices"]["note_browser_tts"]
            != (CHROME["en"]["devices"]["note_browser_tts"])
        )


class TestAssetsAreNeverServedStale:
    """The dev static handler sends no Cache-Control, so a browser is free to
    reuse an edited stylesheet. That cost a round of debugging code which was
    already correct — CSS "not applying" and a deleted string still on screen."""

    @pytest.fixture
    def debug_on(self, settings):
        """The tag is a deliberate no-op outside DEBUG, and the suite runs with
        DEBUG off — so it has to be turned on to observe the behaviour at all."""
        settings.DEBUG = True
        return settings

    def test_kiosk_assets_carry_a_version(self, client, hotel, debug_on):
        body = kiosk_html(client, hotel)
        for name in ("css/kiosk.css", "js/kiosk.js", "js/kiosk-devices.js"):
            assert re.search(rf"{re.escape(name)}\?v=\d+", body), name

    def test_the_version_follows_the_file(self, debug_on):
        """Per file, not per deploy: editing one stylesheet must bust one URL."""
        from django.template import Context, Template

        rendered = Template("{% load assets %}{% asset 'css/kiosk.css' %}").render(Context({}))
        stamp = rendered.split("?v=")[1]
        assert stamp.isdigit()

        css = Path(__file__).parents[2] / "static" / "css" / "kiosk.css"
        assert int(stamp) == int(css.stat().st_mtime)

    def test_production_urls_stay_clean(self, settings):
        """WhiteNoise already hashes the filename there; a query string would only
        defeat the CDN."""
        from django.template import Context, Template

        settings.DEBUG = False
        rendered = Template("{% load assets %}{% asset 'css/kiosk.css' %}").render(Context({}))
        assert "?v=" not in rendered

    def test_a_missing_file_does_not_break_the_page(self, debug_on):
        from django.template import Context, Template

        rendered = Template("{% load assets %}{% asset 'css/nope.css' %}").render(Context({}))
        assert rendered.endswith("css/nope.css")


class TestHandsFree:
    """The microphone listens without being pressed, and blinks when it hears.

    Bounded on purpose. An open microphone in a hotel lobby also hears everyone
    walking past, and browser speech recognition ships that audio to the browser
    vendor — so it runs inside an active conversation and stands down after
    silence, rather than streaming an empty lobby all day.
    """

    def test_it_is_on_for_the_lobby_by_default(self, client, hotel):
        assert 'data-hands-free="true"' in kiosk_html(client, hotel)

    def test_a_property_can_turn_it_off(self, client, hotel):
        hotel.kiosk_hands_free = False
        hotel.save(update_fields=["kiosk_hands_free"])
        assert 'data-hands-free="false"' in kiosk_html(client, hotel)

    def test_the_staff_console_never_opens_a_standing_microphone(self, hotel):
        """A receptionist trying a question on their own laptop did not ask for
        an always-on mic."""

        # The panel's context moved out of views.py into its own module: three
        # pages embed that widget, and the public booking page should not import an
        # app's view module — a URLconf that does inherits everything it imports.
        from apps.reception import panel

        # The channel, not `lobby`: the public booking page is not a lobby either
        # and it DOES open the microphone, because the person who opened it came to
        # talk to the assistant. A receptionist with the widget in the corner of
        # their console came to do something else.
        console = panel.panel_context(hotel, lobby=False, channel="web")
        website = panel.panel_context(hotel, lobby=False, channel="website")
        terminal = panel.panel_context(hotel, lobby=True, channel="kiosk")

        assert console["kiosk"]["hands_free"] is False
        assert website["kiosk"]["hands_free"] is True
        assert terminal["kiosk"]["hands_free"] is True

    def test_the_loop_is_wired(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        for name in ("startHandsFree", "rearm", "standDown", "pauseForAnswer", "watchMic"):
            assert f"const {name}" in source, name

    def test_a_transient_error_never_retires_the_microphone(self):
        """"It stops after a few seconds" — and it did, permanently.

        The failure count was per SESSION, cleared only by a successful utterance, and
        Chrome hands out transient errors for free: 'network' when its speech service
        hiccups, 'audio-capture' when the device is busy for 200ms. Six of those over
        a long conversation is ordinary, and the sixth stood the microphone down for
        good with "I am listening" still on screen.

        Backoff, capped, forever. Only a real refusal is permanent.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "MAX_RESTARTS" not in source
        assert "restarts >= " not in source
        assert "const retryDelay" in source
        assert "RETRY_MAX_MS" in source

    def test_a_stuck_tts_flag_cannot_hold_the_microphone_shut(self):
        """The microphone waits for the answer to finish being read out — and has to
        stop waiting eventually.

        Ask Chrome to speak a language it has no voice installed for and
        `speechSynthesis.speaking` can latch true with no `onend` ever firing. The
        deferral is 400ms at a time, so the microphone then waits for an answer that
        already finished, for the rest of the session. That is a real configuration:
        the kiosk says so itself when a Bangla voice is missing.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "TTS_PATIENCE_MS" in source
        block = source[source.index("const rearm = ") :]
        block = block[: block.index("\n  };")]
        assert "TTS_PATIENCE_MS" in block
        # ...and when patience runs out it cancels the speech rather than waiting on.
        assert "stopSpeaking()" in block

    def test_a_dropped_utterance_cannot_hang_the_turn(self):
        """The microphone reopens in applyTurn's `.then(speak)`. So a speak() promise
        that never settles is a microphone that never comes back — and Chrome will drop
        an utterance mid-sentence without firing onend or onerror.

        The engine is polled instead of trusted: while it is really reading the answer
        out, `speaking` stays true. Two quiet polls mean it stopped without saying so,
        and an absolute ceiling sits under that for an engine that reports `speaking`
        forever.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")
        # The per-utterance logic lives in speakChunk now: an answer is spoken one
        # sentence at a time, and each one needs its own settle guarantee.
        block = source[source.index("const speakChunk = ") :]
        block = block[: block.index("const speakInBrowser = ")]

        assert "window.setInterval" in block
        assert "quiet >= 2" in block
        # The ceiling, and it must scale with the sentence — a flat one either cuts a
        # long answer short or leaves a short one hanging.
        assert "words * 700" in block
        # Every exit path clears both timers; a leaked interval polls for the life of
        # the page.
        assert "window.clearInterval(poll)" in block

    def test_a_failed_turn_reopens_the_microphone(self):
        """A request that threw never reaches applyTurn, so nothing there reopens the
        microphone. It used to cost the guest their voice for the rest of the session
        over one 500 or one throttled request."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        send = source[source.index("const send = async (text)") :]
        send = send[: send.index("// --- Voice ---")]

        finally_block = send[send.index("} finally {") :]
        assert "pausing = false;" in finally_block
        assert "rearm();" in finally_block

    def test_the_watchdog_respects_a_microphone_that_is_meant_to_be_shut(self):
        """It reopens what died, not what somebody closed. A guest who tapped the orb
        to stop it, another screen holding the device, a turn in flight and an answer
        being read out are all states it must leave alone."""
        source = KIOSK_JS.read_text(encoding="utf-8")

        watchdog = source[source.index("const watchMic = ") :]
        watchdog = watchdog[: watchdog.index("\n  };")]
        for guard in ("autoListen", "voiceDisabled", "suspended", "busy", "listening", "pausing"):
            assert guard in watchdog, guard

    def test_it_arms_itself_on_load_with_no_tap(self):
        """Greet, then open the microphone. The guest presses nothing, ever."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "openConversation().then(startHandsFree)" in source

    def test_the_first_touch_handler_is_only_a_recovery_path(self):
        """It exists because a browser that has never been granted the microphone
        refuses on a cold first visit — not because a tap is part of the flow."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "const recoverOnFirstTouch" in source
        assert "{ once: true }" in source

    def test_it_does_not_stand_down_on_silence(self):
        """The property asked for always-on. Silence is not a reason to close it;
        the property switch and the session reset are."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "HANDS_FREE_IDLE_MS" not in source
        assert "idleTimer" not in source

    def test_the_microphone_closes_while_an_answer_is_produced(self):
        """Typed or spoken. Otherwise the kiosk hears its own voice and answers
        itself."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        send = source[source.index("const send = async") : source.index("// --- Hands-free loop")]
        assert "pauseForAnswer();" in send

    def test_answering_is_not_counted_as_a_microphone_failure(self):
        """Closing it ourselves for every answer would otherwise trip the restart
        cap after a handful of typed messages and kill hands-free silently."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "let pausing = false;" in source
        onend = source[source.index("recognition.onend") :]
        onend = onend[: onend.index("// --- Wiring")]
        assert "if (pausing)" in onend
        # Nothing in onend increments the failure count at all now: see below.
        assert "restarts += 1" not in onend

    def test_an_ordinary_silent_reopen_is_not_a_failure(self):
        """Chrome closes the recognition session after a pause even when nobody
        said anything, and reopening that is the normal resting state.

        Counting those meant a terminal nobody spoke to for a couple of minutes
        reached the failure cap and quietly switched its own microphone off.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")
        onend = source[source.index("recognition.onend") :]
        onend = onend[: onend.index("// --- Wiring")]
        armed = onend.index("if (autoListen) {")
        assert "restarts += 1" not in onend[armed:]

    def test_a_transient_recogniser_error_does_not_kill_voice_for_the_session(self):
        """The regression this fixes. ANY unknown error used to stand the
        microphone down permanently, and the common one — 'audio-capture', the
        device momentarily busy — lasts about 200ms. A permanent fix to a
        200ms problem is how the microphone ended up dead.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")
        handler = source[source.index("recognition.onerror") :]
        handler = handler[: handler.index("recognition.onend")]

        # Only an actual refusal is permanent...
        assert handler.index("'not-allowed'") < handler.index("disableVoice")
        # ...and it is the only disableVoice in the handler.
        assert handler.count("disableVoice") == 1
        # Everything else backs off and tries again — and keeps trying. The delay used
        # to be `400 * restarts` with a cap that gave up at six; it is capped backoff
        # with no cap on the attempts now.
        assert "window.setTimeout(rearm, retryDelay());" in handler
        assert "standDown" not in handler

    def test_the_voice_does_not_outlive_the_page(self):
        """Reported from a real browser: the guest closed the window and the assistant
        carried on talking.

        `speechSynthesis` is a queue in the BROWSER process, not in the page. An
        utterance that is mid-sentence when the tab closes keeps reading, and there is
        no page left to stop it — so cancelling on the way out is the page's job.

        The old teardown cleared the timers and the recorder and left the voice running:
        everything visible stopped, and the one part that lives outside the page kept
        going.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "const teardown = " in source
        # Both events. beforeunload is skipped when a tab is discarded, when a phone
        # freezes the page, and when the browser is killed rather than closed — all
        # cases where the utterance would otherwise outlive the page.
        assert "window.addEventListener('beforeunload', teardown);" in source
        assert "window.addEventListener('pagehide', teardown);" in source

        block = source[source.index("const teardown = ") :]
        block = block[: block.index("\n  };")]
        # Comments dropped before ordering: they explain the order, so they mention the
        # calls before the calls happen and an index on the raw text reads backwards.
        code = "\n".join(
            line for line in block.splitlines() if not line.strip().startswith(("//", "/*", "*"))
        )
        # Order matters: stopSpeaking() settles the pending speak() promise, and
        # applyTurn reopens the microphone in that promise's .then().
        assert code.index("autoListen = false") < code.index("stopSpeaking()")
        for step in ("clearInterval(waveTimer)", "clearInterval(micWatchdog)", "stopNudge()",
                     "stopRecording()", "recognition.abort()", "stopSpeaking()"):
            assert step in code, step

    def test_the_input_microphone_is_shut_for_the_whole_answer(self):
        """The rule: while the assistant speaks, the microphone takes no input at all;
        when it stops, input opens again.

        Everything that could open the device used to work this out by SAMPLING the
        engine — `speechSynthesis.speaking`, `player.paused`. A sample is a guess about
        an instant, and an answer is not continuous: there is a gap while the audio is
        fetched and one between every pair of sentences. The three-second watchdog
        landed in those gaps and opened the microphone into the middle of the answer,
        where it heard the next sentence and transcribed it as the guest.

        So there is one flag, set before the first syllable and cleared after the last,
        and every path that can open the device asks it.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "let assistantSpeaking = false;" in source
        assert "const isSpeaking = " in source

        # Set in exactly one place, cleared in that function's finally and by
        # stopSpeaking() — nothing else may claim the assistant is or is not talking.
        assert source.count("assistantSpeaking = true;") == 1

        # And consulted by every path that opens the microphone.
        for opener in ("const rearm = ", "const watchMic = ", "const listenInBrowser = ",
                       "const askNext = "):
            block = source[source.index(opener) :]
            block = block[: block.index("\n  };")]
            assert "isSpeaking()" in block, opener

    def test_the_flag_that_shuts_the_microphone_cannot_stick(self):
        """It is cleared when speak() returns, so anything that stops speak() returning
        holds the microphone shut. Three things could: the provider's HTTP call, which
        reaches a service on the internet; play(), which hangs rather than rejects on a
        machine with no audio output; and any engine that stops reporting.

        This was not hypothetical — it silenced the microphone for a whole session
        while it was being written.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")

        for ceiling in ("SPEAK_FETCH_MS", "PLAY_START_MS", "SPEAKING_MAX_MS"):
            assert ceiling in source, ceiling

        # The fetch is raced, not awaited bare.
        deliver = source[source.index("const deliverSpeech = ") :]
        deliver = deliver[: deliver.index("\n  };")]
        assert "Promise.race" in deliver
        assert "wait(SPEAK_FETCH_MS)" in deliver
        assert "wait(PLAY_START_MS)" in deliver

        # And the flag itself expires rather than being believed forever.
        check = source[source.index("const isSpeaking = ") :]
        check = check[: check.index("\n  };")]
        assert "SPEAKING_MAX_MS" in check

    def test_a_guest_may_still_interrupt(self):
        """The rule is that the machine must not hear ITSELF. A person reaching for the
        orb is not that, and a kiosk that will not stop talking is worse than a clipped
        sentence — so the tap stops the speech first, and the device is never open and
        speaking at the same moment."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const toggleMic = ") :]
        block = block[: block.index("\n  };")]

        assert "if (isSpeaking()) stopSpeaking();" in block
        assert block.index("stopSpeaking()") < block.index("startHandsFree()")

    def test_an_answer_is_spoken_one_sentence_at_a_time(self):
        """The number that decides how bad "it kept talking after I closed it" is.

        An utterance is atomic to the platform voice: once Chrome has handed a
        paragraph to SAPI, a cancel() racing the page's teardown arrives too late and
        SAPI finishes what it was given. Per sentence, the queue holds one short thing
        at a time, so the worst survivable case is the tail of one sentence rather than
        a whole answer.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "const speechChunks = " in source
        assert "SPEECH_CHUNK_MAX" in source
        block = source[source.index("const speechChunks = ") :]
        block = block[: block.index("\n  };")]
        # Bangla's danda, not just the Latin terminators: an answer in Bangla contains
        # no full stops, so splitting on "." alone would hand the whole reply over as
        # one utterance — which is the case this exists for.
        assert "।" in block

        # And the sequence stops issuing chunks the moment anything cancels it.
        speak = source[source.index("const speakInBrowser = ") :]
        speak = speak[: speak.index("\n  };")]
        assert "speechGeneration" in speak

    def test_a_detached_audio_element_is_made_to_let_go(self):
        """`new Audio(blob:…)` is never in the document, so nothing tears it down with
        the page: the media stack keeps it alive until playback ends, and pause() alone
        leaves it holding the decoded stream."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const stopSpeaking = ") :]
        block = block[: block.index("\n  };")]

        assert "player.pause()" in block
        assert "removeAttribute('src')" in block
        assert "player.load()" in block
        # And the blob URL is released, or every answer leaks one.
        assert "URL.revokeObjectURL" in block
        assert "player = null" in block

    def test_stopping_the_voice_is_not_gated_on_which_engine_was_chosen(self):
        """`stopSpeaking()` used to cancel synthesis only `if (browserTts)`.

        That flag says which engine we would CHOOSE to speak with. It says nothing
        about what is queued — so every stop path (the guest leaving, a language
        switch, the tab hidden) skipped the one engine that outlives the page.
        Cancelling an empty queue costs nothing.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const stopSpeaking = ") :]
        block = block[: block.index("\n  };")]

        assert "window.speechSynthesis.cancel();" in block
        assert "if (browserTts)" not in block
        # resume() first: a paused synthesiser ignores cancel() in Chrome, and a
        # backgrounded tab is one of the things that pauses it.
        assert block.index("resume()") < block.index("cancel()")

    def test_a_stand_down_actually_releases_the_microphone(self):
        """It used to flip `autoListen` and repaint the orb, and leave the recognition
        session running — so the device stayed open, for as long as Chrome felt like,
        on a page the guest had walked away from."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const standDown = ") :]
        block = block[: block.index("\n  };")]

        assert "recognition.abort()" in block
        # Flagged as our own close first, so onend does not count it as a failure and
        # retry it.
        assert block.index("pausing = true") < block.index("recognition.abort()")
        assert "'is-recording'" in block

    def test_a_hidden_tab_neither_speaks_nor_listens(self):
        """Looking away is not leaving: the conversation stays, but a page nobody is
        looking at must not read an answer aloud and must not hold a microphone open.

        Not exercised end to end — headless Chrome reports every target as visible, so
        a real tab switch cannot be staged there. This pins the wiring.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "document.addEventListener('visibilitychange'" in source

        block = source[source.index("document.addEventListener('visibilitychange'") :]
        block = block[: block.index("\n  });")]

        assert "document.hidden" in block
        assert "standDown(" in block
        assert "stopSpeaking()" in block
        # Coming back resumes only what hiding stopped. `suspended` means another
        # screen owns the microphone, and reopening underneath that is two recognisers
        # fighting for one device.
        assert "pausedByHiding" in block
        assert "!suspended" in block

    def test_it_stops_when_the_guest_leaves(self):
        """Privacy control, not tidiness."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        left = source.index("'ashos:guest-left'")
        assert "standDown(" in source[left : left + 200]

    def test_it_never_reopens_over_its_own_answer(self):
        """Both engines have to be checked: the server path's speak() resolves when
        playback STARTS, so the promise alone is not enough."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        guard = source[source.index("const stillTalking") : source.index("if (stillTalking)")]
        assert "window.speechSynthesis.speaking" in guard
        assert "player.ended" in guard

    def test_a_broken_microphone_cannot_spin(self):
        """Reopening on every failure would be a tight loop against the device.

        The loop is now bounded by the DELAY rather than by giving up: each failure
        waits longer, up to RETRY_MAX_MS, and the watchdog that catches silent deaths
        ticks in seconds. A genuinely broken device is therefore poked every few
        seconds instead of continuously — and a working one that hiccuped six times is
        still listening, which is the whole point.
        """
        source = KIOSK_JS.read_text(encoding="utf-8")

        assert "Math.min(RETRY_STEP_MS * (restarts + 1), RETRY_MAX_MS)" in source

        def number(name: str) -> int:
            found = re.search(rf"{name} = (\d+)", source)
            assert found, name
            return int(found.group(1))

        step = number("RETRY_STEP_MS")
        ceiling = number("RETRY_MAX_MS")
        watchdog = number("MIC_WATCHDOG_MS")

        assert step >= 250
        assert ceiling >= step
        assert watchdog >= 1000

    def test_a_refusal_is_not_asked_twice(self):
        source = KIOSK_JS.read_text(encoding="utf-8")
        denied = source.index("'not-allowed'")
        assert "autoListen = false;" in source[denied : denied + 300]

    def test_no_speech_is_not_reported_as_an_error_while_hands_free(self):
        """Nobody talking yet is the normal state, not a fault to show the guest."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("'no-speech'") : source.index("'aborted'")]
        assert "if (!autoListen)" in block

    def test_only_actually_hearing_a_voice_blinks(self):
        """A microphone that looks busy the whole time tells the guest nothing."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "recognition.onspeechstart" in source
        assert "add('is-hearing')" in source
        assert "recognition.onspeechend" in source

        css = (Path(__file__).parents[2] / "static" / "css" / "kiosk.css").read_text(
            encoding="utf-8"
        )
        # Armed breathes slowly; hearing blinks. Different meanings, different looks.
        assert "mic-button.is-armed" in css
        assert "mic-breathe" in css
        assert "mic-button.is-hearing" in css
        assert "mic-blink" in css

    def test_reduced_motion_keeps_the_state_visible(self):
        """Somebody who asked for less motion still needs to know it is listening,
        so the state moves to colour rather than disappearing."""
        css = (Path(__file__).parents[2] / "static" / "css" / "kiosk.css").read_text(
            encoding="utf-8"
        )
        block = css[css.index("prefers-reduced-motion: reduce") :]
        assert "animation: none" in block
        assert "is-hearing" in block

    def test_the_button_becomes_stop_resume(self):
        """A guest who wants the microphone shut must be able to shut it."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        toggle = source[source.index("const toggleMic") : source.index("// --- Wiring")]
        assert "if (autoListen) standDown('standby');" in toggle
        assert "else startHandsFree();" in toggle


class TestTheMicrophoneNeedsNoTap:
    def test_permission_is_requested_on_load(self):
        """SpeechRecognition works without a gesture once the origin HAS the
        permission; a cold visit has to obtain it. getUserMedia raises the
        browser's own bubble, which is a browser dialog rather than a control on
        the page — so nothing on screen has to be tapped."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "const requestMicPermission" in source
        assert "await requestMicPermission();" in source
        assert "openConversation().then(startHandsFree)" in source

    def test_the_permission_stream_is_released_immediately(self):
        """The point is the permission, not the audio. Holding a stream open lights
        the recording indicator all day and fights the recogniser for the device."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const requestMicPermission") :]
        block = block[: block.index("const startHandsFree")]
        assert "stream.getTracks().forEach((track) => track.stop());" in block

    def test_a_failed_probe_does_not_stop_the_microphone_arming(self):
        """The probe can fail for reasons that say nothing about whether
        recognition will work — the device momentarily busy, or a remembered
        microphone since unplugged — and Chrome may hold the permission already.
        Refusing to arm on a failed probe left the terminal dead in exactly those
        cases."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const startHandsFree") :]
        block = block[: block.index("// --- Browser speech recognition")]

        assert "const granted = await requestMicPermission();" in block
        # It arms either way; the answer only changes how long it waits first.
        assert block.index("autoListen = true;") > block.index("const granted")
        assert "granted ? 250 : 0" in block

    def test_an_unplugged_remembered_microphone_is_not_a_dead_end(self):
        """An `exact` deviceId that has gone throws OverconstrainedError. Treating
        that as "no microphone here" would mute a terminal because of a device
        somebody swapped out last week."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        block = source[source.index("const requestMicPermission") :]
        block = block[: block.index("const startHandsFree")]

        assert "for (const audio of [audioConstraint(), true])" in block
        # ...but a real refusal still stops immediately rather than retrying.
        assert "NotAllowedError" in block

    def test_recognition_is_not_started_on_top_of_a_closing_stream(self):
        """Which is what produces 'audio-capture' in the first place."""
        source = KIOSK_JS.read_text(encoding="utf-8")
        assert "granted ? 250 : 0" in source


class TestTheMicrophoneIsAlwaysVisible:
    def test_it_sits_under_the_waveform_it_belongs_to(self, client, hotel):
        """It used to be position:fixed so a long conversation could not cover it.
        That problem is gone — the page does not scroll and the conversation is a
        bounded scroller — and floating cost a collision on every attempt: over the
        Restaurant card, then over the text input, then over the language chip. A
        control that lands on another control is worse than one that scrolls."""
        body = kiosk_html(client, hotel)
        assert "mic-dock" in body

        css = (Path(__file__).parents[2] / "static" / "css" / "kiosk.css").read_text(
            encoding="utf-8"
        )
        block = css[css.index(".kiosk-mode .mic-dock {") :][:1200]
        assert "position: static" in block
        assert "position: fixed" not in block.split("*/")[-1]

    def test_nothing_below_it_has_to_leave_room_any_more(self):
        """The 92px strip under the composer existed only to keep a floating dock
        off the quick actions. In the flow it needs no clearance, and that height
        went back to the conversation."""
        css = (Path(__file__).parents[2] / "static" / "css" / "kiosk.css").read_text(
            encoding="utf-8"
        )
        assert ".kiosk-mode .kiosk-composer { margin-bottom: 12px; }" in css

    def test_no_dock_rule_escapes_a_body_class(self):
        """A floating dock over the staff console's dashboard would be an overlay
        nobody asked for, so no dock rule may apply everywhere — each one names the
        page it is for."""
        css = (Path(__file__).parents[2] / "static" / "css" / "kiosk.css").read_text(
            encoding="utf-8"
        )
        selectors = [
            line for line in css.splitlines() if "mic-dock" in line and line.rstrip().endswith("{")
        ]
        assert selectors
        for selector in selectors:
            assert ".kiosk-mode" in selector or ".booking-mode" in selector, selector

    def test_the_booking_page_dock_is_anchored_to_the_scene_not_the_viewport(self):
        """It sits at the bottom-left of the conversation column, which is a place in
        the scene — not a place on the screen.

        position:fixed would keep it under the guest's cursor while they scrolled
        through the room list and the bill, which is how the terminal's dock ended up
        on top of the Restaurant card, then the text input, then the language chip. A
        control that lands on another control is worse than one that scrolls away.
        """
        css = (Path(__file__).parents[2] / "static" / "css" / "kiosk.css").read_text(
            encoding="utf-8"
        )
        block = css[css.index(".booking-mode .mic-dock {") :]
        block = block[: block.index("}")]

        assert "position: absolute" in block
        assert "position: fixed" not in block
        # Out of the flow means the conversation has to be told to leave room, or the
        # newest answer ends up underneath it.
        assert ".booking-mode .scene__panel { padding-bottom: 118px; }" in css
