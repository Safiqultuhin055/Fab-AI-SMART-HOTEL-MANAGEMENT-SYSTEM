"""Domain exception hierarchy.

Services raise these. The API layer maps them to RFC 7807 responses
(goal.txt D17); the HTML layer maps them to messages. Neither layer needs to
know about DRF exceptions, which keeps services importable from Celery tasks
and management commands.
"""

from __future__ import annotations

from typing import Any


class ASHOSError(Exception):
    """Base for every expected, business-meaningful failure."""

    code = "ashos_error"
    status = 400
    title = "Request could not be completed"

    def __init__(self, detail: str = "", **extra: Any) -> None:
        self.detail = detail or self.title
        self.extra = extra
        super().__init__(self.detail)


class ValidationError(ASHOSError):
    code = "validation_error"
    status = 422
    title = "Validation failed"


class NotFound(ASHOSError):
    code = "not_found"
    status = 404
    title = "Resource not found"


class PermissionDenied(ASHOSError):
    code = "permission_denied"
    status = 403
    title = "Not permitted"


class Conflict(ASHOSError):
    code = "conflict"
    status = 409
    title = "Conflicting state"


class TenantMissing(ASHOSError):
    code = "tenant_missing"
    status = 400
    title = "No hotel selected for this request"


# --- AI-specific ---------------------------------------------------------------


class AIError(ASHOSError):
    code = "ai_error"
    status = 502
    title = "AI service failed"


class AIDisabled(AIError):
    """Kill switch is on, or AI is disabled for this tenant (goal.txt D12)."""

    code = "ai_disabled"
    status = 503
    title = "AI is currently disabled; the system is in manual mode"


class AIBudgetExceeded(AIError):
    code = "ai_budget_exceeded"
    status = 429
    title = "AI budget cap reached"


class AITimeout(AIError):
    code = "ai_timeout"
    status = 504
    title = "AI provider timed out"


class LowConfidence(AIError):
    """Answer quality below threshold — hand off to a human (SRS Module 1)."""

    code = "low_confidence"
    status = 200  # not an HTTP failure: the handoff *is* the correct outcome
    title = "Escalated to human staff"


# --- Biometric / privacy -------------------------------------------------------


class ConsentRequired(ASHOSError):
    code = "consent_required"
    status = 403
    title = "Explicit guest consent is required for this operation"


class LivenessFailed(ASHOSError):
    code = "liveness_failed"
    status = 400
    title = "Liveness check failed"
