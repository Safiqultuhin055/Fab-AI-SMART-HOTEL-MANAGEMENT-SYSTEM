"""The public AI surface. Everything in ASHOS calls these functions.

Responsibilities the gateway owns so no caller has to:
  * kill switch and per-hotel AI enablement (goal.txt D12)
  * daily budget cap enforcement (R3)
  * model resolution from AI Center or env defaults (D07)
  * retry, timeout and fallback-model routing
  * metering: tokens, cost, latency, success (R3, §8)
  * dimension validation on anything that produces a vector (D08)

If you find yourself adding an ``if provider == ...`` anywhere else in the
codebase, it belongs here instead.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from apps.core.context import current_tenant_id
from apps.core.exceptions import AIBudgetExceeded, AIDisabled, AIError, AITimeout
from services.ai import metering, registry
from services.ai.base import ChatMessage, ChatResult, EmbeddingResult, Role

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator, Iterator, Sequence

    from services.ai.base import ResolvedModel, SpeechResult, TranscriptionResult

logger = logging.getLogger("ashos.ai")

_TRANSIENT = (AITimeout, AIError)

# Backends that legitimately run without a credential: local inference and the
# test double. Everything else with an empty key is a misconfiguration, not a
# working setup.
KEYLESS_PROVIDERS = frozenset(
    {"fake", "local", "local_clip", "local_insightface", "paddleocr", "edge_tts"}
)


# ==============================================================================
# Guards
# ==============================================================================


def is_available(tenant_id: str | None = None) -> bool:
    """Cheap check for UI badges ('AI Concierge: Online' in the prototype)."""
    try:
        _assert_available(tenant_id)
    except AIDisabled:
        return False
    return True


def is_configured(tenant_id: str | None = None) -> bool:
    """Is there actually a usable backend behind the switch?

    ``is_available`` only answers "is AI turned on". A hotel can have AI enabled
    with no API key, in which case every call fails. Showing a green
    "AI Concierge Online" badge in that state is a lie the staff will believe
    until a guest asks the kiosk a question.
    """
    model = registry.resolve("llm", tenant_id)
    return bool(model.api_key) or model.provider in KEYLESS_PROVIDERS


def status(tenant_id: str | None = None) -> dict[str, str]:
    """AI posture for UI badges.

    Four states, not three. "A key is present" is not the same as "it works" —
    a wrong or expired key produced a green badge and a broken kiosk, so a
    recorded provider failure now shows amber until someone fixes or clears it.
    """
    if not is_available(tenant_id):
        return {"state": "disabled", "label": "Manual mode"}
    if not is_configured(tenant_id):
        return {"state": "unconfigured", "label": "AI not configured"}
    if last_provider_error(tenant_id):
        return {"state": "degraded", "label": "AI failing — answering from hotel data"}
    return {"state": "online", "label": "AI Concierge Online"}


def last_provider_error(tenant_id: str | None = None) -> str:
    """The most recent failure recorded against the default chat model."""
    tenant_id = tenant_id or current_tenant_id()
    if not tenant_id:
        return ""
    try:
        from apps.ai_center.models import ModelConfig, ModelKind

        return (
            ModelConfig.all_objects.filter(
                tenant_id=tenant_id, kind=ModelKind.LLM, is_default=True, is_deleted=False
            )
            .values_list("last_error", flat=True)
            .first()
            or ""
        )
    except Exception:  # noqa: BLE001 - badge must never break a page render
        return ""


def capability_ready(kind: str, tenant_id: str | None = None) -> bool:
    """Is *this specific* capability usable?

    ``is_configured`` only inspects the LLM. Deriving the microphone's
    availability from that was wrong: a hotel can have a working chat model and
    no speech key at all, and the kiosk would then offer a mic that 401s on the
    first press — which is exactly what happened.
    """
    if not is_available(tenant_id):
        return False
    try:
        model = registry.resolve(kind, tenant_id)
    except (KeyError, ValueError):
        return False
    if not model.model_name:
        return False
    return bool(model.api_key) or model.provider in KEYLESS_PROVIDERS


#: Guest language → the tag the browser's speech engines require. They reject a
#: bare "bn"; a region is mandatory.
BCP47 = {
    "bn": "bn-BD",
    "en": "en-US",
    "hi": "hi-IN",
    "ar": "ar-SA",
    "zh": "zh-CN",
}


def speech_status(tenant_id: str | None = None, language: str = "") -> dict[str, Any]:
    """What the kiosk needs to know before drawing the mic and speaker.

    ``stt``/``tts`` mean "a *server* provider is configured". They are no longer
    the whole answer: with no key the kiosk falls back to the browser's own
    speech engines, which cost nothing and speak Bangla. So these flags now
    select the engine rather than deciding whether voice exists at all.
    """
    base = (language or "en").split("-")[0]
    return {
        "stt": capability_ready("stt", tenant_id),
        "tts": capability_ready("tts", tenant_id),
        "language": base,
        "bcp47": BCP47.get(base, "en-US"),
    }


def _assert_available(tenant_id: str | None = None) -> None:
    if not settings.AI["ENABLED"] or settings.AI["KILL_SWITCH"]:
        raise AIDisabled("AI is globally disabled.")

    tenant_id = tenant_id or current_tenant_id()
    if not tenant_id:
        return  # platform-level call (e.g. health probe)

    from apps.tenants.models import Hotel

    hotel = (
        Hotel.all_objects.filter(pk=tenant_id)
        .only("is_active", "ai_enabled", "ai_kill_switch", "ai_daily_cost_cap_usd")
        .first()
    )
    if hotel is None:
        return
    if not hotel.ai_available:
        raise AIDisabled("AI is disabled for this hotel; reception is in manual mode.")

    cap = Decimal(str(hotel.ai_daily_cost_cap_usd or 0))
    if cap > 0 and metering.spend_today(str(tenant_id)) >= cap:
        raise AIBudgetExceeded(f"Daily AI budget of {cap} USD reached for this hotel.")


# ==============================================================================
# Chat
# ==============================================================================


def chat(
    messages: Sequence[ChatMessage] | Sequence[dict[str, Any]],
    *,
    module: str = "general",
    tenant_id: str | None = None,
    conversation_id: str = "",
    **overrides: Any,
) -> ChatResult:
    """Single-shot completion with metering, retry and fallback."""
    _assert_available(tenant_id)
    model = _apply_overrides(registry.resolve("llm", tenant_id), overrides)
    normalised = _normalise(messages)

    try:
        result = _call_with_fallback(
            model, lambda m: registry.get_provider(m.provider).chat(normalised, m)
        )
    except _TRANSIENT as exc:
        metering.record(
            module=module,
            model=model,
            usage=_empty_usage(),
            latency_ms=0,
            cost=Decimal("0"),
            success=False,
            error_code=getattr(exc, "code", "ai_error"),
            conversation_id=conversation_id,
        )
        raise

    result.cost_usd = metering.compute_cost(result.usage, model)
    metering.record(
        module=module,
        model=model,
        usage=result.usage,
        latency_ms=result.latency_ms,
        cost=result.cost_usd,
        fallback_used=result.fallback_used,
        conversation_id=conversation_id,
    )
    return result


def stream(
    messages: Sequence[ChatMessage] | Sequence[dict[str, Any]],
    *,
    module: str = "general",
    tenant_id: str | None = None,
    conversation_id: str = "",
    **overrides: Any,
) -> Iterator[str]:
    """Token stream for the kiosk and chat widget.

    Token accounting for streams is approximate — most OpenAI-compatible servers
    omit a usage block when streaming — so the metered figure is an estimate
    flagged as such rather than a silent zero.
    """
    _assert_available(tenant_id)
    model = _apply_overrides(registry.resolve("llm", tenant_id), overrides)
    normalised = _normalise(messages)
    provider = registry.get_provider(model.provider)

    chars = 0
    for chunk in provider.stream(normalised, model):
        chars += len(chunk)
        yield chunk

    from services.ai.base import Usage

    usage = Usage(
        input_tokens=sum(len(m.content) for m in normalised) // 4,
        output_tokens=chars // 4,
    )
    metering.record(
        module=module,
        model=model,
        usage=usage,
        latency_ms=0,
        cost=metering.compute_cost(usage, model),
        conversation_id=conversation_id,
    )


async def astream(
    messages: Sequence[ChatMessage] | Sequence[dict[str, Any]],
    *,
    tenant_id: str | None = None,
    **overrides: Any,
) -> AsyncIterator[str]:
    """Async stream for Channels consumers (voice loop, live chat)."""
    model = _apply_overrides(registry.resolve("llm", tenant_id), overrides)
    provider = registry.get_provider(model.provider)
    async for chunk in provider.astream(_normalise(messages), model):
        yield chunk


# ==============================================================================
# Embeddings
# ==============================================================================


def embed(
    texts: str | Sequence[str],
    *,
    module: str = "rag",
    tenant_id: str | None = None,
    expect_dimension: int | None = None,
) -> EmbeddingResult:
    """Text embedding with a hard dimension check.

    The check is not paranoia. Writing a 768-dim vector into a column built for
    1536 fails loudly; writing a *different model's* 1536-dim vector succeeds
    and silently degrades every future search. Verifying the width is the
    cheapest available guard against that class of bug (goal.txt D08).
    """
    _assert_available(tenant_id)
    model = registry.resolve("embedding", tenant_id)
    items = [texts] if isinstance(texts, str) else list(texts)

    result = _call_with_fallback(model, lambda m: registry.get_provider(m.provider).embed(items, m))

    expected = expect_dimension or model.dimension or settings.VECTOR_DIMENSIONS["text"]
    if result.dimension and expected and result.dimension != expected:
        raise AIError(
            f"embedding dimension mismatch: model '{result.model}' returned "
            f"{result.dimension}, schema expects {expected}. Re-embed before switching models."
        )

    result.cost_usd = metering.compute_cost(result.usage, model)
    metering.record(
        module=module,
        model=model,
        usage=result.usage,
        latency_ms=result.latency_ms,
        cost=result.cost_usd,
    )
    return result


def embed_image(
    images: Sequence[bytes], *, module: str = "vector_search", tenant_id: str | None = None
) -> EmbeddingResult:
    _assert_available(tenant_id)
    model = registry.resolve("image_embedding", tenant_id)
    provider = registry.get_provider(model.provider)
    result = provider.embed_image(list(images), model)
    metering.record(
        module=module,
        model=model,
        usage=result.usage,
        latency_ms=result.latency_ms,
        cost=Decimal("0"),
    )
    return result


def embed_image_text(
    texts: Sequence[str], *, module: str = "vector_search", tenant_id: str | None = None
) -> EmbeddingResult:
    """Encode a *text query* into the CLIP image space.

    This is what makes "sea view room with balcony" match a photograph nobody
    tagged (SRS §9.2). It must use the same CLIP model as the images, which is
    why it resolves ``image_embedding`` and not ``embedding``.
    """
    _assert_available(tenant_id)
    model = registry.resolve("image_embedding", tenant_id)
    provider = registry.get_provider(model.provider)
    result = provider.embed_text(list(texts), model)
    metering.record(
        module=module,
        model=model,
        usage=result.usage,
        latency_ms=result.latency_ms,
        cost=Decimal("0"),
    )
    return result


# ==============================================================================
# Speech
# ==============================================================================


def transcribe(
    audio: bytes, *, language: str = "", module: str = "reception", tenant_id: str | None = None
) -> TranscriptionResult:
    _assert_available(tenant_id)
    model = registry.resolve("stt", tenant_id)
    result = registry.get_provider(model.provider).transcribe(audio, model, language)
    metering.record(
        module=module,
        model=model,
        usage=_empty_usage(),
        latency_ms=result.latency_ms,
        cost=Decimal("0"),
    )
    return result


def speak(
    text: str,
    *,
    voice: str = "",
    language: str = "",
    module: str = "reception",
    tenant_id: str | None = None,
) -> SpeechResult:
    """Read an answer aloud.

    ``language`` matters as much as ``voice``: an adapter that has voices in
    several languages cannot pick one without it, and reading Bangla with an
    English voice is worse than staying silent.
    """
    _assert_available(tenant_id)
    model = registry.resolve("tts", tenant_id)
    provider = registry.get_provider(model.provider)

    try:
        result = provider.speak(text, model, voice, language=language)
    except TypeError:
        # An adapter written before languages were threaded through. Losing the
        # voice choice is better than losing the audio.
        result = provider.speak(text, model, voice)
    metering.record(
        module=module,
        model=model,
        usage=_empty_usage(),
        latency_ms=result.latency_ms,
        cost=Decimal("0"),
    )
    return result


# ==============================================================================
# Health
# ==============================================================================


def health(tenant_id: str | None = None) -> dict[str, Any]:
    """Round-trip probe used by /api/v1/ai/health/ and the dashboard badge."""
    model = registry.resolve("llm", tenant_id)
    report: dict[str, Any] = {
        "enabled": settings.AI["ENABLED"] and not settings.AI["KILL_SWITCH"],
        "provider": model.provider,
        "model": model.model_name,
        "configured": bool(model.api_key or model.provider in {"fake", "local_clip"}),
    }
    if not report["enabled"]:
        report["status"] = "disabled"
        return report
    if not report["configured"]:
        report["status"] = "unconfigured"
        report["detail"] = "No API key set. Add one in AI Center or .env."
        return report

    try:
        result = chat(
            [ChatMessage(Role.USER, "Reply with the single word: ok")],
            module="health",
            tenant_id=tenant_id,
            max_tokens=8,
        )
    except (AIError, AIDisabled, AIBudgetExceeded) as exc:
        report["status"] = "error"
        report["detail"] = str(exc)
        return report

    report |= {
        "status": "ok",
        "latency_ms": result.latency_ms,
        "reply": result.text.strip()[:40],
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "cost_usd": str(result.cost_usd),
    }
    return report


# ==============================================================================
# Internals
# ==============================================================================


def _normalise(messages: Sequence[Any]) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for message in messages:
        if isinstance(message, ChatMessage):
            out.append(message)
        elif isinstance(message, dict):
            out.append(
                ChatMessage(
                    role=message.get("role", Role.USER),
                    content=message.get("content", ""),
                    name=message.get("name", ""),
                )
            )
        else:  # pragma: no cover - programmer error
            raise TypeError(f"Unsupported message type: {type(message)!r}")
    return out


def _apply_overrides(model: ResolvedModel, overrides: dict[str, Any]) -> ResolvedModel:
    if not overrides:
        return model
    from dataclasses import replace

    allowed = {k: v for k, v in overrides.items() if hasattr(model, k)}
    return replace(model, **allowed) if allowed else model


def _empty_usage():
    from services.ai.base import Usage

    return Usage()


@retry(
    retry=retry_if_exception_type(_TRANSIENT),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.4, max=4),
    reraise=True,
)
def _attempt(model: ResolvedModel, call):
    return call(model)


def _call_with_fallback(model: ResolvedModel, call):
    """Retry the primary; on exhaustion, try the configured fallback once.

    Fallback exists because "the concierge is down" is unacceptable while "the
    concierge is briefly using the cheaper model" is merely a bad afternoon.
    """
    try:
        return _attempt(model, call)
    except _TRANSIENT as exc:
        if not model.fallback:
            raise
        logger.warning(
            "primary AI model failed; using fallback",
            extra={
                "primary": model.model_name,
                "fallback": model.fallback.model_name,
                "error": str(exc),
            },
        )
        result = _attempt(model.fallback, call)
        if hasattr(result, "fallback_used"):
            result.fallback_used = True
        return result
