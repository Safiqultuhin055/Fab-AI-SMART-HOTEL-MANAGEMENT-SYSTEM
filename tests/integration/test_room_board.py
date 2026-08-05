"""The room board answers two questions at a glance: what does it look like, and
is it sold.

Before this it answered neither. Every tile was a number and a housekeeping
status, so "is 402 free" meant reading the reservations, and "what is 402 like"
meant opening another page. Worse, a room standing empty tonight and sold from
Friday looked exactly like a room nobody wants — which is how the same bed gets
sold twice.

The distinction the tests pin down:

    sold    somebody is in it tonight, or arrives today
    booked  free tonight, held for a date ahead
    free    nothing on it at all

Physical status (clean / dirty / out of order) is a separate axis and stays on its
own line. A spotless room can be sold; a dirty one can be free tonight.
"""

from __future__ import annotations

import pathlib
import re
from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import Role, RoleCode
from apps.core.context import set_request_context
from apps.rooms.models import RatePlan, Room, RoomStatus, RoomType, RoomTypePhoto
from apps.tenants.models import HotelMembership
from services.booking import reservations as booking

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

User = get_user_model()
TODAY = None  # set per test from timezone.localdate()

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def staff(db, hotel):
    call_command("seed_roles", "--prune", stdout=StringIO())
    user = User.objects.create_user(
        email="board@test.local", password="test-pass-12345", full_name="Board Reader"
    )
    HotelMembership.objects.create(
        user=user, hotel=hotel, role=Role.objects.get(code=RoleCode.MANAGER), is_default=True
    )
    return user


@pytest.fixture
def inventory(hotel):
    """Two types — one photographed, one not — and three rooms."""
    set_request_context(tenant_id=str(hotel.pk))

    deluxe = RoomType.all_objects.create(
        tenant=hotel, code="DLX", name="Deluxe King", base_rate=Decimal("7000.00")
    )
    plain = RoomType.all_objects.create(
        tenant=hotel, code="STD", name="Standard", base_rate=Decimal("3000.00")
    )
    RatePlan.all_objects.create(tenant=hotel, code="BAR", name="Best Available", is_default=True)
    RoomTypePhoto.all_objects.create(
        tenant=hotel,
        room_type=deluxe,
        image=ContentFile(PNG_1PX, name="dlx.png"),
        caption="The room",
    )

    return {
        "deluxe": deluxe,
        "plain": plain,
        # 101 and 102 are Deluxes (same photo), 201 is a Standard (no photo).
        "sold": Room.all_objects.create(tenant=hotel, number="101", room_type=deluxe, floor=1),
        "ahead": Room.all_objects.create(tenant=hotel, number="102", room_type=deluxe, floor=1),
        "free": Room.all_objects.create(tenant=hotel, number="201", room_type=plain, floor=2),
    }


def hold(hotel, guest, room, room_type, *, start, nights=2):
    """A reservation pinned to one specific room.

    ``reservations.create`` assigns a room of the type itself, and which one is
    not the test's business — so the allocation is then moved to the room this
    test is about.
    """
    reservation = booking.create(
        hotel=hotel,
        guest=guest,
        check_in=start,
        check_out=start + timedelta(days=nights),
        room_type=room_type,
    )
    booking.assign_room(reservation.allocations.first(), room)
    return reservation


@pytest.fixture
def booked(hotel, inventory, guest_factory):
    """101 occupied tonight; 102 sold from a week out; 201 untouched."""
    from django.utils import timezone

    today = timezone.localdate()

    tonight = hold(hotel, guest_factory(), inventory["sold"], inventory["deluxe"], start=today)
    later = hold(
        hotel,
        guest_factory(),
        inventory["ahead"],
        inventory["deluxe"],
        start=today + timedelta(days=7),
    )
    return {"tonight": tonight, "later": later}


def board(client, staff, query: str = "") -> str:
    client.force_login(staff)
    response = client.get(f"{reverse('rooms:home')}{query}")
    assert response.status_code == 200
    return response.content.decode()


#: The outer tile element. Matching '<div class="room-tile' alone lands on the
#: figure inside it, which is how this helper quietly returned half a tile.
TILE_MARK = '<div class="room-tile room-tile--'


CHIP = re.compile(
    r'state-chip[^>]*>\s*<span class="state-chip__count">\s*([0-9]+)\s*</span>'
    # Up to the anchor's close, not the first </span>: the label wraps a colour
    # dot in a span of its own, and a lazy match stopped there and captured nothing.
    r'\s*<span class="state-chip__label">(.*?)</span>\s*</a>',
    re.S,
)


def chips(body: str) -> dict[str, int]:
    """The filter chips as {label: count}.

    Parsed rather than string-matched: the count and the label are separate
    elements now, and a test asserting on the whitespace between them is a test
    about indentation.
    """
    return {
        " ".join(re.sub(r"<[^>]+>", " ", label).split()): int(count)
        for count, label in CHIP.findall(body)
    }


