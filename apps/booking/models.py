"""Reservations, room allocation, check-in and check-out.

The double-booking guarantee lives here, and PostgreSQL enforces it, not
application code. ``ReservationRoom`` carries a ``DateRangeField`` and an
``ExclusionConstraint``: the database physically refuses two overlapping stays
in the same room. Two receptionists clicking Confirm at the same instant, a
retried request, a careless import — none of them can oversell a room.

Date semantics: the stay range is ``[check_in, check_out)``. The departure day
belongs to the next guest, which is how hotels actually work and means
back-to-back stays are not an overlap.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel
from apps.core.utils import short_code


class ReservationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CONFIRMED = "confirmed", _("Confirmed")
    CHECKED_IN = "checked_in", _("Checked in")
    CHECKED_OUT = "checked_out", _("Checked out")
    CANCELLED = "cancelled", _("Cancelled")
    NO_SHOW = "no_show", _("No show")


#: Statuses that hold inventory. A cancelled stay must not block the room.
BLOCKING_STATUSES = (
    ReservationStatus.PENDING,
    ReservationStatus.CONFIRMED,
    ReservationStatus.CHECKED_IN,
)


class BookingSource(models.TextChoices):
    WALK_IN = "walk_in", _("Walk-in")
    KIOSK = "kiosk", _("AI kiosk")
    PHONE = "phone", _("Phone")
    WEBSITE = "website", _("Website")
    OTA = "ota", _("Online travel agent")
    CORPORATE = "corporate", _("Corporate")


class Reservation(TenantOwnedModel):
    code = models.CharField(
        _("confirmation code"),
        max_length=12,
        db_index=True,
        help_text=_("Read aloud over the phone; excludes ambiguous glyphs."),
    )
    guest = models.ForeignKey("guests.Guest", on_delete=models.PROTECT, related_name="reservations")
    status = models.CharField(
        max_length=20,
        choices=ReservationStatus.choices,
        default=ReservationStatus.PENDING,
        db_index=True,
    )
    source = models.CharField(
        max_length=20, choices=BookingSource.choices, default=BookingSource.WALK_IN
    )

    check_in = models.DateField(db_index=True)
    check_out = models.DateField(db_index=True)
    adults = models.PositiveSmallIntegerField(default=1)
    children = models.PositiveSmallIntegerField(default=0)

    rate_plan = models.ForeignKey(
        "rooms.RatePlan", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    special_requests = models.TextField(blank=True)
    internal_notes = models.TextField(blank=True)

    # Snapshot of the quote at booking time. Recomputing from today's rates
    # would silently reprice a stay when a season boundary moves.
    room_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    service_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)

    signature = models.ImageField(upload_to="bookings/signatures/%Y/%m/", null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    conversation = models.ForeignKey(
        "reception.Conversation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reservations",
        help_text=_("Set when the AI kiosk took the booking. Links money to a transcript."),
    )

    class Meta:
        verbose_name = _("reservation")
        verbose_name_plural = _("reservations")
        ordering = ("-check_in", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=models.Q(is_deleted=False),
                name="uniq_reservation_code_per_hotel",
            ),
            models.CheckConstraint(
                condition=models.Q(check_out__gt=models.F("check_in")),
                name="reservation_at_least_one_night",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "check_in"]),
            models.Index(fields=["tenant", "check_out"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} · {self.guest} · {self.check_in}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._unique_code()
        return super().save(*args, **kwargs)

    def _unique_code(self) -> str:
        for _attempt in range(10):
            candidate = short_code(8)
            if not Reservation.all_objects.filter(
                tenant_id=self.tenant_id, code=candidate
            ).exists():
                return candidate
        # Astronomically unlikely; fail loudly rather than write a duplicate.
        raise RuntimeError("Could not generate a unique reservation code.")

    def clean(self) -> None:
        if self.check_out and self.check_in and self.check_out <= self.check_in:
            raise ValidationError({"check_out": _("Check-out must be after check-in.")})

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    @property
    def guest_count(self) -> int:
        return self.adults + self.children

    @property
    def holds_inventory(self) -> bool:
        return self.status in BLOCKING_STATUSES

    @property
    def is_in_house(self) -> bool:
        return self.status == ReservationStatus.CHECKED_IN

    @property
    def can_check_in(self) -> bool:
        return self.status in {ReservationStatus.PENDING, ReservationStatus.CONFIRMED}

    @property
    def balance_due(self) -> Decimal:
        folio = getattr(self, "folio", None)
        return folio.balance if folio else Decimal("0")


class ReservationRoom(TenantOwnedModel):
    """One room, held for one date range.

    A reservation may hold several rooms — a family taking two doubles is one
    booking, one folio, two allocations.
    """

    reservation = models.ForeignKey(
        Reservation, on_delete=models.CASCADE, related_name="allocations"
    )
    room_type = models.ForeignKey(
        "rooms.RoomType", on_delete=models.PROTECT, related_name="allocations"
    )
    room = models.ForeignKey(
        "rooms.Room",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="allocations",
        help_text=_("Assigned at booking or at check-in; a type may be held first."),
    )

    # Mirrors the reservation dates, but as a range so the database can reason
    # about overlap. Kept in sync by ``services.booking``.
    stay = DateRangeField(
        help_text=_("[check-in, check-out) — the departure day belongs to the next guest.")
    )

    # Denormalised from the parent reservation's status. An exclusion constraint
    # can only see this row's own columns, so "does this still hold inventory"
    # has to live here rather than being joined from the reservation.
    blocks_inventory = models.BooleanField(default=True, db_index=True)

    adults = models.PositiveSmallIntegerField(default=1)
    children = models.PositiveSmallIntegerField(default=0)
    rate_snapshot = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, help_text=_("Average nightly rate as quoted.")
    )
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = _("room allocation")
        verbose_name_plural = _("room allocations")
        ordering = ("stay",)
        constraints = [
            # The entire double-booking guarantee, in one declaration.
            #
            # Two rows for the same room whose stays overlap cannot both exist.
            # Scoped to rows that actually hold a room and still block
            # inventory, so cancellations free the room and type-only holds do
            # not collide with each other.
            ExclusionConstraint(
                name="no_double_booked_room",
                expressions=[
                    ("room", RangeOperators.EQUAL),
                    ("stay", RangeOperators.OVERLAPS),
                ],
                condition=models.Q(room__isnull=False, is_deleted=False, blocks_inventory=True),
            )
        ]
        indexes = [
            models.Index(fields=["reservation"]),
            models.Index(fields=["room"]),
        ]

    def __str__(self) -> str:
        target = self.room or self.room_type
        return f"{target} · {self.stay.lower} → {self.stay.upper}"

    @property
    def nights(self) -> int:
        if not self.stay or not self.stay.lower or not self.stay.upper:
            return 0
        return (self.stay.upper - self.stay.lower).days


class StayEvent(TenantOwnedModel):
    """Audit trail of arrivals, departures and moves.

    Separate from the status field: "when exactly did they arrive and who
    processed it" comes up in disputes, and one timestamp cannot describe a
    room move or a re-check-in.
    """

    class Kind(models.TextChoices):
        CHECK_IN = "check_in", _("Check-in")
        CHECK_OUT = "check_out", _("Check-out")
        ROOM_MOVE = "room_move", _("Room move")
        EXTEND = "extend", _("Stay extended")
        CANCEL = "cancel", _("Cancelled")
        NO_SHOW = "no_show", _("Marked no-show")

    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    room = models.ForeignKey(
        "rooms.Room", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    detail = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    performed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = _("stay event")
        verbose_name_plural = _("stay events")
        ordering = ("-occurred_at",)
        indexes = [models.Index(fields=["reservation", "-occurred_at"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.reservation.code}"


def tonight(nights: int = 1) -> tuple[date, date]:
    """Convenience for walk-ins: tonight, for N nights."""
    start = timezone.localdate()
    return start, start + timedelta(days=nights)
