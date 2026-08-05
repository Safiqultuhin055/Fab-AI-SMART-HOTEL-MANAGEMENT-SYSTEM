"""Deterministic offline provider.

Two jobs:
  1. tests — no network, no key, identical output every run (config/settings/test.py);
  2. demos and the dev environment before anyone has a paid API key.

Embeddings are hash-derived but *stable and normalised*, so cosine similarity
behaves sensibly: the same text always yields the same vector, and different
texts yield different ones. That is enough to exercise pgvector queries, index
creation and retrieval plumbing without a provider.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import TYPE_CHECKING

from services.ai.base import (
    ChatMessage,
    ChatResult,
    EmbeddingResult,
    ResolvedModel,
    SpeechResult,
    TranscriptionResult,
    Usage,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator, Iterator, Sequence


class FakeProvider:
    name = "fake"

    def chat(self, messages: Sequence[ChatMessage], model: ResolvedModel) -> ChatResult:
        last_user = next((m.content for m in reversed(list(messages)) if str(m.role) == "user"), "")
        text = f"[fake:{model.model_name}] {last_user.strip()[:200]}"
        return ChatResult(
            text=text,
            usage=Usage(input_tokens=_tokens(messages), output_tokens=len(text) // 4),
            model=model.model_name or "fake-model",
            provider=self.name,
            latency_ms=1,
            finish_reason="stop",
        )

    def stream(self, messages: Sequence[ChatMessage], model: ResolvedModel) -> Iterator[str]:
        for word in self.chat(messages, model).text.split(" "):
            yield word + " "

    async def astream(
        self, messages: Sequence[ChatMessage], model: ResolvedModel
    ) -> AsyncIterator[str]:
        for word in self.chat(messages, model).text.split(" "):
            yield word + " "

    def embed(self, texts: Sequence[str], model: ResolvedModel) -> EmbeddingResult:
        dim = model.dimension or 1536
        vectors = [_stable_vector(text, dim) for text in texts]
        return EmbeddingResult(
            vectors=vectors,
            model=model.model_name or "fake-embedding",
            provider=self.name,
            dimension=dim,
            usage=Usage(input_tokens=sum(len(t) // 4 for t in texts)),
            latency_ms=1,
        )

    def embed_image(self, images: Sequence[bytes], model: ResolvedModel) -> EmbeddingResult:
        dim = model.dimension or 512
        vectors = [_stable_vector(hashlib.sha256(img).hexdigest(), dim) for img in images]
        return EmbeddingResult(
            vectors=vectors, model="fake-clip", provider=self.name, dimension=dim, latency_ms=1
        )

    def embed_text(self, texts: Sequence[str], model: ResolvedModel) -> EmbeddingResult:
        dim = model.dimension or 512
        return EmbeddingResult(
            vectors=[_stable_vector(t, dim) for t in texts],
            model="fake-clip",
            provider=self.name,
            dimension=dim,
            latency_ms=1,
        )

    def transcribe(
        self, audio: bytes, model: ResolvedModel, language: str = ""
    ) -> TranscriptionResult:
        return TranscriptionResult(
            text=f"[fake transcript of {len(audio)} bytes]",
            language=language or "en",
            model="fake-whisper",
            provider=self.name,
            latency_ms=1,
        )

    def speak(self, text: str, model: ResolvedModel, voice: str = "") -> SpeechResult:
        # Minimal silent WAV header so callers can still write a playable file.
        return SpeechResult(
            audio=b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt ",
            mime_type="audio/wav",
            model="fake-tts",
            provider=self.name,
            latency_ms=1,
        )


def _tokens(messages: Sequence[ChatMessage]) -> int:
    return sum(len(m.content) // 4 for m in messages)


def _stable_vector(text: str, dim: int) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture, not crypto
    raw = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]
