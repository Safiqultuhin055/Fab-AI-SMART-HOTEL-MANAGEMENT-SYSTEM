"""Audit recording helper.

Call ``audit.record(...)`` from the service layer, not from views: the service
is the only place that knows the business meaning of a change. Recording is
best-effort — a failure to write an audit row must never roll back the guest's
check-in — but failures are logged loudly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from apps.core.context import get_request_context

if TYPE_CHECKING:  # pragma: no cover
    from django.db.models import Model

logger = logging.getLogger("ashos.audit")

SENSITIVE_KEYS = {
    "password",
    "api_key",
    "secret",
    "token",
    "embedding",
    "face_embedding",
    "card_number",
    "cvv",
}


def _scrub(changes: dict[str, Any]) -> dict[str, Any]:
    """Strip anything that must never sit in a queryable log table."""
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if any(marker in key.lower() for marker in SENSITIVE_KEYS):
            clean[key] = "<redacted>"
        else:
            clean[key] = value
    return clean


def record(
    action: str,
    *,
    summary: str = "",
    obj: Model | None = None,
    changes: dict[str, Any] | None = None,
    hotel_id: str | None = None,
    actor_id: str | None = None,
    user_agent: str = "",
) -> None:
    from apps.accounts.models import AuditLog

    ctx = get_request_context()
    try:
        AuditLog.objects.create(
            actor_id=actor_id or ctx["actor_id"] or None,
            actor_label=ctx["actor_label"],
            hotel_id=hotel_id or ctx["tenant_id"] or None,
            action=action,
            object_type=obj.__class__.__name__ if obj is not None else "",
            object_id=str(obj.pk) if obj is not None else "",
            summary=summary[:255],
            changes=_scrub(changes or {}),
            ip_address=ctx["client_ip"] or None,
            user_agent=user_agent[:255],
            request_id=ctx["request_id"],
        )
    except Exception:  # noqa: BLE001 - audit must never break the transaction
        logger.exception("audit write failed", extra={"action": action, "summary": summary})


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    """Build a {field: [old, new]} map of what actually changed."""
    return {
        key: [before.get(key), after.get(key)]
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }
