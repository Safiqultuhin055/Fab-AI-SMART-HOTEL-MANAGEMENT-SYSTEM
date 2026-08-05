"""Keyless text to speech, and the reason it exists.

The browser's own speechSynthesis can read English on any Windows machine — Zira
ships with the OS — and cannot read Bangla on any Windows machine, because no
Bangla voice does. So a Bangla property read every answer aloud in English and
went silent in Bangla: the exact opposite of the point.

This adapter has neural voices for both, including a female Bangla one, and needs
no key. What it does NOT have is a contract, which is why these tests also pin the
fallback behaviour: a contracted provider must always win, and a keyless one must
never be the thing that decides a guest hears nothing.
"""

from __future__ import annotations

import pytest

from apps.ai_center.models import ModelConfig, ModelKind, Provider
from services.ai import gateway, registry
from services.ai.base import ResolvedModel, SpeechResult
from services.ai.providers.edge_tts import DEFAULT_GENDER, VOICES, EdgeTTSProvider

pytestmark = pytest.mark.django_db


@pytest.fixture
def model():
    return ResolvedModel(kind="tts", provider="edge_tts", model_name="edge-neural", timeout_s=30)


@pytest.fixture
def platform_edge_row():
    row = ModelConfig.all_objects.create(
        tenant=None,
        kind=ModelKind.TTS,
        name="Edge",
        provider=Provider.EDGE_TTS,
        model_name="edge-neural",
        api_key="",
        is_default=True,
        is_active=True,
    )
    registry.invalidate()
    yield row
    registry.invalidate()


class TestVoiceChoice:
    def test_bangla_gets_a_bangla_voice(self):
        assert EdgeTTSProvider.voice_for("bn") == "bn-BD-NabanitaNeural"
        assert EdgeTTSProvider.voice_for("bn-BD") == "bn-BD-NabanitaNeural"

    def test_and_it_is_female_by_default(self):
        """What the product asks for, in both languages."""
        assert DEFAULT_GENDER == "female"
        for language in ("bn", "en"):
            assert EdgeTTSProvider.voice_for(language) == VOICES[(language, "female")]

    def test_male_is_available_when_asked_for(self):
        assert EdgeTTSProvider.voice_for("bn", "male") == "bn-BD-PradeepNeural"
        assert EdgeTTSProvider.voice_for("en", "male") == "en-US-AndrewNeural"

    def test_an_unknown_gender_does_not_lose_the_language(self):
        assert EdgeTTSProvider.voice_for("bn", "robot") == "bn-BD-NabanitaNeural"

    def test_a_language_with_no_voice_falls_back_to_english(self):
        """A voice that cannot pronounce the text is worse than one that is
        obviously the wrong language."""
        assert EdgeTTSProvider.voice_for("fr") == VOICES[("en", "female")]
        assert EdgeTTSProvider.voice_for("") == VOICES[("en", "female")]


class TestItIsTreatedAsKeyless:
    def test_no_credential_is_required(self):
        assert "edge_tts" in gateway.KEYLESS_PROVIDERS

    def test_a_configured_row_counts_as_ready(self, hotel, platform_edge_row):
        assert gateway.capability_ready("tts", str(hotel.pk)) is True
        assert gateway.speech_status(str(hotel.pk), language="bn")["tts"] is True

    def test_a_contracted_provider_still_wins(self, hotel, platform_edge_row):
        """This is a pilot answer, not a plan. A paid row must take precedence."""
        ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.TTS,
            name="OpenAI",
            provider=Provider.OPENAI,
            model_name="tts-1",
            api_key="sk-paid",
            is_default=True,
            is_active=True,
        )
        registry.invalidate()

        assert registry.resolve("tts", str(hotel.pk)).provider == Provider.OPENAI

    def test_the_adapter_is_reachable_from_the_registry(self):
        assert isinstance(registry.get_provider("edge_tts"), EdgeTTSProvider)


class TestTheLanguageReachesTheAdapter:
    def test_speak_forwards_it(self, hotel, platform_edge_row, monkeypatch):
        """Without this the adapter cannot pick a Bangla voice, which was the whole
        bug: it had one and never got to use it."""
        seen = {}

        class Spy:
            def speak(self, text, model, voice="", language=""):
                seen.update(text=text, voice=voice, language=language)
                return SpeechResult(audio=b"x", mime_type="audio/mpeg")

        monkeypatch.setattr(registry, "get_provider", lambda name: Spy())
        gateway.speak("চেক আউট", language="bn", tenant_id=str(hotel.pk))

        assert seen["language"] == "bn"

    def test_an_older_adapter_without_the_argument_still_speaks(self, hotel, monkeypatch):
        """Losing the voice choice is better than losing the audio."""

        class Legacy:
            def speak(self, text, model, voice=""):
                return SpeechResult(audio=b"legacy", mime_type="audio/mpeg")

        monkeypatch.setattr(registry, "get_provider", lambda name: Legacy())
        result = gateway.speak("hello", language="bn", tenant_id=str(hotel.pk))

        assert result.audio == b"legacy"


class TestTheEndpointPassesItOn:
    @pytest.fixture
    def spy_speak(self, monkeypatch):
        seen = {}

        def spy(text, **kwargs):
            seen.update(kwargs)
            return SpeechResult(audio=b"x", mime_type="audio/mpeg")

        monkeypatch.setattr(gateway, "speak", spy)
        return seen

    def test_the_hotels_language_is_the_default(self, client, hotel, spy_speak):
        hotel.kiosk_language = "bn"
        hotel.save(update_fields=["kiosk_language"])

        response = client.post(
            "/api/v1/reception/speak/",
            {"text": "চেক আউট কখন"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=hotel.code,
        )

        assert response.status_code == 200
        assert spy_speak["language"] == "bn"

    def test_an_explicit_language_wins_over_the_property(self, client, hotel, spy_speak):
        """The guest may have switched since; the language of the ANSWER counts."""
        hotel.kiosk_language = "bn"
        hotel.save(update_fields=["kiosk_language"])

        client.post(
            "/api/v1/reception/speak/",
            {"text": "check out", "language": "en-US"},
            content_type="application/json",
            HTTP_X_HOTEL_CODE=hotel.code,
        )
        assert spy_speak["language"] == "en-US"


class TestTheClientAsksForTheRightLanguage:
    def test_it_sends_the_language_of_the_answer(self):
        from pathlib import Path

        source = (Path(__file__).parents[2] / "static" / "js" / "kiosk.js").read_text(
            encoding="utf-8"
        )
        block = source[source.index("await post(API.speak") :][:400]
        assert "language: speechLang" in block


@pytest.mark.ai_eval
class TestItReallySynthesises:
    """Reaches Microsoft. Marked ai_eval so the normal suite stays offline."""

    def test_bangla_comes_back_as_audio(self, model):
        result = EdgeTTSProvider().speak("চেক-আউটের সময় দুপুর ১২টা।", model, language="bn")

        assert result.model == "bn-BD-NabanitaNeural"
        assert result.mime_type == "audio/mpeg"
        assert len(result.audio) > 5000

    def test_english_does_too(self, model):
        result = EdgeTTSProvider().speak("Check-out is at noon.", model, language="en")

        assert result.model == "en-US-AvaNeural"
        assert len(result.audio) > 5000
