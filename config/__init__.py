"""ASHOS project configuration package.

Importing the Celery app here guarantees ``@shared_task`` decorators are bound
to the configured app as soon as Django starts.
"""

from __future__ import annotations

from .celery import app as celery_app

__all__ = ("celery_app",)
