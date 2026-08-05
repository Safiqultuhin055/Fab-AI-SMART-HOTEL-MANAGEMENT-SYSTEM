"""Creating, changing and completing a stay.

Every state change goes through here so that four things always happen
together: the reservation status moves, the allocations' ``blocks_inventory``
flag follows it, the room's physical status is updated, and a ``StayEvent`` is
written. Doing any one of those in a view guarantees the other three eventually
get forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.audit import record as audit
from apps.accounts.models import AuditAction
from apps.booking.models import (
    BookingSource,
    Reservation,
    ReservationRoom,
    ReservationStatus,
    StayEvent,
)
from apps.core.exceptions import Conflict, NotFound, ValidationError
from apps.core.utils import money
from apps.rooms.models import Room, RoomStatus
from services.billing import folio as billing
from services.booking import availability
from services.rooms import pricing

if TYPE_CHECKING:  # pragma: no cover
    from apps.guests.models import Guest
    from apps.rooms.models import RatePlan, RoomType
    from apps.tenants.models import Hotel


@dataclass
class BookingRequest:
    room_type: RoomType
    rooms: int = 1
    adults: int = 1
    children: int = 0


@transaction.atomic
def create(
    *,
    hotel: Hotel,
    guest: Guest,
    check_in: date,
    check_out: date,
    room_type: RoomType,
    rooms: int = 1,
    adults: int = 1,
    children: int = 0,
    rate_plan: RatePlan | None = None,
    source: str = BookingSource.WALK_IN,
    special_requests: str = "",
    internal_notes: str = "",
    user=None,
    conversation=None,
    assign_rooms: bool = True,
    allow_past: bool = False,
) -> Reservation:
    """Quote, hold the rooms and open the folio, atomically.

    If the rooms cannot be held the whole thing rolls back — a reservation with
    no rooms is worse than no reservation, because reception will believe it.
    """
    if check_out <= check_in:
        raise ValidationError("Check-out must be after check-in.")
    if check_in < timezone.localdate() and not allow_past:
        # ``allow_past`` exists for backfill — migrating history from another
        # PMS, or seeding. Reception can never set it, so a mistyped year is
        # still caught at the desk.
        raise ValidationError("Check-in cannot be in the past.")
    if guest.is_blacklisted:
        raise Conflict(f"{guest.full_name} is blacklisted; a manager must override this.")
    if adults + children > room_type.max_occupancy * rooms:
        raise ValidationError(
            f"{room_type.name} sleeps {room_type.max_occupancy}; "
            f"{adults + children} guests need more rooms."
        )

    if rate_plan is None:
        from apps.rooms.models import RatePlan as RatePlanModel

        rate_plan = RatePlanModel.all_objects.filter(
            tenant=hotel, is_default=True, is_deleted=False
        ).first()

    free = list(
        availability.available_rooms(hotel, check_in, check_out, room_type=room_type)[:rooms]
    )
    if assign_rooms and len(free) < rooms:
        raise Conflict(
            f"Only {len(free)} {room_type.name} room(s) free for those dates; {rooms} requested."
        )

    estimate = pricing.quote(
        hotel=hotel,
        room_type=room_type,
        check_in=check_in,
        check_out=check_out,
        rate_plan=rate_plan,
        adults=adults,
        rooms=rooms,
    )

    reservation = Reservation.objects.create(
        tenant=hotel,
        guest=guest,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        children=children,
        rate_plan=rate_plan,
        source=source,
        special_requests=special_requests,
        internal_notes=internal_notes,
        status=ReservationStatus.CONFIRMED,
        room_total=estimate.room_total,
        service_total=estimate.service_amount,
        tax_total=estimate.tax_amount,
        grand_total=estimate.grand_total,
        created_by=user,
        conversation=conversation,
    )

    per_room_subtotal = money(estimate.room_total / rooms) if rooms else Decimal("0")
    for index in range(rooms):
        room = free[index] if assign_rooms and index < len(free) else None
        _allocate(
            reservation,
            room_type,
            room,
            check_in,
            check_out,
            adults,
            children,
            estimate.average_nightly,
            per_room_subtotal,
        )

    billing.open_folio(reservation)

    audit(
        AuditAction.CREATE,
        summary=(
            f"reservation {reservation.code} for {guest.full_name}, "
            f"{check_in}→{check_out}, {estimate.grand_total} {estimate.currency}"
        ),
        obj=reservation,
        hotel_id=str(hotel.pk),
    )
    return reservation


def _allocate(
    reservation, room_type, room, check_in, check_out, adults, children, rate, subtotal
) -> ReservationRoom:
    try:
        return ReservationRoom.objects.create(
            tenant=reservation.tenant,
            reservation=reservation,
            room_type=room_type,
            room=room,
            stay=(check_in, check_out),
            adults=adults,
            children=children,
            rate_snapshot=rate,
            subtotal=subtotal,
            blocks_inventory=True,
        )
    except IntegrityError as exc:
        # The exclusion constraint fired: someone took the room between the
        # availability check and this insert. That race is exactly why the
        # constraint exists; translate it into something reception can act on.
        if "no_double_booked_room" in str(exc):
            raise Conflict(
                f"Room {room.number if room else ''} was taken while this booking was "
                "being made. Please pick another room."
            ) from exc
        raise


@transaction.atomic
def assign_room(allocation: ReservationRoom, room: Room, *, user=None) -> ReservationRoom:
    """Put a specific room against a held room type, or move an existing one."""
    if room.room_type_id != allocation.room_type_id:
        raise ValidationError(
            f"Room {room.number} is a {room.room_type.name}; "
            f"this booking holds a {allocation.room_type.name}."
        )
    if not room.is_sellable:
        raise Conflict(f"Room {room.number} is {room.get_status_display().lower()}.")

    previous = allocation.room
    allocation.room = room
    try:
        allocation.save(update_fields=["room", "updated_at"])
    except IntegrityError as exc:
        if "no_double_booked_room" in str(exc):
            raise Conflict(f"Room {room.number} is already booked for those dates.") from exc
        raise

    StayEvent.objects.create(
        tenant=allocation.tenant,
        reservation=allocation.reservation,
        kind=StayEvent.Kind.ROOM_MOVE,
        room=room,
        detail=f"{previous.number if previous else 'unassigned'} → {room.number}",
        performed_by=user,
    )
    return allocation


@transaction.atomic
def check_in(reservation: Reservation, *, user=None) -> Reservation:
    if not reservation.can_check_in:
        raise Conflict(
            f"Cannot check in a booking that is {reservation.get_status_display().lower()}."
        )

    unassigned = reservation.allocations.filter(room__isnull=True)
    for allocation in unassigned:
        free = availability.available_rooms(
            reservation.tenant,
            reservation.check_in,
            reservation.check_out,
            room_type=allocation.room_type,
            exclude_reservation=reservation,
        ).first()
        if free is None:
            raise Conflict(
                f"No {allocation.room_type.name} room is free to assign. "
                "Move the guest to another room type or free a room first."
            )
        assign_room(allocation, free, user=user)

    reservation.status = ReservationStatus.CHECKED_IN
    reservation.checked_in_at = timezone.now()
    reservation.save(update_fields=["status", "checked_in_at", "updated_at"])

    for allocation in reservation.allocations.select_related("room"):
        if allocation.room:
            allocation.room.set_status(
                RoomStatus.OCCUPIED, note=f"Check-in {reservation.code}", user=user
            )

    billing.open_folio(reservation)
    StayEvent.objects.create(
        tenant=reservation.tenant,
        reservation=reservation,
        kind=StayEvent.Kind.CHECK_IN,
        detail=f"{reservation.guest.full_name} checked in",
        performed_by=user,
    )
    audit(
        AuditAction.UPDATE,
        summary=f"check-in {reservation.code}",
        obj=reservation,
        hotel_id=str(reservation.tenant_id),
    )
    return reservation


@transaction.atomic
def check_out(reservation: Reservation, *, user=None, force: bool = False):
    """Settle the folio, free the rooms, issue the invoice.

    Refuses on an outstanding balance unless ``force`` — a night manager can
    release a guest with an unpaid balance, but it has to be a decision, not an
    accident.
    """
    if reservation.status != ReservationStatus.CHECKED_IN:
        raise Conflict("Only a checked-in booking can be checked out.")

    folio = billing.open_folio(reservation)
    folio.recalculate()

    if folio.balance > 0 and not force:
        raise Conflict(
            f"{folio.balance} {folio.currency} outstanding. Take payment, or check out "
            "with the override if the balance is being carried."
        )

    invoice = billing.issue_invoice(folio, user=user)
    if folio.balance <= 0:
        folio.status = "settled"
        folio.settled_at = timezone.now()
        folio.save(update_fields=["status", "settled_at", "updated_at"])

    reservation.status = ReservationStatus.CHECKED_OUT
    reservation.checked_out_at = timezone.now()
    reservation.save(update_fields=["status", "checked_out_at", "updated_at"])

    # Free the inventory and send the room to housekeeping.
    reservation.allocations.update(blocks_inventory=False)
    for allocation in reservation.allocations.select_related("room"):
        if allocation.room:
            allocation.room.set_status(
                RoomStatus.VACANT_DIRTY, note=f"Check-out {reservation.code}", user=user
            )

    guest = reservation.guest
    guest.total_stays += 1
    guest.total_spend = money(Decimal(guest.total_spend) + folio.charges_total)
    guest.last_stay_at = timezone.localdate()
    guest.save(update_fields=["total_stays", "total_spend", "last_stay_at", "updated_at"])

    StayEvent.objects.create(
        tenant=reservation.tenant,
        reservation=reservation,
        kind=StayEvent.Kind.CHECK_OUT,
        detail=f"invoice {invoice.number}",
        performed_by=user,
    )
    audit(
        AuditAction.UPDATE,
        summary=f"check-out {reservation.code}, invoice {invoice.number}",
        obj=reservation,
        hotel_id=str(reservation.tenant_id),
    )
    return invoice


@transaction.atomic
def cancel(reservation: Reservation, *, reason: str = "", user=None) -> Reservation:
    if reservation.status in {ReservationStatus.CHECKED_OUT, ReservationStatus.CANCELLED}:
        raise Conflict(f"Already {reservation.get_status_display().lower()}.")
    if reservation.status == ReservationStatus.CHECKED_IN:
        raise Conflict("Check the guest out instead of cancelling an in-house stay.")

    reservation.status = ReservationStatus.CANCELLED
    reservation.cancelled_at = timezone.now()
    reservation.cancellation_reason = reason[:255]
    reservation.save(update_fields=["status", "cancelled_at", "cancellation_reason", "updated_at"])
    # Releasing the hold is the whole point of a cancellation.
    reservation.allocations.update(blocks_inventory=False)

    StayEvent.objects.create(
        tenant=reservation.tenant,
        reservation=reservation,
        kind=StayEvent.Kind.CANCEL,
        detail=reason[:255],
        performed_by=user,
    )
    audit(
        AuditAction.UPDATE,
        summary=f"cancelled {reservation.code}: {reason}",
        obj=reservation,
        hotel_id=str(reservation.tenant_id),
    )
    return reservation


@transaction.atomic
def mark_no_show(reservation: Reservation, *, user=None) -> Reservation:
    if reservation.status not in {ReservationStatus.PENDING, ReservationStatus.CONFIRMED}:
        raise Conflict("Only a pending or confirmed booking can be a no-show.")

    reservation.status = ReservationStatus.NO_SHOW
    reservation.save(update_fields=["status", "updated_at"])
    reservation.allocations.update(blocks_inventory=False)

    StayEvent.objects.create(
        tenant=reservation.tenant,
        reservation=reservation,
        kind=StayEvent.Kind.NO_SHOW,
        performed_by=user,
    )
    return reservation


def find(hotel: Hotel, code: str) -> Reservation:
    reservation = Reservation.all_objects.filter(
        tenant=hotel, code__iexact=code.strip(), is_deleted=False
    ).first()
    if reservation is None:
        raise NotFound(f"No booking found with code {code}.")
    return reservation


def arrivals(hotel: Hotel, on_date: date | None = None):
    day = on_date or timezone.localdate()
    return (
        Reservation.all_objects.filter(
            tenant=hotel,
            is_deleted=False,
            check_in=day,
            status__in=[ReservationStatus.PENDING, ReservationStatus.CONFIRMED],
        )
        .select_related("guest")
        .order_by("created_at")
    )


def departures(hotel: Hotel, on_date: date | None = None):
    day = on_date or timezone.localdate()
    return (
        Reservation.all_objects.filter(
            tenant=hotel,
            is_deleted=False,
            check_out=day,
            status=ReservationStatus.CHECKED_IN,
        )
        .select_related("guest")
        .order_by("created_at")
    )


def in_house(hotel: Hotel):
    return (
        Reservation.all_objects.filter(
            tenant=hotel, is_deleted=False, status=ReservationStatus.CHECKED_IN
        )
        .select_related("guest")
        .prefetch_related("allocations__room")
        .order_by("check_out")
    )
