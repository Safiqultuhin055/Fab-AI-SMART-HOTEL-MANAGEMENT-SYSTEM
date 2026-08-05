"""AI Reception HTTP API.

Thin by design: parse, delegate to ``services.reception.orchestrator``, serialise.
Any conversation rule you find here is in the wrong place.

The kiosk is a public terminal in a lobby, so these endpoints accept a session
rather than a login, and are throttled hard. Everything they can reach is scoped
to one hotel and one conversation.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasTenant
from apps.core.exceptions import AIError, NotFound
from apps.reception.models import Channel, Conversation, ConversationStatus, Handoff, MessageRole
from services.ai import gateway
from services.reception import orchestrator, redact

MAX_AUDIO_BYTES = 8 * 1024 * 1024


# ==============================================================================
# Serializers
# ==============================================================================


class MessageSerializer(serializers.Serializer):
    role = serializers.CharField()
    content = serializers.CharField()
    created_at = serializers.DateTimeField()
    citations = serializers.ListField(required=False)
    confidence = serializers.FloatField(required=False, allow_null=True)
    latency_ms = serializers.IntegerField(required=False)


class StartSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=Channel.choices, default=Channel.KIOSK)
    guest_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    # Blank, not "en". Defaulting to English here silently overrode the hotel's
    # configured kiosk language, so a Bangla property greeted in Bangla and then
    # answered every question in English.
    language = serializers.CharField(required=False, allow_blank=True, default="", max_length=8)


class ChatSerializer(serializers.Serializer):
    conversation = serializers.UUIDField()
    message = serializers.CharField(max_length=2000)
    spoken = serializers.BooleanField(default=False)
    # Set when the guest picked a language from the kiosk control rather than by
    # saying it. Needed because speech recognition listens in ONE language at a
    # time: a guest speaking Bangla into an English-tuned recogniser gets a
    # transcript of Latin noise, so the request to switch can never be heard. The
    # control is the way out of that, and it has to outrank text detection.
    language = serializers.CharField(required=False, allow_blank=True, default="", max_length=8)


# ==============================================================================
# Helpers
# ==============================================================================


def _session_key(request: Request) -> str:
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key or ""


def _load(request: Request, conversation_id) -> Conversation:
    """Fetch a conversation this caller is allowed to touch.

    Staff may open any conversation in their hotel. An anonymous kiosk may only
    continue the one its own session started — otherwise a guest could enumerate
    UUIDs and read someone else's check-in chat.
    """
    tenant = getattr(request, "tenant", None)
    queryset = Conversation.all_objects.filter(pk=conversation_id, is_deleted=False)
    if tenant is not None:
        queryset = queryset.filter(tenant=tenant)

    conversation = queryset.first()
    if conversation is None:
        raise NotFound("Conversation not found.")

    if not request.user.is_authenticated and conversation.session_key != _session_key(request):
        raise NotFound("Conversation not found.")
    return conversation


def _turn_payload(turn) -> dict[str, Any]:
    """One exchange, as the guest's browser is allowed to see it.

    ``reply`` is redacted here and nowhere else. The model is still asked to cite its
    facts as [1], [2] — that is how the server knows whether an answer was sourced at
    all — but those markers index our own CONTEXT block and mean nothing to a guest.
    They were reaching the screen inline and again as a "তথ্যসূত্র:" footer.

    ``citations`` still travels: it is structured data for the staff console and the
    transcript, not a line of text under the bubble, and the client no longer renders
    it. ``Message.content`` in the database keeps the markers, because "which fact did
    that answer come from" is a question an operator has to be able to answer later.
    """
    return {
        "conversation": str(turn.conversation.pk),
        "reply": redact.for_guest(turn.reply),
        "citations": turn.citations,
        "confidence": round(turn.confidence, 2),
        "handoff": turn.handoff,
        "handoff_reason": turn.handoff_reason,
        "latency_ms": turn.latency_ms,
        "ai_used": turn.ai_used,
        "status": turn.conversation.status,
        "turn_count": turn.conversation.turn_count,
        "mode": turn.conversation.mode,
        # The kiosk retunes its speech recognition and voice from this: a guest
        # who switches to English must then also be *heard* in English.
        "language": turn.language or turn.conversation.language,
        "booking": turn.booking,
        "reservation_code": turn.reservation_code,
    }


# ==============================================================================
# Endpoints
# ==============================================================================


class StartConversationView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "ai_chat"

    @extend_schema(tags=["reception"], request=StartSerializer)
    def post(self, request: Request) -> Response:
        serializer = StartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        hotel = getattr(request, "tenant", None)
        if hotel is None:
            # Every conversation belongs to a property. Failing here with a
            # readable message beats a null-constraint 500 three layers down.
            return Response(
                {
                    "detail": (
                        "No hotel is bound to this session. Open the kiosk as "
                        "/reception/kiosk/?hotel=<CODE> or send an X-Hotel-Code header."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        conversation = orchestrator.start(
            hotel=hotel,
            channel=data["channel"],
            session_key=_session_key(request),
            guest_name=data.get("guest_name", ""),
            language=data.get("language", ""),
        )
        return Response(
            {
                "conversation": str(conversation.pk),
                # Deliberately empty. The welcome is held back until the guest has
                # said which language they read, because greeting first means
                # guessing — and the welcome is the one sentence you least want to
                # get wrong. It arrives as the reply to their choice.
                "greeting": "",
                "language": conversation.language,
                # The opening, in both languages, as separate parts so the kiosk can
                # speak each in its own voice. A single utterance cannot be
                # bilingual, and Bangla read by an English voice is worse than not
                # reading it out at all.
                "language_prompt": orchestrator.language_prompt(hotel),
                "ai": gateway.status(str(hotel.pk) if hotel else None),
            },
            status=status.HTTP_201_CREATED,
        )


class ChatView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "ai_chat"

    @extend_schema(tags=["reception"], request=ChatSerializer)
    def post(self, request: Request) -> Response:
        serializer = ChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        conversation = _load(request, data["conversation"])
        turn = orchestrator.respond(
            conversation,
            data["message"],
            spoken=data["spoken"],
            language=data.get("language", ""),
        )
        return Response(_turn_payload(turn))


class VoiceView(APIView):
    """Speech in, answer out.

    One round trip on purpose: transcribe, answer and (optionally) synthesise in
    a single request. Three separate calls from a kiosk on hotel wifi would add
    two more network round trips to a budget of three seconds (goal.txt §6).
    """

    permission_classes = [AllowAny]
    throttle_scope = "ai_voice"

    @extend_schema(
        tags=["reception"],
        responses={200: OpenApiResponse(description="Transcript plus assistant reply")},
    )
    def post(self, request: Request) -> Response:
        audio = request.FILES.get("audio")
        conversation_id = request.data.get("conversation")
        if audio is None or not conversation_id:
            return Response(
                {"detail": "audio file and conversation id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if audio.size > MAX_AUDIO_BYTES:
            return Response(
                {"detail": "audio too large"}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )

        conversation = _load(request, conversation_id)
        hotel = conversation.tenant

        try:
            transcription = gateway.transcribe(
                audio.read(),
                language=conversation.language,
                module="reception",
                tenant_id=str(hotel.pk) if hotel else None,
            )
        except AIError as exc:
            return Response(
                {"detail": f"speech recognition unavailable: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        text = transcription.text.strip()
        if not text:
            return Response({"transcript": "", "reply": "I did not catch that. Could you repeat?"})

        turn = orchestrator.respond(conversation, text, spoken=True)
        return Response({"transcript": text, **_turn_payload(turn)})


class SpeakView(APIView):
    """Text to speech for the avatar. Returns audio bytes."""

    permission_classes = [AllowAny]
    throttle_scope = "ai_voice"

    @extend_schema(tags=["reception"])
    def post(self, request: Request) -> Response:
        from django.http import HttpResponse

        text = (request.data.get("text") or "").strip()[:1000]
        if not text:
            return Response({"detail": "text required"}, status=status.HTTP_400_BAD_REQUEST)

        tenant = getattr(request, "tenant", None)
        # The property decides how reception sounds; the client may name a voice
        # but the hotel's own setting is the default rather than the provider's.
        voice = (request.data.get("voice") or "").strip()[:40]
        if not voice and tenant is not None:
            voice = tenant.kiosk_voice_name

        # Without the language a multi-language adapter cannot choose a voice, and
        # Bangla read by an English voice is worse than silence.
        language = (request.data.get("language") or "").strip()[:8]
        if not language and tenant is not None:
            language = tenant.kiosk_language

        try:
            speech = gateway.speak(
                text,
                voice=voice,
                language=language,
                module="reception",
                tenant_id=str(tenant.pk) if tenant else None,
            )
        except AIError as exc:
            return Response(
                {"detail": f"speech synthesis unavailable: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return HttpResponse(speech.audio, content_type=speech.mime_type)


class NudgeView(APIView):
    """The guest went quiet. Ask the next question.

    Called by the client on a silence timer, so it is deliberately the cheapest
    endpoint here: the question comes from the validated booking draft, no model is
    called, and nothing is spent. A booking page left open in a tab is the commonest
    way a booking dies, and the assistant noticing costs nothing.

    204 when there is nothing to say — a closed conversation, or a guest who has not
    spoken yet — so the client can stop its timer without parsing a body.
    """

    permission_classes = [AllowAny]
    throttle_scope = "ai_chat"

    @extend_schema(
        tags=["reception"],
        responses={
            200: OpenApiResponse(description="The next question, as a normal turn"),
            204: OpenApiResponse(description="Nothing to ask right now"),
        },
    )
    def post(self, request: Request) -> Response:
        conversation = _load(request, request.data.get("conversation"))
        turn = orchestrator.nudge(conversation)
        if turn is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(_turn_payload(turn))


class HandoffView(APIView):
    """The 'talk to a human' button. Always available (goal.txt R7)."""

    permission_classes = [AllowAny]
    throttle_scope = "ai_chat"

    @extend_schema(tags=["reception"])
    def post(self, request: Request) -> Response:
        conversation = _load(request, request.data.get("conversation"))
        turn = orchestrator.request_human(conversation, detail=request.data.get("detail", ""))
        return Response(_turn_payload(turn))


class HistoryView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["reception"], responses=MessageSerializer(many=True))
    def get(self, request: Request, conversation_id) -> Response:
        conversation = _load(request, conversation_id)
        messages = conversation.messages.exclude(role=MessageRole.SYSTEM).order_by("created_at")
        data = MessageSerializer(messages, many=True).data

        # A guest reading their own transcript back gets it the way they were told it,
        # citation markers and all removed. Staff get it verbatim: an operator
        # auditing an answer needs to see which fact it came from, and stripping that
        # would make the transcript useless for the one job it has.
        if not request.user.is_authenticated:
            for row in data:
                row["content"] = redact.for_guest(row.get("content", ""))

        return Response(
            {
                "conversation": str(conversation.pk),
                "status": conversation.status,
                "messages": data,
            }
        )


class HandoffQueueView(APIView):
    """Staff-side queue. Sorted by wait time, longest first."""

    permission_classes = [IsAuthenticated, HasTenant]
    required_permission = "core.access_reception"

    @extend_schema(tags=["reception"])
    def get(self, request: Request) -> Response:  # noqa: ARG002 - DRF signature
        queue = (
            Handoff.objects.filter(resolved_at__isnull=True)
            .select_related("conversation", "claimed_by")
            .order_by("created_at")[:50]
        )
        return Response(
            {
                "count": queue.count() if hasattr(queue, "count") else len(queue),
                "items": [
                    {
                        "id": str(item.pk),
                        "conversation": str(item.conversation_id),
                        "guest": item.conversation.guest_name or "Walk-up guest",
                        "reason": item.get_reason_display(),
                        "detail": item.detail,
                        "waiting_seconds": item.waiting_seconds,
                        "claimed_by": item.claimed_by.full_name if item.claimed_by else None,
                    }
                    for item in queue
                ],
            }
        )

    @extend_schema(tags=["reception"])
    def post(self, request: Request) -> Response:
        """Claim or resolve a queue item."""
        handoff = Handoff.objects.filter(pk=request.data.get("id")).first()
        if handoff is None:
            raise NotFound("Handoff not found.")

        action = request.data.get("action", "claim")
        if action == "claim":
            orchestrator.claim(handoff, request.user)
        elif action == "resolve":
            handoff.resolved_at = timezone.now()
            handoff.save(update_fields=["resolved_at", "updated_at"])
            handoff.conversation.close(ConversationStatus.RESOLVED)
        else:
            return Response({"detail": "unknown action"}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"id": str(handoff.pk), "action": action})
