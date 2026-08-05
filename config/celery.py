"""Celery application for ASHOS.

Queue topology (workers are started per-queue in deploy/compose.yml):

    default    — short transactional work: notifications, audit fan-out
    ai         — LLM / embedding / OCR calls.  Slow, rate-limited, retried.
    vision     — face + image pipelines.  Pinned to GPU nodes when available.
    periodic   — Celery Beat output: night audit, retention purge, reminders

Keeping AI off the default queue matters: a stalled LLM provider must never
block a check-in confirmation SMS.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("ashos")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.task_routes = {
    "services.ai.*": {"queue": "ai"},
    "apps.rag.*": {"queue": "ai"},
    "apps.vision.*": {"queue": "vision"},
    "apps.vector_search.*": {"queue": "vision"},
}

app.conf.task_default_queue = "default"
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1
app.conf.task_reject_on_worker_lost = True

app.conf.beat_schedule = {
    # goal.txt D10 — biometric embeddings past expires_at must not survive.
    "purge-expired-biometrics": {
        "task": "apps.vision.tasks.purge_expired_biometrics",
        "schedule": crontab(hour=3, minute=0),
        "options": {"queue": "periodic"},
    },
    # SRS §7 — proactive checkout reminder, T-12h.
    "checkout-reminders": {
        "task": "apps.billing.tasks.dispatch_checkout_reminders",
        "schedule": crontab(minute="*/30"),
        "options": {"queue": "periodic"},
    },
    # SRS §7 — night audit rolls the business date and posts room charges.
    "night-audit": {
        "task": "apps.billing.tasks.run_night_audit",
        "schedule": crontab(hour=2, minute=30),
        "options": {"queue": "periodic"},
    },
    # goal.txt R3 — cost guard rail.
    "ai-cost-rollup": {
        "task": "apps.ai_center.tasks.rollup_ai_cost",
        "schedule": crontab(minute=0),
        "options": {"queue": "periodic"},
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> str:  # pragma: no cover - smoke helper
    return f"celery ok: {self.request!r}"
