from __future__ import annotations

from django.apps import AppConfig


class GuestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.guests"
    verbose_name = "Guest profiles, documents, consent"
