from __future__ import annotations

from django.apps import AppConfig


class VectorSearchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.vector_search"
    verbose_name = "Semantic image and room search"
