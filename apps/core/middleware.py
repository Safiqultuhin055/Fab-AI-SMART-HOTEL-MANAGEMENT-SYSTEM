"""Request-scoped context middleware."""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.core.context import (
    clear_request_context,
    new_request_id,
    set_request_context,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
RESPONSE_ID_HEADER = "X-Request-ID"


def _client_ip(request: HttpRequest) -> str:
    # Nginx sets X-Forwarded-For; take the left-most entry, which is the client.
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


class RequestContextMiddleware:
    """Binds request id, actor and client IP for the lifetime of the request.

    Everything downstream — audit rows, structured logs, AI usage records —
    reads from this instead of being handed a request object.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = request.META.get(REQUEST_ID_HEADER) or new_request_id()
        user = getattr(request, "user", None)

        set_request_context(
            request_id=request_id,
            actor_id=str(user.pk) if user is not None and user.is_authenticated else "",
            actor_label=(
                getattr(user, "email", "") if user is not None and user.is_authenticated else ""
            ),
            client_ip=_client_ip(request),
        )
        request.request_id = request_id  # type: ignore[attr-defined]

        try:
            response = self.get_response(request)
        finally:
            # ASGI reuses tasks; a leaked actor id would be attributed to the
            # next request handled by the same context.
            clear_request_context()

        response[RESPONSE_ID_HEADER] = request_id
        return response
