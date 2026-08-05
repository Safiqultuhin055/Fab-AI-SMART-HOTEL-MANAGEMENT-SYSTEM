"""AI Reception conversations, messages and human handoff.

Every guest interaction is persisted. Three reasons, all operational rather than
academic:

*Handoff needs history.* When the AI escalates, the staff member who picks it up
must see what was already said. Asking the guest to repeat themselves is exactly
the failure this product exists to remove.

*Prompt regression needs evidence.* ``tests/ai_eval`` is only meaningful against
questions guests actually asked (goal.txt D14).

*Disputes need a record.* "Your robot told me late checkout was free" has to be
resolvable from data.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel


class Channel(models.TextChoices):
    KIOSK = "kiosk", _("Reception kiosk")
    WEB = "web", _("Staff web")
    # The public booking page. Separate from WEB, which is a receptionist trying a
    # question on their own laptop: these two produce different reservations and a
    # manager asking "how much did the website bring in" needs them apart.
    WEBSITE = "website", _("Website")
    PWA = "pwa", _("Guest app")
    WHATSAPP = "whatsapp", _("WhatsApp")
    PHONE = "phone", _("Phone")


class ConversationStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    RESOLVED = "resolved", _("Resolved by AI")
    HANDOFF = "handoff", _("Handed to staff")
    ABANDONED = "abandoned", _("Abandoned")


class MessageRole(models.TextChoices):
    SYSTEM = "system", _("System")
    GUEST = "user", _("Guest")
    ASSISTANT = "assistant", _("AI")
    STAFF = "staff", _("Staff")


class GreetingStyle(models.TextChoices):
    """How the kiosk opens. A property-level decision, never a default.

    A religious greeting to every guest who walks through the door is a choice
    the hotel makes about its own identity and its guests, and a resort full of
    foreign tourists will answer it differently from a city business hotel.
    """

    NEUTRAL = "neutral", _("Warm — “Good evening, and welcome to …”")
    FORMAL = "formal", _("Formal — “Good evening. Welcome to …”")
    ISLAMIC = "islamic", _("Assalamu alaikum")


class ConversationMode(models.TextChoices):
    """What the assistant is currently doing.

    Booking mode costs a much larger prompt — the whole live room list — so it
    is entered on intent and left again when the booking is placed or dropped,
    rather than paid for on every "where is the lift" question.
    """

    CHAT = "chat", _("Answering questions")
    BOOKING = "booking", _("Taking a booking")


class HandoffReason(models.TextChoices):
    LOW_CONFIDENCE = "low_confidence", _("AI confidence below threshold")
    REPEATED = "repeated", _("Guest repeated the same question")
    BLOCKED_TOPIC = "blocked_topic", _("Blocked topic")
    TURN_LIMIT = "turn_limit", _("Conversation turn limit reached")
    GUEST_REQUEST = "guest_request", _("Guest asked for a human")
    AI_UNAVAILABLE = "ai_unavailable", _("AI unavailable — manual mode")
    ERROR = "error", _("AI error")


class Conversation(TenantOwnedModel):
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.KIOSK)
    status = models.CharField(
        max_length=20,
        choices=ConversationStatus.choices,
        default=ConversationStatus.ACTIVE,
        db_index=True,
    )

    # Guests are not modelled until P1, and a kiosk walk-up may never identify
    # themselves at all. A display name plus a session key holds a conversation
    # together; the guest FK slots in later without a data migration.
    guest_name = models.CharField(_("guest name"), max_length=150, blank=True)
    session_key = models.CharField(max_length=64, db_index=True, blank=True)
    language = models.CharField(max_length=8, default="en")
    language_confirmed = models.BooleanField(
        default=False,
        help_text=_(
            "The guest has settled the language, either by naming one or simply by "
            "asking a question. Until then the kiosk offers the choice."
        ),
    )

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    turn_count = models.PositiveSmallIntegerField(default=0)

    total_tokens = models.PositiveIntegerField(default=0)
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    handoff_reason = models.CharField(
        max_length=30, choices=HandoffReason.choices, blank=True, db_index=True
    )
    satisfaction = models.PositiveSmallIntegerField(null=True, blank=True)

    mode = models.CharField(
        max_length=12, choices=ConversationMode.choices, default=ConversationMode.CHAT
    )
    # The booking being assembled, held server-side rather than in the browser.
    # A kiosk that reloads mid-booking must not lose the four things the guest
    # already said, and a draft in the client is a draft a guest could edit.
    booking_draft = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Work-in-progress booking. Cleared once the reservation is created."),
    )

    class Meta:
        verbose_name = _("conversation")
        verbose_name_plural = _("conversations")
        ordering = ("-started_at",)
        indexes = [
            models.Index(fields=["tenant", "status", "-started_at"]),
            models.Index(fields=["channel", "-started_at"]),
        ]

    def __str__(self) -> str:
        who = self.guest_name or "walk-up guest"
        return f"{who} · {self.get_channel_display()} · {self.started_at:%d %b %H:%M}"

    @property
    def is_open(self) -> bool:
        return self.status == ConversationStatus.ACTIVE

    def close(self, status: str, reason: str = "") -> None:
        self.status = status
        self.handoff_reason = reason
        self.ended_at = timezone.now()
        self.save(update_fields=["status", "handoff_reason", "ended_at", "updated_at"])


class Message(TenantOwnedModel):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=12, choices=MessageRole.choices)
    content = models.TextField()

    # --- AI metadata (assistant messages only) --------------------------------
    model_name = models.CharField(max_length=120, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    confidence = models.FloatField(null=True, blank=True)
    citations = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Sources the answer was drawn from. Empty means unsourced."),
    )

    # --- Voice ----------------------------------------------------------------
    was_spoken = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("message")
        verbose_name_plural = _("messages")
        ordering = ("created_at",)
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self) -> str:
        return f"{self.get_role_display()}: {self.content[:60]}"


class Handoff(TenantOwnedModel):
    """A conversation the AI could not finish.

    Its own row rather than a flag on Conversation because the queue has its own
    lifecycle — created, claimed, resolved — and the front desk sorts it by wait
    time.
    """

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="handoffs"
    )
    reason = models.CharField(max_length=30, choices=HandoffReason.choices)
    detail = models.CharField(max_length=255, blank=True)

    claimed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="handoffs"
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("handoff request")
        verbose_name_plural = _("handoff queue")
        ordering = ("created_at",)
        indexes = [models.Index(fields=["tenant", "resolved_at", "created_at"])]

    def __str__(self) -> str:
        return f"Handoff ({self.get_reason_display()}) — {self.conversation_id}"

    @property
    def waiting_seconds(self) -> int:
        end = self.claimed_at or timezone.now()
        return int((end - self.created_at).total_seconds())
