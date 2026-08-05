"""Rooms, room types, rate plans and the live status board.

The inventory every other module reads from. Reception quotes from it,
housekeeping works through it, billing prices against it, and the AI concierge
answers "do you have a sea-view room" out of it.

Money note: every amount is ``DecimalField``. Floats are how an invoice ends up
one poisha off and a night audit fails to balance.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActiveModel, TenantOwnedModel


class BedType(models.TextChoices):
    SINGLE = "single", _("Single")
    TWIN = "twin", _("Twin")
    DOUBLE = "double", _("Double")
    QUEEN = "queen", _("Queen")
    KING = "king", _("King")
    BUNK = "bunk", _("Bunk")


class RoomStatus(models.TextChoices):
    """The housekeeping/front-desk status board (SRS Module 3).

    Occupancy is derived from reservations, not stored here — two sources of
    truth for "is someone in room 101" is how a hotel double-sells a room.
    This is the *physical* state of the room.
    """

    VACANT_CLEAN = "vacant_clean", _("Vacant clean")
    VACANT_DIRTY = "vacant_dirty", _("Vacant dirty")
    OCCUPIED = "occupied", _("Occupied")
    OUT_OF_ORDER = "out_of_order", _("Out of order")
    OUT_OF_SERVICE = "out_of_service", _("Out of service (maintenance)")


class Amenity(TenantOwnedModel, ActiveModel):
    name = models.CharField(_("name"), max_length=80)
    icon = models.CharField(_("icon"), max_length=40, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _("amenity")
        verbose_name_plural = _("amenities")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class RoomType(TenantOwnedModel, ActiveModel):
    code = models.SlugField(_("code"), max_length=24)
    name = models.CharField(_("name"), max_length=80)
    description = models.TextField(blank=True)

    base_occupancy = models.PositiveSmallIntegerField(_("standard occupancy"), default=2)
    max_occupancy = models.PositiveSmallIntegerField(_("maximum occupancy"), default=3)
    max_children = models.PositiveSmallIntegerField(default=2)
    bed_type = models.CharField(max_length=20, choices=BedType.choices, default=BedType.DOUBLE)
    size_sqm = models.PositiveSmallIntegerField(null=True, blank=True)

    base_rate = models.DecimalField(
        _("base rate per night"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_("Fallback price when no rate plan matches the date."),
    )
    extra_person_rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    amenities = models.ManyToManyField(Amenity, blank=True, related_name="room_types")
    # Used by the AI concierge and, from P3, by CLIP image search.
    view = models.CharField(
        _("view"),
        max_length=40,
        blank=True,
        help_text=_("sea · city · garden · pool — free text, surfaced to guests."),
    )
    is_smoking = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = _("room type")
        verbose_name_plural = _("room types")
        ordering = ("sort_order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=models.Q(is_deleted=False),
                name="uniq_room_type_code_per_hotel",
            ),
            models.CheckConstraint(
                condition=models.Q(max_occupancy__gte=models.F("base_occupancy")),
                name="room_type_occupancy_sane",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class RoomTypePhoto(TenantOwnedModel):
    """A photograph of a room type, shown to the guest while they book it.

    On the room type rather than the room: a guest choosing a Deluxe is choosing
    the category, and photographing all forty of them is work no hotel will do.
    A specific room's own picture — a fault, a maintenance note — belongs on the
    housekeeping record, not here.

    ``caption`` is what a guest reads, so it is translatable free text ("the
    balcony", "বাথরুম") and not a slug.
    """

    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(_("photo"), upload_to="rooms/types/%Y/%m/")
    caption = models.CharField(_("caption"), max_length=120, blank=True)
    sort_order = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Lowest first. The first photo is the one the kiosk leads with."),
    )

    class Meta:
        verbose_name = _("room photo")
        verbose_name_plural = _("room photos")
        # created_at breaks the tie so a set of photos uploaded at once, all on
        # the default sort_order, still comes back in a stable order — otherwise
        # the "cover" photo changes between two page loads.
        ordering = ("sort_order", "created_at")
        indexes = [models.Index(fields=["room_type", "sort_order"])]

    def __str__(self) -> str:
        return f"{self.room_type.code} · {self.caption or self.image.name}"


class Room(TenantOwnedModel, ActiveModel):
    number = models.CharField(_("room number"), max_length=12)
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name="rooms")

    floor = models.SmallIntegerField(default=1)
    wing = models.CharField(max_length=24, blank=True)

    status = models.CharField(
        max_length=20,
        choices=RoomStatus.choices,
        default=RoomStatus.VACANT_CLEAN,
        db_index=True,
    )
    status_changed_at = models.DateTimeField(default=timezone.now)
    out_of_order_until = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _("room")
        verbose_name_plural = _("rooms")
        ordering = ("floor", "number")
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "number"],
                condition=models.Q(is_deleted=False),
                name="uniq_room_number_per_hotel",
            )
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["room_type", "status"]),
        ]

    def __str__(self) -> str:
        return f"Room {self.number}"

    @property
    def is_sellable(self) -> bool:
        """Can this room be assigned to a new stay at all?

        A dirty room is still sellable — housekeeping will reach it before the
        guest does. Out of order is not.
        """
        return self.is_active and self.status not in {
            RoomStatus.OUT_OF_ORDER,
            RoomStatus.OUT_OF_SERVICE,
        }

    def set_status(self, status: str, *, note: str = "", user=None) -> RoomStatusLog:
        """Change status and leave a trail.

        Housekeeping disputes ("that room was never cleaned") are settled from
        this log, so the transition is recorded even when nothing else changes.
        """
        previous = self.status
        self.status = status
        self.status_changed_at = timezone.now()
        self.save(update_fields=["status", "status_changed_at", "updated_at"])

        return RoomStatusLog.objects.create(
            tenant=self.tenant,
            room=self,
            from_status=previous,
            to_status=status,
            note=note[:255],
            changed_by=user,
        )


class RoomStatusLog(TenantOwnedModel):
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="status_logs")
    from_status = models.CharField(max_length=20, choices=RoomStatus.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=RoomStatus.choices)
    note = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = _("room status change")
        verbose_name_plural = _("room status history")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["room", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.room} {self.from_status} → {self.to_status}"


class RatePlan(TenantOwnedModel, ActiveModel):
    """A named pricing policy, e.g. "Standard", "Non-refundable", "Corporate"."""

    code = models.SlugField(max_length=24)
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)

    includes_breakfast = models.BooleanField(default=False)
    is_refundable = models.BooleanField(default=True)
    min_nights = models.PositiveSmallIntegerField(default=1)
    advance_days = models.PositiveSmallIntegerField(
        default=0, help_text=_("Must be booked at least this many days ahead.")
    )
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text=_("Applied to the room type base rate when no dated rate exists."),
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("rate plan")
        verbose_name_plural = _("rate plans")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=models.Q(is_deleted=False),
                name="uniq_rate_plan_code_per_hotel",
            ),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_default=True, is_deleted=False),
                name="uniq_default_rate_plan_per_hotel",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        # Same demote-then-promote rule as AI Center: making something the
        # default must not 500 because something else already is.
        if self.is_default:
            RatePlan.all_objects.filter(tenant_id=self.tenant_id, is_default=True).exclude(
                pk=self.pk
            ).update(is_default=False, updated_at=timezone.now())
        return super().save(*args, **kwargs)


class RoomRate(TenantOwnedModel):
    """Price for one room type, on one rate plan, over a date range.

    Seasonal pricing lives here. Overlapping ranges are allowed on purpose — a
    Christmas override should be able to sit on top of a winter season — and
    ``services.rooms.pricing`` resolves the conflict by priority, then by the
    narrowest range.
    """

    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name="rates")
    rate_plan = models.ForeignKey(RatePlan, on_delete=models.CASCADE, related_name="rates")

    valid_from = models.DateField(db_index=True)
    valid_to = models.DateField(db_index=True, help_text=_("Inclusive."))
    price = models.DecimalField(max_digits=12, decimal_places=2)

    priority = models.PositiveSmallIntegerField(
        default=0, help_text=_("Higher wins when ranges overlap.")
    )
    # Weekend and holiday pricing without inventing a second table.
    weekdays = models.CharField(
        max_length=20,
        blank=True,
        help_text=_("Comma-separated 0-6 (Mon-Sun). Blank means every day."),
    )
    label = models.CharField(max_length=80, blank=True)

    class Meta:
        verbose_name = _("room rate")
        verbose_name_plural = _("room rates")
        ordering = ("-priority", "valid_from")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_to__gte=models.F("valid_from")),
                name="room_rate_range_sane",
            )
        ]
        indexes = [models.Index(fields=["room_type", "rate_plan", "valid_from", "valid_to"])]

    def __str__(self) -> str:
        return f"{self.room_type} {self.valid_from}–{self.valid_to}: {self.price}"

    def applies_on(self, day) -> bool:
        if not (self.valid_from <= day <= self.valid_to):
            return False
        if not self.weekdays:
            return True
        allowed = {int(part) for part in self.weekdays.split(",") if part.strip().isdigit()}
        return day.weekday() in allowed
