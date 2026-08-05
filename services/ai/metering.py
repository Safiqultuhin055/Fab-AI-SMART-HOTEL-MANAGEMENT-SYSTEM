"""Cost accounting and usage recording.

Every AI call is metered. Without this, "AI cost per guest-stay" in goal.txt §8
is an opinion, and the budget cap in R3 has nothing to act on.

Writes are best-effort and must never fail a guest interaction: a dropped usage
row costs a data point, a raised exception costs a check-in.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.utils import timezone

from apps.core.context import current_request_id, current_tenant_id

if TYPE_CHECKING:  # pragma: no cover
    from services.ai.base import ResolvedModel, Usage

logger = logging.getLogger("ashos.ai")

SPEND_CACHE_TTL = 3600


def compute_cost(usage: Usage, model: ResolvedModel) -> Decimal:
    inp = Decimal(usage.input_tokens) / Decimal(1000) * model.cost_per_1k_input
    out = Decimal(usage.output_tokens) / Decimal(1000) * model.cost_per_1k_output
    return (inp + out).quantize(Decimal("0.000001"))


def record(
    *,
    module: str,
    model: ResolvedModel,
    usage: Usage,
    latency_ms: int,
    cost: Decimal,
    success: bool = True,
    error_code: str = "",
    fallback_used: bool = False,
    cache_hit: bool = False,
    conversation_id: str = "",
) -> None:
    from apps.ai_center.models import UsageLog

    tenant_id = current_tenant_id() or None
    try:
        UsageLog.objects.create(
            tenant_id=tenant_id,
            module=module,
            kind=model.kind,
            provider=model.provider,
            model_name=model.model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            success=success,
            error_code=error_code[:60],
            fallback_used=fallback_used,
            cache_hit=cache_hit,
            request_id=current_request_id(),
            conversation_id=conversation_id[:64],
        )
    except Exception:  # noqa: BLE001
        logger.warning("usage log write failed", exc_info=True)

    if cost and tenant_id:
        _bump_spend(tenant_id, cost)


# ==============================================================================
# Rolling daily spend — read on the hot path, so it lives in Redis, not SQL.
# ==============================================================================


def _spend_key(tenant_id: str) -> str:
    return f"aispend:{tenant_id}:{timezone.now():%Y%m%d}"


def _bump_spend(tenant_id: str, cost: Decimal) -> None:
    key = _spend_key(tenant_id)
    try:
        current = Decimal(str(cache.get(key, "0")))
        cache.set(key, str(current + cost), SPEND_CACHE_TTL * 25)
    except Exception:  # noqa: BLE001
        logger.debug("spend counter update failed", exc_info=True)


def spend_today(tenant_id: str) -> Decimal:
    """Best-effort cached spend; falls back to the DB when the cache is cold."""
    key = _spend_key(tenant_id)
    cached = cache.get(key)
    if cached is not None:
        return Decimal(str(cached))

    from django.db.models import Sum

    from apps.ai_center.models import UsageLog

    since = timezone.now() - timedelta(hours=24)
    total = UsageLog.objects.filter(tenant_id=tenant_id, created_at__gte=since).aggregate(
        total=Sum("cost_usd")
    )["total"] or Decimal("0")
    cache.set(key, str(total), SPEND_CACHE_TTL)
    return Decimal(total)
