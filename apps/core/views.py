"""Shared view helpers.

``module_page`` renders the standard shell for a top-level module. Modules that
have shipped pass their own template and context; modules still being built get
the roadmap panel from ``module_plan``.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.core.module_plan import MODULE_PLANS
from apps.core.navigation import NAV_BY_KEY


def module_page(
    request: HttpRequest,
    key: str,
    *,
    template: str = "modules/placeholder.html",
    context: dict[str, Any] | None = None,
) -> HttpResponse:
    # Drives the sidebar highlight; read by the ui_context processor.
    request.active_nav = key  # type: ignore[attr-defined]

    nav = NAV_BY_KEY.get(key)
    plan = MODULE_PLANS.get(key)

    payload: dict[str, Any] = {
        "module_key": key,
        "module_nav": nav,
        "plan": plan,
        "page_title": plan.title if plan else (nav.label if nav else key.title()),
        "hotel": getattr(request, "tenant", None),
    }
    payload.update(context or {})
    return render(request, template, payload)
