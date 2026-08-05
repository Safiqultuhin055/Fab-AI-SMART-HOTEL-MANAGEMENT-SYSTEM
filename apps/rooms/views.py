from __future__ import annotations

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Q
from django.utils import timezone

from apps.booking.models import ReservationRoom
from apps.core.views import module_page
from apps.rooms.models import Room, RoomStatus, RoomType
from services.booking import availability
from services.booking.availability import UNSELLABLE

MODULE_KEY = "rooms"

#: The three answers a receptionist actually asks this board for.
#:
#: Physical status (clean, dirty, out of order) is a different question and keeps
#: its own line: a room can be spotless and sold, or dirty and free tonight.
SOLD = "sold"  # somebody is in it tonight, or arrives today
BOOKED = "booked"  # free tonight, promised to somebody for a date ahead
BLOCKED = "blocked"  # out of order or out of service — cannot be sold at all
FREE = "free"  # sellable, and nothing on it

BOOKING_FILTERS = [
    (SOLD, "In house tonight"),
    (BOOKED, "Booked ahead"),
    (FREE, "Free to sell"),
    (BLOCKED, "Out of service"),
]


def _allocations(hotel, today):
    """Every live hold on a room from today onwards, in one query.

    Two things come out of it: who is in each room tonight, and which rooms are
    already promised to somebody later. Both matter for "is this room booked" — a
    room that is empty tonight and sold from Friday is not a free room, and
    showing it as one is how a walk-in gets sold a bed that is already gone.
    """
    live = ReservationRoom.all_objects.filter(
        tenant=hotel,
        is_deleted=False,
        blocks_inventory=True,
        room__isnull=False,
    ).select_related("reservation__guest")

    now_map = {row.room_id: row for row in live.filter(stay__contains=today)}

    # Ordered by arrival so the last write per room is the earliest one: "booked
    # from" has to name the next guest through the door, not whichever row the
    # database happened to return first.
    next_map = {
        row.room_id: row
        for row in live.filter(stay__startswith__gt=today).order_by("-stay__startswith")
    }

    return now_map, next_map


def _lead_photos(types) -> dict[str, dict]:
    """One photograph per room type, keyed by room type id as a string.

    On the type rather than the room, because that is where a hotel photographs:
    the category, not all forty Deluxes. Every room of a type shows its type's
    picture, and a type nobody has photographed yet shows a labelled tile rather
    than a stock bedroom.
    """
    from services.rooms import media

    gallery = media.gallery([room_type.pk for room_type in types], limit=1)
    return {key: photos[0] for key, photos in gallery.items() if photos}


@login_required
@permission_required("core.access_rooms", raise_exception=True)
def home(request):
    """The status board reception and housekeeping both work from."""
    hotel = getattr(request, "tenant", None)
    today = timezone.localdate()

    rooms = (
        Room.all_objects.filter(tenant=hotel, is_deleted=False)
        .select_related("room_type")
        .order_by("floor", "number")
    )

    now_map, next_map = _allocations(hotel, today)

    status_filter = request.GET.get("status", "")
    type_filter = request.GET.get("type", "")
    booking_filter = request.GET.get("booking", "")
    if status_filter:
        rooms = rooms.filter(status=status_filter)
    if type_filter:
        rooms = rooms.filter(room_type__code=type_filter)

    types = list(
        RoomType.all_objects.filter(tenant=hotel, is_deleted=False)
        .annotate(room_count=Count("rooms", filter=Q(rooms__is_deleted=False)))
        .order_by("sort_order", "name")
    )
    photos = _lead_photos(types)
    # Hung on the object rather than looked up in the template: Django templates
    # cannot index a dict by a variable key without a custom filter, and a filter
    # that exists only to work around that is a filter somebody has to maintain.
    for room_type in types:
        room_type.lead_photo = photos.get(str(room_type.pk))

    labels = dict(BOOKING_FILTERS)
    floors: dict[int, list] = {}
    # Per floor as well as per property: a housekeeper works one floor at a time,
    # and "how many of these forty are free" should not need counting by eye.
    per_floor: dict[int, dict] = {}
    counts = {SOLD: 0, BOOKED: 0, FREE: 0, BLOCKED: 0, "total": 0}
    for room in rooms:
        allocation = now_map.get(room.id)
        upcoming = next_map.get(room.id)
        # Precedence, and it is the order a receptionist would ask in: is somebody
        # in it, can I sell it at all, is it promised to somebody, is it free.
        #
        # An out-of-order room used to fall through to "free", which is the worst
        # of the four to get wrong: it is the one state where the room cannot be
        # sold for any money at all.
        if allocation:
            state = SOLD
        elif room.status in UNSELLABLE:
            state = BLOCKED
        elif upcoming:
            state = BOOKED
        else:
            state = FREE
        # Counted before the filter, so the totals still describe the whole
        # property while the board below shows one slice of it.
        counts[state] += 1
        counts["total"] += 1

        if booking_filter and booking_filter != state:
            continue

        floors.setdefault(room.floor, []).append(
            {
                "room": room,
                "photo": photos.get(str(room.room_type_id)),
                "allocation": allocation,
                "guest": allocation.reservation.guest if allocation else None,
                "until": allocation.stay.upper if allocation else None,
                "occupied": allocation is not None,
                "state": state,
                # Spelled out for the tile's aria-label: a screen reader gets the
                # same four words a sighted user gets from the colour.
                "state_label": labels[state],
                # Who is coming, and when, for a room that is free tonight but sold.
                "next_guest": upcoming.reservation.guest if upcoming else None,
                "next_arrival": upcoming.stay.lower if upcoming else None,
            }
        )
        floor_counts = per_floor.setdefault(room.floor, {SOLD: 0, BOOKED: 0, FREE: 0, BLOCKED: 0})
        floor_counts[state] += 1

    groups = [
        {"floor": floor, "rooms": entries, "counts": per_floor[floor]}
        for floor, entries in sorted(floors.items())
    ]

    return module_page(
        request,
        MODULE_KEY,
        template="modules/rooms.html",
        context={
            "floors": groups,
            "types": types,
            "type_photos": photos,
            "statuses": RoomStatus.choices,
            "booking_filters": BOOKING_FILTERS,
            "status_filter": status_filter,
            "type_filter": type_filter,
            "booking_filter": booking_filter,
            "counts": counts,
            "summary": availability.occupancy(hotel, today) if hotel else {},
            "today": today,
        },
    )
