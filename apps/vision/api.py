"""Vision HTTP API — face capture after a confirmed booking.

This is the first endpoint in ASHOS that accepts a photograph of a person, so
it is worth being explicit about what guards it. In order, before any image byte
is read:

  1. the platform flag and the property flag are both on
  2. the caller owns the conversation that made the booking (the kiosk is
     anonymous, so the session key is the only claim it has)
  3. the reservation is real, belongs to this hotel, and came from that
     conversation
  4. ``consent`` is literally ``true`` in the request body
  5. the guest is an adult

Any of those failing is a 403 or 422 with nothing stored. There is no path
through this view that writes a face without a matching consent row, and there
is no endpoint anywhere that *matches* one — recognition is a later phase.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import NotFound, PermissionDenied, ValidationError
from services.vision import enrolment


class EnrolSerializer(serializers.Serializer):
    conversation = serializers.UUIDField()
    reservation = serializers.CharField(max_length=12)
    # Not a checkbox default. Absent means no.
    consent = serializers.BooleanField(default=False)
    language = serializers.CharField(required=False, allow_blank=True, max_length=8, default="")


def _session_key(request: Request) -> str:
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key or ""


def _reservation_for(request: Request, hotel, conversation_id, code: str):
    """The booking this capture belongs to, or a 404.

    The conversation is the kiosk's only proof of identity. Requiring the
    reservation to have come from *this* conversation means a guest cannot attach
    photographs to somebody else's stay by guessing a confirmation code — and
    those codes are read aloud across a lobby.
    """
    from apps.booking.models import Reservation
    from apps.reception.models import Conversation

    conversation = Conversation.all_objects.filter(
        pk=conversation_id, tenant=hotel, is_deleted=False
    ).first()
    if conversation is None:
        raise NotFound("Conversation not found.")
    if not request.user.is_authenticated and conversation.session_key != _session_key(request):
        raise NotFound("Conversation not found.")

    reservation = Reservation.all_objects.filter(
        tenant=hotel, code__iexact=code.strip(), conversation=conversation, is_deleted=False
    ).first()
    if reservation is None:
        raise NotFound("That booking was not made in this conversation.")
    return reservation


class EnrolmentStatusView(APIView):
    """Should the kiosk offer this at all, and on what terms?

    The kiosk asks before it draws the consent screen, so a property with the
    feature off never shows a guest a camera it has no right to use.
    """

    permission_classes = [AllowAny]

    @extend_schema(tags=["vision"], responses={200: OpenApiResponse(description="Capture policy")})
    def get(self, request: Request) -> Response:
        return Response(enrolment.status(getattr(request, "tenant", None)))


class EnrolmentView(APIView):
    """Store a capture session, or record that the guest refused."""

    permission_classes = [AllowAny]
    throttle_scope = "ai_voice"  # same tight bucket: multipart, unauthenticated

    @extend_schema(tags=["vision"], request=EnrolSerializer)
    def post(self, request: Request) -> Response:
        hotel = getattr(request, "tenant", None)
        if not enrolment.is_enabled(hotel):
            # Refused before anything is read. An uploaded face that is then
            # rejected has still been uploaded.
            raise PermissionDenied(
                "Face capture is not enabled for this property. Nothing was stored."
            )

        serializer = EnrolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        reservation = _reservation_for(request, hotel, data["conversation"], data["reservation"])
        guest = reservation.guest
        language = data["language"] or reservation.guest.language

        if not data["consent"]:
            consent = enrolment.decline(hotel=hotel, guest=guest, language=language)
            return Response(
                {"stored": 0, "consent": str(consent.pk), "declined": True},
                status=status.HTTP_200_OK,
            )

        frames = _frames_from(request)
        if not frames:
            raise ValidationError("No frames were sent.")

        result = enrolment.enrol(
            hotel=hotel,
            guest=guest,
            frames=frames,
            reservation=reservation,
            language=language,
        )
        return Response(_payload(result), status=status.HTTP_201_CREATED)


def _frames_from(request: Request) -> list[enrolment.Frame]:
    """Read the multipart frames, capped at the session size.

    Anything beyond ``FRAME_COUNT`` is dropped rather than accepted-and-ignored:
    a client that sent twenty frames should not be led to believe twenty were
    stored.
    """
    files = request.FILES.getlist("frames") or request.FILES.getlist("frame")
    return [
        enrolment.Frame(
            data=item.read(),
            content_type=(item.content_type or "image/jpeg").split(";")[0],
        )
        for item in files[: enrolment.FRAME_COUNT]
    ]


def _payload(result) -> dict[str, Any]:
    return {
        "stored": result.stored,
        "declined": False,
        "reservation": result.reservation_code,
        "consent": result.consent_id,
        "expires_at": result.expires_at.isoformat(),
        "retention_days": result.retention_days,
    }
