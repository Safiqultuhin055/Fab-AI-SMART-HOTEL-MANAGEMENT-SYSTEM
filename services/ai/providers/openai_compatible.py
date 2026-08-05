"""OpenAI-compatible HTTP provider.

Covers OpenAI, Azure OpenAI, Groq, Together, DeepInfra, OpenRouter, vLLM,
Ollama's OpenAI shim and LM Studio — anything speaking ``/chat/completions``
and ``/embeddings``.

Implemented with raw ``httpx`` rather than the ``openai`` SDK on purpose: the
SDK pins its own retry/timeout behaviour and its own auth assumptions, and we
already own those at the gateway. One less dependency that can break a hotel's
reception at 3am.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from apps.core.exceptions import AIError, AITimeout
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

logger = logging.getLogger("ashos.ai")


class OpenAICompatibleProvider:
    name = "openai_compatible"

    # --- plumbing -------------------------------------------------------------

    @staticmethod
    def _headers(model: ResolvedModel) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if model.api_key:
            headers["Authorization"] = f"Bearer {model.api_key}"
        # Azure deployments authenticate with a different header name.
        if model.extra.get("api_key_header"):
            headers[model.extra["api_key_header"]] = model.api_key
        return headers

    @staticmethod
    def _url(model: ResolvedModel, path: str) -> str:
        base = (model.base_url or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    @staticmethod
    def _payload(messages: Sequence[ChatMessage], model: ResolvedModel, **extra: Any) -> dict:
        return {
            "model": model.model_name,
            "messages": [m.as_dict() for m in messages],
            "temperature": model.temperature,
            "max_tokens": model.max_tokens,
            **model.extra.get("params", {}),
            **extra,
        }

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        body = response.text[:500]
        raise AIError(
            f"provider returned {response.status_code}",
            status_code=response.status_code,
            body=body,
        )

    # --- chat -----------------------------------------------------------------

    def chat(self, messages: Sequence[ChatMessage], model: ResolvedModel) -> ChatResult:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=model.timeout_s) as client:
                response = client.post(
                    self._url(model, "chat/completions"),
                    headers=self._headers(model),
                    json=self._payload(messages, model),
                )
        except httpx.TimeoutException as exc:
            raise AITimeout(f"{model.model_name} timed out after {model.timeout_s}s") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"transport error talking to {model.provider}: {exc}") from exc

        self._raise_for(response)
        data = response.json()
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}

        return ChatResult(
            text=(choice.get("message") or {}).get("content", "") or "",
            usage=Usage(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            ),
            model=data.get("model", model.model_name),
            provider=model.provider,
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason", ""),
            raw=data,
        )

    def stream(self, messages: Sequence[ChatMessage], model: ResolvedModel) -> Iterator[str]:
        """Yield content deltas.

        Streaming is not a nicety here: goal.txt §6 requires a first token in
        under 1.5s, and a guest staring at a blank kiosk for four seconds has
        already decided the AI is broken.
        """
        payload = self._payload(messages, model, stream=True)
        try:
            with (
                httpx.Client(timeout=model.timeout_s) as client,
                client.stream(
                    "POST",
                    self._url(model, "chat/completions"),
                    headers=self._headers(model),
                    json=payload,
                ) as response,
            ):
                self._raise_for(response)
                yield from _iter_sse(response.iter_lines())
        except httpx.TimeoutException as exc:
            raise AITimeout(f"{model.model_name} stream timed out") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"transport error during stream: {exc}") from exc

    async def astream(
        self, messages: Sequence[ChatMessage], model: ResolvedModel
    ) -> AsyncIterator[str]:
        """Async variant used by the voice/chat WebSocket consumers."""
        payload = self._payload(messages, model, stream=True)
        try:
            async with (
                httpx.AsyncClient(timeout=model.timeout_s) as client,
                client.stream(
                    "POST",
                    self._url(model, "chat/completions"),
                    headers=self._headers(model),
                    json=payload,
                ) as response,
            ):
                self._raise_for(response)
                async for line in response.aiter_lines():
                    # _iter_sse is a sync generator over one line at a time, so
                    # the async loop stays the thing that awaits the socket.
                    for chunk in _iter_sse([line]):
                        yield chunk
        except httpx.TimeoutException as exc:
            raise AITimeout(f"{model.model_name} stream timed out") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"transport error during stream: {exc}") from exc

    # --- embeddings -----------------------------------------------------------

    def embed(self, texts: Sequence[str], model: ResolvedModel) -> EmbeddingResult:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=model.timeout_s) as client:
                response = client.post(
                    self._url(model, "embeddings"),
                    headers=self._headers(model),
                    json={"model": model.model_name, "input": list(texts)},
                )
        except httpx.TimeoutException as exc:
            raise AITimeout("embedding request timed out") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"transport error during embed: {exc}") from exc

        self._raise_for(response)
        data = response.json()
        vectors = [item["embedding"] for item in sorted(data["data"], key=lambda d: d["index"])]
        usage = data.get("usage") or {}

        return EmbeddingResult(
            vectors=vectors,
            model=data.get("model", model.model_name),
            provider=model.provider,
            dimension=len(vectors[0]) if vectors else 0,
            usage=Usage(input_tokens=usage.get("prompt_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # --- speech ---------------------------------------------------------------

    def transcribe(
        self, audio: bytes, model: ResolvedModel, language: str = ""
    ) -> TranscriptionResult:
        started = time.perf_counter()
        files = {"file": ("audio.webm", audio, "audio/webm")}
        data: dict[str, str] = {"model": model.model_name}
        if language:
            data["language"] = language

        headers = self._headers(model)
        headers.pop("Content-Type", None)  # multipart boundary is set by httpx

        try:
            with httpx.Client(timeout=model.timeout_s) as client:
                response = client.post(
                    self._url(model, "audio/transcriptions"),
                    headers=headers,
                    data=data,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise AITimeout("transcription timed out") from exc

        self._raise_for(response)
        body = response.json()
        return TranscriptionResult(
            text=body.get("text", ""),
            language=body.get("language", language),
            model=model.model_name,
            provider=model.provider,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def speak(self, text: str, model: ResolvedModel, voice: str = "") -> SpeechResult:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=model.timeout_s) as client:
                response = client.post(
                    self._url(model, "audio/speech"),
                    headers=self._headers(model),
                    json={
                        "model": model.model_name,
                        "input": text,
                        "voice": voice or model.extra.get("voice", "alloy"),
                        "response_format": "mp3",
                    },
                )
        except httpx.TimeoutException as exc:
            raise AITimeout("speech synthesis timed out") from exc

        self._raise_for(response)
        return SpeechResult(
            audio=response.content,
            mime_type="audio/mpeg",
            model=model.model_name,
            provider=model.provider,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def _iter_sse(lines) -> Iterator[str]:
    """Extract content deltas from an SSE stream."""
    for raw in lines:
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[5:].strip()
        if payload == "[DONE]":
            return
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []):
            delta = (choice.get("delta") or {}).get("content")
            if delta:
                yield delta
