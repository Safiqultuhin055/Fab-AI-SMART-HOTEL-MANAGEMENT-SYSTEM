from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from api.v1.serializers import (
    AIHealthSerializer,
    ASHOSTokenObtainPairSerializer,
    MeSerializer,
    SystemHealthSerializer,
)
from services.ai import gateway

APP_VERSION = "0.1.0"


class LoginView(TokenObtainPairView):
    """Exchange email + password for an access/refresh pair."""

    serializer_class = ASHOSTokenObtainPairSerializer
    throttle_scope = "auth"


class LogoutView(APIView):
    """Blacklist a refresh token.

    Without this, a stolen refresh token stays valid for its full lifetime.
    Kiosks live in public lobbies, so real logout matters.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["auth"],
        request={
            "application/json": {"type": "object", "properties": {"refresh": {"type": "string"}}}
        },
        responses={205: OpenApiResponse(description="Token blacklisted")},
    )
    def post(self, request: Request) -> Response:
        token = request.data.get("refresh")
        if not token:
            return Response(
                {"detail": "refresh token required"}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            RefreshToken(token).blacklist()
        except Exception:  # noqa: BLE001 - already expired or malformed
            return Response({"detail": "invalid token"}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(APIView):
    """Identity, memberships and effective permissions for the current hotel."""

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["auth"], responses=MeSerializer)
    def get(self, request: Request) -> Response:
        return Response(MeSerializer(request.user, context={"request": request}).data)


class SystemHealthView(APIView):
    """Liveness probe for the load balancer and the ops dashboard."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(tags=["ai-center"], responses=SystemHealthSerializer)
    def get(self, request: Request) -> Response:  # noqa: ARG002
        checks: dict[str, Any] = {}

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["database"] = f"error: {exc.__class__.__name__}"

        try:
            cache.set("ashos:health", "1", 10)
            checks["cache"] = "ok" if cache.get("ashos:health") == "1" else "degraded"
        except Exception as exc:  # noqa: BLE001
            checks["cache"] = f"error: {exc.__class__.__name__}"

        # pgvector is load-bearing: without it, RAG, face and image search are
        # all dead. Report it separately from "the database answered".
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                row = cursor.fetchone()
            checks["pgvector"] = row[0] if row else "missing"
        except Exception as exc:  # noqa: BLE001
            checks["pgvector"] = f"error: {exc.__class__.__name__}"

        healthy = all(str(v).startswith(("ok", "0", "1", "2")) for v in checks.values())
        return Response(
            {
                "status": "ok" if healthy else "degraded",
                "version": APP_VERSION,
                "checks": checks,
            },
            status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class AIHealthView(APIView):
    """Real round-trip to the configured LLM.

    Authenticated on purpose: it costs money and reveals provider details.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_chat"

    @extend_schema(tags=["ai-center"], responses=AIHealthSerializer)
    def get(self, request: Request) -> Response:
        tenant = getattr(request, "tenant", None)
        report = gateway.health(str(tenant.pk) if tenant else None)
        code = (
            status.HTTP_200_OK
            if report.get("status") == "ok"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return Response(report, status=code)


class AIConfigView(APIView):
    """Non-secret AI posture for client bootstrapping.

    Deliberately excludes endpoints and keys: the kiosk needs to know whether to
    show the mic button, not which vendor is behind it.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["ai-center"])
    def get(self, request: Request) -> Response:
        tenant = getattr(request, "tenant", None)
        return Response(
            {
                "ai_available": gateway.is_available(str(tenant.pk) if tenant else None),
                "voice_enabled": settings.AI["STT"]["provider"] != "disabled",
                "biometric_enabled": bool(tenant and tenant.biometric_enabled)
                and settings.BIOMETRIC["ENABLED"],
                "confidence_threshold": settings.AI["CONFIDENCE_THRESHOLD"],
                "max_turns": settings.AI["MAX_CONVERSATION_TURNS"],
                "languages": [code for code, _ in settings.LANGUAGES],
            }
        )
