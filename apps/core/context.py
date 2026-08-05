"""Per-request ambient context.

Audit logging and tenant scoping both need "who is doing this, for which hotel"
in places that have no ``request`` object — model ``save()``, Celery tasks,
service functions. Threading a request through every layer would poison the
service-layer signatures, so a contextvar carries it instead.

Contextvars are async-safe: each ASGI task gets its own copy, so a slow voice
WebSocket cannot leak its actor into a concurrent HTTP request.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator

_request_id: ContextVar[str] = ContextVar("ashos_request_id", default="")
_actor_id: ContextVar[str] = ContextVar("ashos_actor_id", default="")
_actor_label: ContextVar[str] = ContextVar("ashos_actor_label", default="")
_client_ip: ContextVar[str] = ContextVar("ashos_client_ip", default="")
_tenant_id: ContextVar[str] = ContextVar("ashos_tenant_id", default="")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_context(
    *,
    request_id: str = "",
    actor_id: str = "",
    actor_label: str = "",
    client_ip: str = "",
    tenant_id: str = "",
) -> None:
    if request_id:
        _request_id.set(request_id)
    if actor_id:
        _actor_id.set(actor_id)
    if actor_label:
        _actor_label.set(actor_label)
    if client_ip:
        _client_ip.set(client_ip)
    if tenant_id:
        _tenant_id.set(tenant_id)


def get_request_context() -> dict[str, str]:
    return {
        "request_id": _request_id.get(),
        "actor_id": _actor_id.get(),
        "actor_label": _actor_label.get(),
        "client_ip": _client_ip.get(),
        "tenant_id": _tenant_id.get(),
    }


def current_tenant_id() -> str:
    return _tenant_id.get()


def current_actor_id() -> str:
    return _actor_id.get()


def current_request_id() -> str:
    return _request_id.get()


def clear_request_context() -> None:
    for var in (_request_id, _actor_id, _actor_label, _client_ip, _tenant_id):
        var.set("")


@contextmanager
def scoped_context(**kwargs: Any) -> Iterator[None]:
    """Temporarily override context — used by Celery tasks and management jobs."""
    previous = get_request_context()
    set_request_context(**kwargs)
    try:
        yield
    finally:
        clear_request_context()
        set_request_context(**{k: v for k, v in previous.items() if v})
