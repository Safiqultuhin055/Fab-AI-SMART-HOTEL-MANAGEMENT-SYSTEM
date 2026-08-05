from __future__ import annotations

from django.apps import AppConfig


class ReceptionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reception"
    verbose_name = "AI reception conversations and handoff"
