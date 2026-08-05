from __future__ import annotations

from django.apps import AppConfig


class RAGConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rag"
    verbose_name = "Knowledge base, chunking, retrieval"
