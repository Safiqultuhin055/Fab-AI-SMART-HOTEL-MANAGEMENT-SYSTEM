"""API v1 routes.

Modules register their routers here as they land. Keeping one file as the map
of the whole API is worth more than scattering ``include()`` calls per app.
"""

from __future__ import annotations

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from api.v1 import views
from apps.reception import api as reception_api
from apps.vision import api as vision_api

app_name = "v1"

urlpatterns = [
    # --- auth ------------------------------------------------------------------
    path("auth/token/", views.LoginView.as_view(), name="token_obtain"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    path("auth/me/", views.MeView.as_view(), name="me"),
    # --- platform health -------------------------------------------------------
    path("health/", views.SystemHealthView.as_view(), name="health"),
    path("ai/health/", views.AIHealthView.as_view(), name="ai_health"),
    path("ai/config/", views.AIConfigView.as_view(), name="ai_config"),
    # --- AI Reception -----------------------------------------------------------
    path(
        "reception/conversations/",
        reception_api.StartConversationView.as_view(),
        name="reception_start",
    ),
    path(
        "reception/conversations/<uuid:conversation_id>/",
        reception_api.HistoryView.as_view(),
        name="reception_history",
    ),
    path("reception/chat/", reception_api.ChatView.as_view(), name="reception_chat"),
    path("reception/voice/", reception_api.VoiceView.as_view(), name="reception_voice"),
    path("reception/speak/", reception_api.SpeakView.as_view(), name="reception_speak"),
    # The silence timer's endpoint. Spends nothing: the next question comes from the
    # booking draft, so a page nobody is typing into costs nothing to keep alive.
    path("reception/nudge/", reception_api.NudgeView.as_view(), name="reception_nudge"),
    path("reception/handoff/", reception_api.HandoffView.as_view(), name="reception_handoff"),
    path("reception/queue/", reception_api.HandoffQueueView.as_view(), name="reception_queue"),
    # --- Vision: face capture after a confirmed booking -------------------------
    # The only endpoints in ASHOS that accept a photograph of a person. Both are
    # gated on the platform flag AND the property flag; see apps/vision/api.py.
    path(
        "vision/enrolment/",
        vision_api.EnrolmentView.as_view(),
        name="vision_enrolment",
    ),
    path(
        "vision/enrolment/status/",
        vision_api.EnrolmentStatusView.as_view(),
        name="vision_enrolment_status",
    ),
    # --- P1+ modules register below ---------------------------------------------
    # path("rooms/", include("apps.rooms.api.urls")),
    # path("reservations/", include("apps.booking.api.urls")),
    # path("reception/", include("apps.reception.api.urls")),
]
