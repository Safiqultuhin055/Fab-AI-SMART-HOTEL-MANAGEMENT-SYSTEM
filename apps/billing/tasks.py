"""Billing background tasks (implemented in P1/P4; scheduled from P0)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("ashos.billing")


@shared_task(name="apps.billing.tasks.dispatch_checkout_reminders")
def dispatch_checkout_reminders() -> dict[str, int]:
    """T-12h proactive checkout reminder (SRS §7).

    Composes in the guest's language, includes outstanding balance, checkout
    time, a late-checkout upsell and a feedback link, then fans out to
    SMS/WhatsApp/Push.
    """
    logger.debug("checkout reminder sweep — implemented in P4")
    return {"sent": 0, "status": "not_implemented"}


@shared_task(name="apps.billing.tasks.run_night_audit")
def run_night_audit() -> dict[str, int]:
    """Roll the business date, post room charges and taxes, freeze the day."""
    logger.debug("night audit — implemented in P1")
    return {"folios": 0, "status": "not_implemented"}
