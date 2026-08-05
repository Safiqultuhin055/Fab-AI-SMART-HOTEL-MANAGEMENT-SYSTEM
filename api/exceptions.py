"""RFC 7807 problem+json error handling (goal.txt D17).

One error shape for the whole API::

    {
      "type": "https://ashos.dev/errors/validation_error",
      "title": "Validation failed",
      "status": 422,
      "detail": "check_out must be after check_in",
      "instance": "/api/v1/reservations/",
      "request_id": "3f9c1a2b7d4e5f60",
      "errors": {"check_out": ["must be after check_in"]}
    }

Clients (kiosk, PWA, staff UI) then have exactly one branch to write, and
support can trace any complaint from a screenshot via ``request_id``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.context import current_request_id
from apps.core.exceptions import ASHOSError

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = logging.getLogger("ashos.api")

TYPE_BASE = "https://ashos.dev/errors/"
CONTENT_TYPE = "application/problem+json"


def problem_detail_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    request = context.get("request")
    instance = getattr(request, "path", "")

    # --- Domain exceptions from services/ ------------------------------------
    if isinstance(exc, ASHOSError):
        return _problem(
            code=exc.code,
            title=exc.title,
            status=exc.status,
            detail=exc.detail,
            instance=instance,
            extra=exc.extra,
        )

    # --- Django-native ---------------------------------------------------------
    if isinstance(exc, Http404):
        return _problem("not_found", "Resource not found", 404, str(exc) or "Not found", instance)
    if isinstance(exc, DjangoPermissionDenied):
        return _problem("permission_denied", "Not permitted", 403, str(exc), instance)
    if isinstance(exc, DjangoValidationError):
        return _problem(
            "validation_error",
            "Validation failed",
            422,
            "; ".join(exc.messages),
            instance,
            extra={"errors": getattr(exc, "message_dict", {})},
        )

    # --- DRF -------------------------------------------------------------------
    response = drf_exception_handler(exc, context)
    if response is None:
        # Genuinely unexpected. Log with the traceback; tell the client nothing
        # about internals.
        logger.exception("unhandled API exception", extra={"path": instance})
        return _problem(
            "internal_error",
            "Unexpected server error",
            500,
            "The request could not be completed. Support can trace it by request id.",
            instance,
        )

    if isinstance(exc, drf_exceptions.ValidationError):
        return _problem(
            "validation_error",
            "Validation failed",
            422,
            "One or more fields are invalid.",
            instance,
            extra={"errors": response.data},
            headers=dict(response.headers or {}),
        )

    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    code = getattr(exc, "default_code", "error")
    return _problem(
        code,
        getattr(exc, "default_detail", "Request failed"),
        response.status_code,
        str(detail or response.data),
        instance,
        headers=dict(response.headers or {}),
    )


def _problem(
    code: str,
    title: str,
    status: int,
    detail: str,
    instance: str = "",
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    body: dict[str, Any] = {
        "type": f"{TYPE_BASE}{code}",
        "title": str(title),
        "status": status,
        "detail": detail,
        "instance": instance,
        "request_id": current_request_id(),
    }
    if extra:
        body |= extra

    response = Response(body, status=status, content_type=CONTENT_TYPE)
    for key, value in (headers or {}).items():
        if key.lower() not in {"content-type", "allow"}:
            response[key] = value
    return response
