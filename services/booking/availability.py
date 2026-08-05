"""What is free, and when.

One query answers it: a room is available for a date range if it is sellable
and has no allocation whose stay overlaps that range. The overlap test is done
in PostgreSQL with the range operator, not in Python — pulling every allocation
into memory to compare dates would be both slower and a second, divergent
implementation of the rule the exclusion constraint already enforces.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from django.db.models import Count, Q

from apps.booking.models import ReservationRoom
from apps.rooms.models import Room, RoomStatus, RoomType

if TYPE_CHECKING:  # pragma: no cover
    from django.db.models import QuerySet

    from apps.tenants.models import Hotel

#: Physical states that cannot take a guest at all.
UNSELLABLE = (RoomStatus.OUT_OF_ORDER, RoomStatus.OUT_OF_SERVICE)


@dataclass
class TypeAvailability:
    room_type: RoomType
    total: int
    available: int
    rooms: list[Room]

    @property
    def is_available(self) -> bool:
        return self.available > 0


def busy_room_ids(hotel: Hotel, check_in: date, check_out: date, *, exclude_reservation=None):
    """Rooms already held for any part of ``[check_in, check_out)``."""
    booked = ReservationRoom.all_objects.filter(
        tenant=hotel,
        is_deleted=False,
        blocks_inventory=True,
        room__isnull=False,
        stay__overlap=(check_in, check_out),
    )
    if exclude_reservation is not None:
        booked = booked.exclude(reservation=exclude_reservation)
    return booked.values_list("room_id", flat=True)


def available_rooms(
    hotel: Hotel,
    check_in: date,
    check_out: date,
    *,
    room_type: RoomType | None = None,
    exclude_reservation=None,
) -> QuerySet[Room]:
    if check_out <= check_in:
        raise ValueError("check_out must be after check_in")

    rooms = Room.all_objects.filter(tenant=hotel, is_deleted=False, is_active=True).exclude(
        status__in=UNSELLABLE
    )
    if room_type is not None:
        rooms = rooms.filter(room_type=room_type)

    return (
        rooms.exclude(
            id__in=busy_room_ids(
                hotel, check_in, check_out, exclude_reservation=exclude_reservation
            )
        )
        .select_related("room_type")
        .order_by("floor", "number")
    )


def by_type(
    hotel: Hotel, check_in: date, check_out: date, *, guests: int = 1
) -> list[TypeAvailability]:
    """Availability grouped by room type — what reception and the AI quote from.

    Types too small for the party are excluded rather than shown as available:
    offering a single to four people is worse than offering nothing.
    """
    free = available_rooms(hotel, check_in, check_out)
    free_by_type: dict = {}
    for room in free:
        free_by_type.setdefault(room.room_type_id, []).append(room)

    totals = {
        row["id"]: row["room_count"]
        for row in RoomType.all_objects.filter(tenant=hotel, is_deleted=False)
        .annotate(room_count=Count("rooms", filter=Q(rooms__is_deleted=False)))
        .values("id", "room_count")
    }

    results: list[TypeAvailability] = []
    types = RoomType.all_objects.filter(
        tenant=hotel, is_deleted=False, is_active=True, max_occupancy__gte=guests
    ).order_by("sort_order", "name")

    for room_type in types:
        rooms = free_by_type.get(room_type.id, [])
        results.append(
            TypeAvailability(
                room_type=room_type,
                total=totals.get(room_type.id, 0),
                available=len(rooms),
                rooms=rooms,
            )
        )
    return results


def occupancy(hotel: Hotel, on_date: date) -> dict:
    """Occupancy for one night — the dashboard's headline number.

    Counts allocations, not reservations: a booking holding three rooms fills
    three rooms.
    """
    sellable = Room.all_objects.filter(tenant=hotel, is_deleted=False, is_active=True).exclude(
        status__in=UNSELLABLE
    )
    total = sellable.count()

    occupied = (
        ReservationRoom.all_objects.filter(
            tenant=hotel,
            is_deleted=False,
            blocks_inventory=True,
            room__isnull=False,
            stay__contains=on_date,
        )
        .values("room_id")
        .distinct()
        .count()
    )

    return {
        "date": on_date,
        "total_rooms": total,
        "occupied": occupied,
        "available": max(0, total - occupied),
        "occupancy_rate": (occupied / total * 100) if total else 0.0,
    }
