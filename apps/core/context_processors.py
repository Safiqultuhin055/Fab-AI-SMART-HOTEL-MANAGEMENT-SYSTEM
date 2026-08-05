"""Template context shared by the whole staff UI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings

from apps.core.navigation import NAVIGATION, QUICK_ACTIONS

if TYPE_CHECKING:  # pragma: no cover
    from django.http import HttpRequest


def ui_context(request: HttpRequest) -> dict[str, Any]:
    from services.ai import gateway

    user = getattr(request, "user", None)
    authenticated = bool(user and user.is_authenticated)
    tenant = getattr(request, "tenant", None)

    def visible(items):
        if not authenticated:
            return ()
        return tuple(
            item
            for item in items
            if not item.permission or user.is_superuser or user.has_perm(item.permission)
        )

    ai_status = gateway.status(str(tenant.pk) if tenant else None)

    return {
        "nav_items": visible(NAVIGATION),
        "quick_actions": visible(QUICK_ACTIONS),
        "active_nav": getattr(request, "active_nav", ""),
        "current_hotel": tenant,
        "ai_enabled": ai_status["state"] != "disabled",
        "ai_status": ai_status,
        "biometric_enabled": settings.BIOMETRIC["ENABLED"],
        "app_version": "0.1.0",
    }