def tile(body: str, number: str) -> str:
    """The markup of one room's tile: this room's, and nothing after it."""
    start = body.index(f">{number}<")
    opening = body.rindex(TILE_MARK, 0, start)
    ends = [
        index
        for index in (body.find(TILE_MARK, start), body.find("Room types & rates", start))
        if index != -1
    ]
    return body[opening : min(ends)] if ends else body[opening:]


class TestEveryRoomShowsItsRoom:
    def test_a_room_carries_its_type_s_photograph(self, client, staff, inventory):
        body = board(client, staff)
        photo = RoomTypePhoto.all_objects.get(room_type=inventory["deluxe"]).image.url

        assert photo in tile(body, "101")
        # Same type, same picture — the photo is of the category, which is what a
        # hotel actually photographs.
        assert photo in tile(body, "102")

    def test_a_type_with_no_photograph_says_so_rather_than_faking_one(
        self, client, staff, inventory
    ):
        """A stock bedroom shown as this hotel's room is worse than no picture."""
        body = board(client, staff)
        standard = tile(body, "201")

        assert "room-tile__figure--blank" in standard
        assert "No photo" in standard
        assert "<img" not in standard

    def test_the_number_stays_readable_over_the_photograph(self, client, staff, inventory):
        """It is what somebody scans the board for."""
        assert "room-tile__number" in tile(board(client, staff), "101")

    def test_the_rates_table_shows_a_thumbnail_per_type(self, client, staff, inventory):
        body = board(client, staff)
        assert "room-thumb" in body
        assert body.count("room-thumb--blank") >= 1  # the Standard has no photo


class TestBookedAndFreeAreTellableApart:
    def test_a_room_occupied_tonight_reads_as_booked(self, client, staff, inventory, booked):
        body = board(client, staff)
        sold = tile(body, "101")

        assert "room-tile--sold" in sold
        assert "Booked" in sold
        assert booked["tonight"].guest.full_name in sold
        assert "until" in sold

    def test_a_room_free_tonight_but_sold_later_is_not_shown_as_free(
        self, client, staff, inventory, booked
    ):
        """The bug this exists to prevent: it is empty tonight, so it looked
        exactly like a room nobody wants, and gets sold to a walk-in for a stay
        that runs into somebody else's booking."""
        ahead = tile(board(client, staff), "102")

        assert "room-tile--booked" in ahead
        assert "Booked ahead" in ahead
        assert booked["later"].guest.full_name in ahead
        assert "from" in ahead

    def test_a_room_with_nothing_on_it_reads_as_free(self, client, staff, inventory, booked):
        free = tile(board(client, staff), "201")

        assert "room-tile--free" in free
        assert "Free" in free

    def test_the_physical_status_stays_its_own_line(self, client, staff, inventory, booked):
        """A sold room can be spotless and a free one can be dirty. One colour for
        both questions answers neither."""
        inventory["sold"].status = RoomStatus.VACANT_DIRTY
        inventory["sold"].save(update_fields=["status"])

        sold = tile(board(client, staff), "101")

        assert "room-tile--sold" in sold  # still sold
        assert "Vacant dirty" in sold  # and still dirty

    def test_a_room_out_of_service_is_not_offered_as_free(self, client, staff, inventory, booked):
        """The worst of the four to get wrong: it is the one state where the room
        cannot be sold for any money at all, and it used to fall through to
        "free" because nothing was booked on it."""
        inventory["free"].status = RoomStatus.OUT_OF_ORDER
        inventory["free"].save(update_fields=["status"])

        body = board(client, staff)
        blocked = tile(body, "201")

        assert "room-tile--blocked" in blocked
        assert "Out of service" in blocked
        marks = chips(body)
        assert marks["Free to sell"] == 0
        assert marks["Out of service"] == 1

    def test_out_of_service_still_shows_a_booking_sitting_on_it(
        self, client, staff, inventory, booked
    ):
        """Somebody has to move that guest. Hiding the booking because the room is
        blocked is how they find out at the desk on the day."""
        inventory["ahead"].status = RoomStatus.OUT_OF_SERVICE
        inventory["ahead"].save(update_fields=["status"])

        ahead = tile(board(client, staff), "102")

        assert "room-tile--blocked" in ahead
        assert booked["later"].guest.full_name in ahead

    def test_the_earliest_arrival_is_the_one_named(self, client, staff, inventory, guest_factory):
        """Two future bookings on one room: the board must name the next guest
        through the door, not whichever row came back first."""
        from django.utils import timezone

        today = timezone.localdate()
        hotel = inventory["ahead"].tenant
        # Booked later first, so passing cannot depend on insertion order.
        hold(
            hotel,
            guest_factory(),
            inventory["ahead"],
            inventory["deluxe"],
            start=today + timedelta(days=20),
            nights=1,
        )
        soon = hold(
            hotel,
            guest_factory(),
            inventory["ahead"],
            inventory["deluxe"],
            start=today + timedelta(days=3),
            nights=1,
        )

        ahead = tile(board(client, staff), "102")
        assert soon.guest.full_name in ahead


