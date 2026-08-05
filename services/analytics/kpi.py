"""The numbers on the dashboard.

Definitions matter more than the SQL here, because a hotelier will compare
these against their own spreadsheet on day one and any disagreement destroys
trust in the whole system:

*Occupancy* = occupied sellable rooms / sellable rooms. Out-of-order rooms are
excluded from both sides — counting a room you cannot sell against yourself is
how a full hotel shows 92%.

*ADR* (average daily rate) = room revenue / rooms sold. **Room revenue only** —
tax, service charge and restaurant spend are excluded, which is the industry
definition and the one that makes ADR comparable between hotels.

*RevPAR* = room revenue / sellable rooms, equivalently ADR × occupancy. It is
computed independently here so a mismatch between the two surfaces a bug rather
than hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.billing.models import ChargeType, FolioLine, Payment, PaymentStatus
from apps.booking.models import Reservation, ReservationRoom, ReservationStatus
from apps.core.utils import money
from apps.rooms.models import Room, RoomStatus
from services.booking import availability

if TYPE_CHECKING:  # pragma: no cover
    from apps.tenants.models import Hotel


@dataclass
class DayStats:
    day: date
    total_rooms: int
    occupied: int
    available: int
    occupancy_rate: float
    room_revenue: Decimal
    adr: Decimal
    revpar: Decimal
    arrivals: int
    departures: int
    in_house: int


def for_day(hotel: Hotel, day: date | None = None) -> DayStats:
    day = day or timezone.localdate()
    occ = availability.occupancy(hotel, day)

    room_revenue = FolioLine.all_objects.filter(
        tenant=hotel,
        is_deleted=False,
        is_voided=False,
        charge_type=ChargeType.ROOM,
        business_date=day,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    rooms_sold = occ["occupied"]
    sellable = occ["total_rooms"]

    return DayStats(
        day=day,
        total_rooms=sellable,
        occupied=rooms_sold,
        available=occ["available"],
        occupancy_rate=occ["occupancy_rate"],
        room_revenue=money(room_revenue),
        adr=money(room_revenue / rooms_sold) if rooms_sold else Decimal("0"),
        revpar=money(room_revenue / sellable) if sellable else Decimal("0"),
        arrivals=Reservation.all_objects.filter(
            tenant=hotel,
            is_deleted=False,
            check_in=day,
            status__in=[
                ReservationStatus.PENDING,
                ReservationStatus.CONFIRMED,
                ReservationStatus.CHECKED_IN,
            ],
        ).count(),
        departures=Reservation.all_objects.filter(
            tenant=hotel,
            is_deleted=False,
            check_out=day,
            status__in=[ReservationStatus.CHECKED_IN, ReservationStatus.CHECKED_OUT],
        ).count(),
        in_house=Reservation.all_objects.filter(
            tenant=hotel, is_deleted=False, status=ReservationStatus.CHECKED_IN
        ).count(),
    )


def room_status_breakdown(hotel: Hotel) -> list[dict]:
    """The donut on the prototype dashboard.

    Occupancy is taken from live allocations rather than the room's own status
    flag: the flag is the housekeeping view and can lag a check-in by minutes.
    """
    counts = dict(
        Room.all_objects.filter(tenant=hotel, is_deleted=False)
        .values_list("status")
        .annotate(total=Count("id"))
    )
    occupied_now = (
        ReservationRoom.all_objects.filter(
            tenant=hotel,
            is_deleted=False,
            blocks_inventory=True,
            room__isnull=False,
            stay__contains=timezone.localdate(),
        )
        .values("room_id")
        .distinct()
        .count()
    )
    total = sum(counts.values())
    dirty = counts.get(RoomStatus.VACANT_DIRTY, 0)
    maintenance = counts.get(RoomStatus.OUT_OF_ORDER, 0) + counts.get(RoomStatus.OUT_OF_SERVICE, 0)
    available = max(0, total - occupied_now - dirty - maintenance)

    rows = [
        {"label": "Occupied", "count": occupied_now, "colour": "#6366f1"},
        {"label": "Available", "count": available, "colour": "#22c55e"},
        {"label": "Dirty", "count": dirty, "colour": "#f59e0b"},
        {"label": "Maintenance", "count": maintenance, "colour": "#ef4444"},
    ]
    for row in rows:
        row["percent"] = round(row["count"] / total * 100) if total else 0
    return rows


def revenue_today(hotel: Hotel, day: date | None = None) -> Decimal:
    """Everything charged today, not just rooms — what the owner asks for."""
    day = day or timezone.localdate()
    total = FolioLine.all_objects.filter(
        tenant=hotel, is_deleted=False, is_voided=False, business_date=day
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return money(total)


def payments_today(hotel: Hotel, day: date | None = None) -> Decimal:
    day = day or timezone.localdate()
    total = Payment.all_objects.filter(
        tenant=hotel,
        is_deleted=False,
        status=PaymentStatus.CAPTURED,
        received_at__date=day,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return money(total)


def outstanding_balance(hotel: Hotel) -> Decimal:
    from apps.billing.models import Folio, FolioStatus

    total = Folio.all_objects.filter(
        tenant=hotel, is_deleted=False, status=FolioStatus.OPEN
    ).aggregate(total=Sum("balance"))["total"] or Decimal("0")
    return money(total)


def trend(hotel: Hotel, days: int = 14) -> list[dict]:
    """Occupancy and room revenue per day, for the dashboard chart."""
    today = timezone.localdate()
    series = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        stats = for_day(hotel, day)
        series.append(
            {
                "date": day.isoformat(),
                "label": day.strftime("%d %b"),
                "occupancy": round(stats.occupancy_rate, 1),
                "revenue": float(stats.room_revenue),
                "adr": float(stats.adr),
            }
        )
    return series


def comparison(hotel: Hotel) -> dict:
    """Today against yesterday, for the little up/down deltas."""
    today = for_day(hotel)
    yesterday = for_day(hotel, timezone.localdate() - timedelta(days=1))

    def delta(now: float, before: float) -> float:
        if not before:
            return 0.0
        return round((now - before) / before * 100, 1)

    return {
        "occupancy": delta(today.occupancy_rate, yesterday.occupancy_rate),
        "revenue": delta(float(today.room_revenue), float(yesterday.room_revenue)),
        "adr": delta(float(today.adr), float(yesterday.adr)),
        "revpar": delta(float(today.revpar), float(yesterday.revpar)),
    }


def recent_bookings(hotel: Hotel, limit: int = 8):
    return (
        Reservation.all_objects.filter(tenant=hotel, is_deleted=False)
        .select_related("guest")
        .prefetch_related("allocations__room")
        .order_by("-created_at")[:limit]
    )


def ai_snapshot(hotel: Hotel) -> dict:
    """AI usage and self-service rate — goal.txt §8's headline metric."""
    from apps.ai_center.models import UsageLog
    from apps.reception.models import Conversation, ConversationStatus

    since = timezone.now() - timedelta(days=1)
    conversations = Conversation.all_objects.filter(
        tenant=hotel, is_deleted=False, started_at__gte=since
    )
    total = conversations.count()
    resolved = conversations.filter(status=ConversationStatus.RESOLVED).count()

    usage = UsageLog.objects.filter(tenant=hotel, created_at__gte=since).aggregate(
        calls=Count("id"), cost=Sum("cost_usd"), failures=Count("id", filter=Q(success=False))
    )
    return {
        "conversations": total,
        "resolved": resolved,
        "self_service_rate": (resolved / total * 100) if total else 0.0,
        "calls": usage["calls"] or 0,
        "cost_usd": usage["cost"] or Decimal("0"),
        "failures": usage["failures"] or 0,
    }
