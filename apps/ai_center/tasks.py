"""AI Center background tasks."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

import httpx
from celery import shared_task
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger("ashos.ai")


@shared_task(name="apps.ai_center.tasks.rollup_ai_cost")
def rollup_ai_cost() -> dict[str, str]:
    """Hourly cost guard (goal.txt R3).

    Compares the last 24h of spend against each hotel's cap. At 80% it warns; at
    100% it trips the hotel's kill switch. Burning the month's AI budget in an
    afternoon because of a runaway loop is a real failure mode, and an alert
    nobody reads is not a control.
    """
    from apps.ai_center.models import UsageLog
    from apps.tenants.models import Hotel

    since = timezone.now() - timedelta(hours=24)
    tripped: list[str] = []

    for hotel in Hotel.objects.filter(is_active=True, ai_enabled=True):
        spend = UsageLog.objects.filter(tenant=hotel, created_at__gte=since).aggregate(
            total=Sum("cost_usd")
        )["total"] or Decimal("0")

        cap = hotel.ai_daily_cost_cap_usd
        if cap <= 0:
            continue

        ratio = spend / cap
        if ratio >= 1:
            Hotel.objects.filter(pk=hotel.pk).update(ai_kill_switch=True)
            tripped.append(hotel.code)
            _alert(hotel, spend, cap, level="critical")
            logger.error(
                "AI budget exceeded; kill switch engaged",
                extra={"hotel": hotel.code, "spend": str(spend), "cap": str(cap)},
            )
        elif ratio >= Decimal("0.8"):
            _alert(hotel, spend, cap, level="warning")

    return {"tripped": ",".join(tripped)}


def _alert(hotel, spend: Decimal, cap: Decimal, *, level: str) -> None:
    from django.conf import settings

    url = settings.AI["COST_ALERT_WEBHOOK"]
    if not url:
        return
    payload = {
        "level": level,
        "hotel": hotel.code,
        "spend_usd": str(spend),
        "cap_usd": str(cap),
        "message": f"ASHOS AI spend {spend} of {cap} USD in 24h at {hotel.name}",
    }
    try:
        httpx.post(url, json=payload, timeout=5)
    except httpx.HTTPError:
        logger.warning("cost alert webhook failed", extra={"hotel": hotel.code})