class TestTheCountsAndTheFilter:
    def test_the_four_totals_describe_the_property(self, client, staff, inventory, booked):
        marks = chips(board(client, staff))

        # The All chip counts what it filters, not what availability calls sellable.
        assert marks["All rooms"] == 3
        assert marks["In house tonight"] == 1
        assert marks["Booked ahead"] == 1
        assert marks["Free to sell"] == 1
        assert marks["Out of service"] == 0

    def test_a_filter_narrows_the_board_but_not_the_totals(self, client, staff, inventory, booked):
        """Numbers that change every time somebody narrows the view stop being the
        property's position."""
        body = board(client, staff, "?booking=free")

        assert ">201<" in body
        assert ">101<" not in body
        assert ">102<" not in body
        # ...and the counts still describe all three rooms.
        marks = chips(body)
        assert marks["All rooms"] == 3
        assert marks["In house tonight"] == 1
        assert marks["Free to sell"] == 1

    @pytest.mark.parametrize(
        ("state", "shown", "hidden"),
        [("sold", "101", "201"), ("booked", "102", "101"), ("free", "201", "102")],
    )
    def test_each_state_can_be_listed_on_its_own(
        self, client, staff, inventory, booked, state, shown, hidden
    ):
        body = board(client, staff, f"?booking={state}")
        assert f">{shown}<" in body
        assert f">{hidden}<" not in body

    def test_the_booking_filter_survives_a_type_filter(self, client, staff, inventory, booked):
        """The two selects submit a form; the state filter is a link. It has to
        travel with them or every filter change silently drops it."""
        body = board(client, staff, "?booking=booked")
        assert 'name="booking" value="booked"' in body

        body = board(client, staff, "?booking=free&type=STD")
        assert ">201<" in body
        assert ">101<" not in body


class TestItDoesNotGetSlowerWithMoreRooms:
    def test_photos_and_occupancy_are_not_per_room_queries(self, client, staff, inventory, booked):
        """A hundred-room property would otherwise fire a hundred photo lookups
        and a hundred reservation lookups to draw one board.

        Asserted as "the same number of queries with forty more rooms" rather than
        a fixed count, so the test is about the shape of the page and not about
        whichever query the shell or the navigation happens to run today.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client.force_login(staff)
        url = reverse("rooms:home")

        # One throwaway request first: the permission cache and the session are
        # populated on the way through, and counting that warm-up as the baseline
        # made the second request look seven queries cheaper than the first.
        client.get(url)

        with CaptureQueriesContext(connection) as first:
            client.get(url)
        baseline = len(first)

        for number in range(300, 340):
            Room.all_objects.create(
                tenant=inventory["free"].tenant,
                number=str(number),
                room_type=inventory["deluxe"],
                floor=3,
            )

        with CaptureQueriesContext(connection) as second:
            client.get(url)

        assert len(second) == baseline, f"{len(second) - baseline} extra queries for 40 extra rooms"


class TestTheBoardLayout:
    """Pinned because they were asked for, and because both are the kind of value
    somebody "tidies" later without knowing why it was chosen."""

    CSS = pathlib.Path(__file__).parents[2] / "static" / "css" / "ashos.css"

    def grid_block(self) -> str:
        css = self.CSS.read_text(encoding="utf-8")
        return css[css.index(".room-grid {") : css.index(".room-tile {")]

    def test_six_tiles_to_a_line(self):
        """A fixed column count, not auto-fill: the board is read as a floor plan,
        and a grid that reflows by available width puts 102 under 108 on one floor
        and under 109 on the next."""
        assert "repeat(6, 132px)" in self.grid_block()

    def test_the_column_is_the_picture_s_width(self):
        """A fluid column with a fixed-size photograph inside it is dead space
        either side of every thumbnail: 120 of photo + 6 of tile padding each side."""
        assert "132px" in self.grid_block()
        css = self.CSS.read_text(encoding="utf-8")
        tile = css[css.index(".room-tile {") : css.index(".room-tile:hover")]
        assert "padding: 6px;" in tile

    def test_it_steps_down_rather_than_squashing_on_a_narrow_window(self):
        block = self.grid_block()
        for columns in (5, 4, 3):
            assert f"repeat({columns}, 132px)" in block, columns

    def test_every_photograph_is_the_same_120_by_100(self):
        """Fixed, not an aspect ratio: forty tiles whose pictures are each a
        different height is a board that looks broken."""
        css = self.CSS.read_text(encoding="utf-8")
        figure = css[css.index(".room-tile__figure {") : css.index(".room-tile__photo {")]
        assert "width: 120px;" in figure
        assert "height: 100px;" in figure
