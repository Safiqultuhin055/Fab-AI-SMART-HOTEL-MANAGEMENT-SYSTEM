from __future__ import annotations

from django.apps import AppConfig


class VisionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.vision"
    verbose_name = "Face recognition, liveness, document OCR"
