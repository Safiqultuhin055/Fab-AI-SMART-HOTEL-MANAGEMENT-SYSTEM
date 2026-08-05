"""AI Center operations that are not plain CRUD."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.utils import timezone

from apps.core.exceptions import AIError
from services.ai import registry
from services.ai.base import ChatMessage, ResolvedModel, Role

if TYPE_CHECKING:  # pragma: no cover
    from apps.ai_center.models import ModelConfig

logger = logging.getLogger("ashos.ai")


def verify_config(config: ModelConfig) -> tuple[bool, str]:
    """Make one real call against a configuration and record the outcome.

    Worth the few cents: a wrong key, a stale endpoint or a model name the
    vendor has retired all look identical in the admin until a guest asks the
    kiosk a question. This turns that into a green tick or a specific error,
    on demand.

    Goes straight to the provider rather than through ``gateway.chat`` on
    purpose — the point is to test *this row*, including inactive and
    non-default ones, without the gateway resolving something else.
    """
    from apps.ai_center.models import ModelKind

    resolved = ResolvedModel(
        kind=config.kind,
        provider=config.provider,
        model_name=config.model_name,
        base_url=config.effective_base_url,
        api_key=config.api_key,
        temperature=0.0,
        max_tokens=16,
        timeout_s=min(config.timeout_s, 20),
        dimension=config.dimension,
        extra=config.extra or {},
    )

    try:
        provider = registry.get_provider(config.provider)
    except ValueError as exc:
        return _record(config, False, str(exc))

    try:
        if config.kind == ModelKind.LLM:
            result = provider.chat([ChatMessage(Role.USER, "Reply with: ok")], resolved)
            detail = (result.text or "").strip()[:60] or "empty reply"
        elif config.kind in {ModelKind.EMBEDDING, ModelKind.IMAGE_EMBEDDING}:
            result = provider.embed(["connection test"], resolved)
            width = result.dimension or len(result.vector)
            if config.dimension and width != config.dimension:
                return _record(
                    config,
                    False,
                    f"dimension mismatch: model returns {width}, config says {config.dimension}",
                )
            detail = f"{width}-dim vector"
        else:
            # STT/TTS/OCR need real media to test; do not pretend otherwise.
            return _record(config, False, f"no automated test for {config.get_kind_display()}")
    except AIError as exc:
        return _record(config, False, str(exc)[:200])
    except Exception as exc:  # noqa: BLE001 - provider libraries raise anything
        logger.exception("verify failed", extra={"config": str(config.pk)})
        return _record(config, False, f"{exc.__class__.__name__}: {exc}"[:200])

    return _record(config, True, detail)


def _record(config: ModelConfig, ok: bool, detail: str) -> tuple[bool, str]:
    config.last_verified_at = timezone.now() if ok else config.last_verified_at
    config.last_error = "" if ok else detail
    config.save(update_fields=["last_verified_at", "last_error", "updated_at"])
    return ok, detail
