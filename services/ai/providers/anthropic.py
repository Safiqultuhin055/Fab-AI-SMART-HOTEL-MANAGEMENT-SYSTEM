"""Anthropic Messages API adapter.

Claude does not speak the OpenAI wire format, so it needs its own adapter
rather than a base-URL swap. Four differences matter:

* auth is ``x-api-key``, not ``Authorization: Bearer``
* an ``anthropic-version`` header is mandatory
* the system prompt is a **top-level ``system`` field**, not a message with
  ``role: "system"`` — sending it as a message is the classic mistake and Claude
  will either reject it or treat it as user text
* ``max_tokens`` is required, and the reply arrives as a list of content blocks

Everything else — retries, metering, fallback, budget caps — is handled by the
gateway, exactly as for every other provider.

Model ids are never hardcoded here: they come from ``ModelConfig.model_name``,
so switching Claude versions is a config change.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from apps.core.exceptions import AIError, AITimeout
from services.ai.base import ChatMessage, ChatResult, ResolvedModel, Usage

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator, Iterator, Sequence

logger = logging.getLogger("ashos.ai")

API_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com/v1"


class AnthropicProvider:
    name = "anthropic"

    # --- plumbing -------------------------------------------------------------

    @staticmethod
    def _headers(model: ResolvedModel) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": model.api_key,
            "anthropic-version": model.extra.get("api_version", API_VERSION),
        }

    @staticmethod
    def _url(model: ResolvedModel) -> str:
        base = (model.base_url or DEFAULT_BASE_URL).rstrip("/")
        return f"{base}/messages"

    @staticmethod
    def _split(messages: Sequence[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        """Separate the system prompt from the conversation.

        Consecutive same-role turns are merged: the API rejects two user
        messages in a row, and our orchestrator can produce that shape when a
        guardrail injects a message between turns.
        """
        system_parts: list[str] = []
        turns: list[dict[str, Any]] = []

        for message in messages:
            role = str(message.role)
            if role == "system":
                system_parts.append(message.content)
                continue

            mapped = "assistant" if role == "assistant" else "user"
            if turns and turns[-1]["role"] == mapped:
                turns[-1]["content"] += "\n\n" + message.content
            else:
                turns.append({"role": mapped, "content": message.content})

        # The API also requires the first turn to be from the user.
        if turns and turns[0]["role"] != "user":
            turns.insert(0, {"role": "user", "content": "(conversation continues)"})

        return "\n\n".join(system_parts), turns

    def _payload(self, messages: Sequence[ChatMessage], model: ResolvedModel, **extra) -> dict:
        system, turns = self._split(messages)
        payload: dict[str, Any] = {
            "model": model.model_name,
            # Required by Anthropic, unlike OpenAI where it is optional.
            "max_tokens": model.max_tokens or 1024,
            "messages": turns,
            "temperature": model.temperature,
            **model.extra.get("params", {}),
            **extra,
        }
        if system:
            payload["system"] = system
        return payload

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = response.text[:500]
        try:
            body = response.json()
            detail = body.get("error", {}).get("message", detail)
        except (ValueError, AttributeError):
            # Non-JSON error body (a proxy or gateway page). Keep the raw text.
            logger.debug("non-JSON error body from Anthropic")
        raise AIError(
            f"Anthropic returned {response.status_code}: {detail}",
            status_code=response.status_code,
        )

    @staticmethod
    def _text_of(data: dict) -> str:
        """Concatenate the text blocks; ignore tool-use blocks for now."""
        return "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )

    # --- chat -----------------------------------------------------------------

    def chat(self, messages: Sequence[ChatMessage], model: ResolvedModel) -> ChatResult:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=model.timeout_s) as client:
                response = client.post(
                    self._url(model),
                    headers=self._headers(model),
                    json=self._payload(messages, model),
                )
        except httpx.TimeoutException as exc:
            raise AITimeout(f"{model.model_name} timed out after {model.timeout_s}s") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"transport error talking to Anthropic: {exc}") from exc

        self._raise_for(response)
        data = response.json()
        usage = data.get("usage") or {}

        return ChatResult(
            text=self._text_of(data),
            usage=Usage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            ),
            model=data.get("model", model.model_name),
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason=data.get("stop_reason", ""),
            raw=data,
        )

    def stream(self, messages: Sequence[ChatMessage], model: ResolvedModel) -> Iterator[str]:
        payload = self._payload(messages, model, stream=True)
        try:
            with (
                httpx.Client(timeout=model.timeout_s) as client,
                client.stream(
                    "POST", self._url(model), headers=self._headers(model), json=payload
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
        payload = self._payload(messages, model, stream=True)
        try:
            async with (
                httpx.AsyncClient(timeout=model.timeout_s) as client,
                client.stream(
                    "POST", self._url(model), headers=self._headers(model), json=payload
                ) as response,
            ):
                self._raise_for(response)
                async for line in response.aiter_lines():
                    for chunk in _iter_sse([line]):
                        yield chunk
        except httpx.TimeoutException as exc:
            raise AITimeout(f"{model.model_name} stream timed out") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"transport error during stream: {exc}") from exc


def _iter_sse(lines) -> Iterator[str]:
    """Pull text out of Anthropic's SSE stream.

    The stream carries several event types; only ``content_block_delta`` with a
    ``text_delta`` carries visible output.
    """
    for raw in lines:
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[5:].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield delta["text"]
