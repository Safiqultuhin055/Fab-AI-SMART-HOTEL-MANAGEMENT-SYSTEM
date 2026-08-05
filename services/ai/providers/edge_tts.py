"""Text to speech with no API key, in Bangla and English.

Why this exists. The kiosk fell back to the browser's own ``speechSynthesis`` when
no TTS provider was configured, which works for English on any Windows machine —
Zira is installed by default — and cannot work for Bangla on any Windows machine,
because no Bangla voice ships with the OS. So a Bangla property read every answer
aloud in English and stayed silent in Bangla: the exact opposite of what it was
for.

This adapter reaches Microsoft's Edge read-aloud service, which has neural voices
for both, including ``bn-BD-NabanitaNeural`` — a female Bangla voice, which is
what the product asks for. No key, no signup, no per-character bill.

*** THE TRADE, STATED PLAINLY ***

That endpoint is not a documented, contracted API. It is what the Edge browser's
own read-aloud feature uses, reached through the ``edge-tts`` package. Microsoft
can change or block it without notice, and there is no SLA behind it and nothing
to escalate to if it stops.

That is an acceptable trade for a pilot, and a bad one for a hotel group that has
signed a service agreement. For production, configure a contracted provider in AI
Center (OpenAI ``tts-1``, Azure Speech, ElevenLabs) — it takes precedence
automatically, and this becomes the fallback rather than the plan. The gateway
already treats a failing provider as a reason to fall back, not to go silent.
"""

from __future__ import annotations

import asyncio
import logging
import time

from apps.core.exceptions import AIError
from services.ai.base import ResolvedModel, SpeechResult

logger = logging.getLogger("ashos.ai")

#: language + preferred gender -> Edge voice. Only the pairs this reception
#: actually speaks; anything else falls through to English, because a voice that
#: cannot pronounce the text is worse than one in the wrong language being obvious
#: about it.
VOICES: dict[tuple[str, str], str] = {
    ("bn", "female"): "bn-BD-NabanitaNeural",
    ("bn", "male"): "bn-BD-PradeepNeural",
    ("en", "female"): "en-US-AvaNeural",
    ("en", "male"): "en-US-AndrewNeural",
}

DEFAULT_GENDER = "female"

#: Slightly under conversational pace. A room rate read at speed to somebody who
#: has just walked in off the street with a suitcase is a rate they will ask for
#: again.
DEFAULT_RATE = "-5%"


class EdgeTTSProvider:
    name = "edge_tts"

    def speak(
        self,
        text: str,
        model: ResolvedModel,
        voice: str = "",
        language: str = "",
    ) -> SpeechResult:
        started = time.perf_counter()
        chosen = voice or self.voice_for(language, model.extra.get("gender", DEFAULT_GENDER))

        try:
            audio = asyncio.run(self._synthesise(text, chosen, model))
        except RuntimeError as exc:
            # Already inside an event loop — a Channels consumer, not a view. Run
            # the coroutine on its own loop in a worker thread rather than failing.
            if "event loop is running" not in str(exc):
                raise AIError(f"edge TTS failed: {exc}") from exc
            audio = self._synthesise_threaded(text, chosen, model)
        except Exception as exc:  # noqa: BLE001 - one adapter must not decide policy
            raise AIError(f"edge TTS failed: {exc}") from exc

        if not audio:
            raise AIError("edge TTS returned no audio")

        return SpeechResult(
            audio=audio,
            mime_type="audio/mpeg",
            model=chosen,
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ------------------------------------------------------------------
    @staticmethod
    def voice_for(language: str, gender: str = DEFAULT_GENDER) -> str:
        base = (language or "en").split("-")[0].lower()
        want = (gender or DEFAULT_GENDER).lower()
        if want not in {"female", "male"}:
            want = DEFAULT_GENDER
        return VOICES.get((base, want)) or VOICES[("en", want)]

    async def _synthesise(self, text: str, voice: str, model: ResolvedModel) -> bytes:
        import edge_tts

        rate = model.extra.get("rate", DEFAULT_RATE)
        communicate = edge_tts.Communicate(text, voice, rate=rate)

        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                chunks.append(chunk["data"])
        return b"".join(chunks)

    def _synthesise_threaded(self, text: str, voice: str, model: ResolvedModel) -> bytes:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self._synthesise(text, voice, model)).result()
