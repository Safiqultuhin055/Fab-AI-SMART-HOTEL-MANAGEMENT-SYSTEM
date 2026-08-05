from __future__ import annotations

from django.apps import AppConfig


class HousekeepingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.housekeeping"
    verbose_name = "Task queue and AI priority engine"
