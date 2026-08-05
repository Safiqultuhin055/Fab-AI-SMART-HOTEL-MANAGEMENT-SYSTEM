"""Resolve *which* backend serves a capability, and instantiate its provider.

Precedence: AI Center row for this hotel → AI Center platform row → settings
env defaults. The env layer exists so a fresh clone boots and answers before
anyone has opened the admin; the DB layer exists so operations can change it
afterwards without a deploy.

Resolutions are cached briefly. A 60s window means a config change reaches
production within a minute while the hot chat path avoids a DB round-trip per
turn.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.cache import cache
from django.db import models

from apps.core.context import current_tenant_id
from services.ai.base import ResolvedModel

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger("ashos.ai")

CACHE_TTL = 60

# kind -> settings.AI key
SETTINGS_KEY = {
    "llm": "LLM",
    "embedding": "EMBEDDING",
    "image_embedding": "IMAGE_EMBEDDING",
    "face": "FACE",
    "stt": "STT",
    "tts": "TTS",
    "ocr": "OCR",
}


def resolve(kind: str, tenant_id: str | None = None) -> ResolvedModel:
    tenant_id = tenant_id or current_tenant_id()
    cache_key = f"aicfg:{kind}:{tenant_id or 'platform'}"

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    resolved = _from_db(kind, tenant_id) or _from_settings(kind)
    cache.set(cache_key, resolved, CACHE_TTL)
    return resolved


def invalidate(kind: str | None = None, tenant_id: str | None = None) -> None:
    """Called by AI Center on save so an operator sees their change immediately.

    Editing a *platform* row changes what every hotel resolves to, and their
    entries are cached separately. Clearing only the platform slot would leave
    every property serving the old key for up to a minute, so a platform edit
    clears the whole namespace.
    """
    kinds = [kind] if kind else list(SETTINGS_KEY)
    if tenant_id:
        for k in kinds:
            cache.delete(f"aicfg:{k}:{tenant_id}")
        return

    cache.delete_many([f"aicfg:{k}:platform" for k in kinds])
    try:
        from apps.tenants.models import Hotel

        ids = list(Hotel.all_objects.values_list("pk", flat=True))
    except Exception:  # noqa: BLE001 — app registry not ready
        return
    cache.delete_many([f"aicfg:{k}:{pk}" for k in kinds for pk in ids])


def _from_db(kind: str, tenant_id: str | None) -> ResolvedModel | None:
    """Best usable row: this hotel's, else the platform's.

    "Usable" does real work here. A row with no credential is a placeholder, and
    seeding creates one per capability for every new property. Treating those as
    configuration meant a freshly onboarded hotel's kiosk said "AI not
    configured" while sitting next to a perfectly good platform key — so an
    unusable row is skipped rather than allowed to shadow a working one.

    Within a scope the order is: the default, then any other usable row. The
    second part matters when an operator marks a keyless row default by mistake:
    reception keeps working from the row that has a key instead of going dark.
    """
    try:
        from apps.ai_center.models import ModelConfig
    except Exception:  # noqa: BLE001 — app registry not ready (e.g. during migrate)
        return None

    try:
        rows = list(
            ModelConfig.all_objects.filter(kind=kind, is_active=True, is_deleted=False)
            .filter(models.Q(tenant_id=tenant_id) if tenant_id else models.Q(tenant__isnull=True))
            .select_related("fallback")
            .order_by("-is_default", "-last_verified_at", "created_at")
        )
        if tenant_id:
            platform = list(
                ModelConfig.all_objects.filter(
                    kind=kind, is_active=True, is_deleted=False, tenant__isnull=True
                )
                .select_related("fallback")
                .order_by("-is_default", "-last_verified_at", "created_at")
            )
            rows += platform
    except Exception:  # noqa: BLE001 — table not migrated yet
        logger.debug("ai_center.ModelConfig unavailable; falling back to settings")
        return None

    for config in rows:
        if config.is_usable:
            return _to_resolved(config)

    if rows:
        logger.warning(
            "no usable %s configuration; every row is missing a credential",
            kind,
            extra={"tenant": tenant_id, "rows": len(rows)},
        )
    return None


def _to_resolved(config: Any) -> ResolvedModel:
    return ResolvedModel(
        kind=config.kind,
        provider=config.provider,
        model_name=config.model_name,
        base_url=config.base_url,
        api_key=config.api_key,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout_s=config.timeout_s,
        dimension=config.dimension,
        cost_per_1k_input=Decimal(str(config.cost_per_1k_input_usd)),
        cost_per_1k_output=Decimal(str(config.cost_per_1k_output_usd)),
        fallback=_to_resolved(config.fallback) if config.fallback_id else None,
        extra=config.extra or {},
        config_id=str(config.pk),
    )


def _from_settings(kind: str) -> ResolvedModel:
    block: dict[str, Any] = settings.AI[SETTINGS_KEY[kind]]
    return ResolvedModel(
        kind=kind,
        provider=block.get("provider", "openai_compatible"),
        model_name=block.get("model", ""),
        base_url=block.get("base_url", ""),
        api_key=block.get("api_key", ""),
        temperature=block.get("temperature", 0.2),
        max_tokens=block.get("max_tokens", 1024),
        timeout_s=block.get("timeout_s", 30),
        dimension=block.get("dimension"),
        extra={k: v for k, v in block.items() if k not in _SETTINGS_CORE_KEYS},
    )


_SETTINGS_CORE_KEYS = {
    "provider",
    "model",
    "base_url",
    "api_key",
    "temperature",
    "max_tokens",
    "timeout_s",
    "dimension",
}


# ==============================================================================
# Provider instances
# ==============================================================================

_PROVIDERS: dict[str, Any] = {}

# Which adapter serves which vendor.
#
# Most vendors ship an OpenAI-compatible surface, so they share one adapter and
# differ only by base URL and key — that is the whole reason the gateway exists.
# Anthropic does not, so it gets its own. Adding a vendor is a line here plus a
# choice in ``ai_center.Provider``; nothing else in the codebase changes.
PROVIDER_ADAPTERS: dict[str, str] = {
    "openai": "openai_compatible",
    "openai_compatible": "openai_compatible",
    "azure_openai": "openai_compatible",
    "gemini": "openai_compatible",  # via /v1beta/openai
    "google": "openai_compatible",
    "moonshot": "openai_compatible",
    "zai": "openai_compatible",
    "groq": "openai_compatible",
    "openrouter": "openai_compatible",
    "local": "openai_compatible",  # Ollama, vLLM, LM Studio
    "other": "openai_compatible",
    "anthropic": "anthropic",
    # Keyless TTS with Bangla neural voices. See the module docstring for the
    # trade this makes; it is a pilot answer, not a contracted one.
    "edge_tts": "edge_tts",
    "fake": "fake",
}


def get_provider(name: str) -> Any:
    """Instantiate a provider lazily.

    Lazy because local providers import torch. Paying a multi-second import at
    Django startup on a web node that only ever calls a remote API would be
    pure waste.
    """
    if name in _PROVIDERS:
        return _PROVIDERS[name]

    adapter = PROVIDER_ADAPTERS.get(name)

    if adapter == "openai_compatible":
        from services.ai.providers.openai_compatible import OpenAICompatibleProvider

        provider: Any = OpenAICompatibleProvider()
    elif adapter == "anthropic":
        from services.ai.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider()
    elif adapter == "edge_tts":
        from services.ai.providers.edge_tts import EdgeTTSProvider

        provider = EdgeTTSProvider()
    elif adapter == "fake":
        from services.ai.providers.fake import FakeProvider

        provider = FakeProvider()
    else:
        raise ValueError(
            f"No adapter for AI provider '{name}'. Add it to PROVIDER_ADAPTERS in "
            "services/ai/registry.py and to ai_center.Provider."
        )

    _PROVIDERS[name] = provider
    return provider
