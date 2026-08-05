"""Provider-neutral data types and protocols.

These shapes are the contract between ASHOS and *any* AI backend. A provider
module's job is to translate its vendor's wire format into these, and nothing
else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator, Iterator, Sequence


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(slots=True)
class ChatMessage:
    role: Role | str
    content: str
    name: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": str(self.role), "content": self.content}
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class ChatResult:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")
    finish_reason: str = ""
    fallback_used: bool = False
    cache_hit: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str = ""
    provider: str = ""
    dimension: int = 0
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    cost_usd: Decimal = Decimal("0")

    @property
    def vector(self) -> list[float]:
        """Convenience for the common single-input case."""
        return self.vectors[0] if self.vectors else []


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    language: str = ""
    duration_s: float = 0.0
    model: str = ""
    provider: str = ""
    latency_ms: int = 0


@dataclass(slots=True)
class SpeechResult:
    audio: bytes
    mime_type: str = "audio/mpeg"
    model: str = ""
    provider: str = ""
    latency_ms: int = 0


@dataclass(slots=True)
class ResolvedModel:
    """A concrete backend choice, resolved from DB config or env defaults."""

    kind: str
    provider: str
    model_name: str
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int = 1024
    timeout_s: int = 30
    dimension: int | None = None
    cost_per_1k_input: Decimal = Decimal("0")
    cost_per_1k_output: Decimal = Decimal("0")
    fallback: ResolvedModel | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    config_id: str = ""


# ==============================================================================
# Provider protocols — a provider implements only what it supports.
# ==============================================================================


@runtime_checkable
class ChatProvider(Protocol):
    def chat(self, messages: Sequence[ChatMessage], model: ResolvedModel) -> ChatResult: ...

    def stream(self, messages: Sequence[ChatMessage], model: ResolvedModel) -> Iterator[str]: ...

    async def astream(
        self, messages: Sequence[ChatMessage], model: ResolvedModel
    ) -> AsyncIterator[str]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str], model: ResolvedModel) -> EmbeddingResult: ...


@runtime_checkable
class ImageEmbeddingProvider(Protocol):
    def embed_image(self, images: Sequence[bytes], model: ResolvedModel) -> EmbeddingResult: ...

    def embed_text(self, texts: Sequence[str], model: ResolvedModel) -> EmbeddingResult: ...


@runtime_checkable
class SpeechToTextProvider(Protocol):
    def transcribe(
        self, audio: bytes, model: ResolvedModel, language: str = ""
    ) -> TranscriptionResult: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    def speak(self, text: str, model: ResolvedModel, voice: str = "") -> SpeechResult: ...
