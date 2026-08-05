"""WebSocket routing table.

Consumers are added per phase:
  P2 — /ws/reception/chat/ and /ws/reception/voice/
  P4 — /ws/kds/ (kitchen display), /ws/notifications/
"""

from __future__ import annotations

from django.urls import path

websocket_urlpatterns: list[path] = []  # type: ignore[valid-type]
