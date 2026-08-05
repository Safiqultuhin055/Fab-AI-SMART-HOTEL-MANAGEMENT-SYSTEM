"""The kiosk takes a real booking.

The model drives the conversation; it does not decide anything that costs money
or inventory. Every test here is about that line. A model can hallucinate a room
code, quote a room that sold out thirty seconds ago, invent a price, book a
family of five into a double, or claim the guest confirmed when they never gave
their name — and none of it may reach the database.
"""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.booking.models import BookingSource, Reservation
from apps.core.exceptions import Conflict
from apps.reception.models import Channel, ConversationMode
from apps.rooms.models import RatePlan, Room, RoomType
from services.ai import gateway
from services.ai.base import ChatResult, Usage
from services.reception import booking_agent, orchestrator

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

TOMORROW = (timezone.localdate() + timedelta(days=1)).isoformat()

#: A one-pixel PNG. The room gallery tests care about which photo comes back and
#: in what order, never about what is in it.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def inventory(hotel):
    from apps.core.context import set_request_context

    set_request_context(tenant_id=str(hotel.pk))
    deluxe = RoomType.all_objects.create(
        tenant=hotel,
        code="DLX",
        name="Deluxe King",
        base_occupancy=2,
        max_occupancy=3,
        base_rate=Decimal("7200.00"),
        view="sea",
    )
    single = RoomType.all_objects.create(
        tenant=hotel,
        code="STD",
        name="Standard Single",
        base_occupancy=1,
        max_occupancy=1,
        base_rate=Decimal("3000.00"),
    )
    RatePlan.all_objects.create(tenant=hotel, code="BAR", name="Best Available", is_default=True)
    for number in ("101", "102"):
        Room.all_objects.create(tenant=hotel, number=number, room_type=deluxe, floor=1)
    Room.all_objects.create(tenant=hotel, number="201", room_type=single, floor=2)
    return deluxe


@pytest.fixture
def convo(hotel, inventory):
    return orchestrator.start(hotel=hotel, channel=Channel.KIOSK)


@pytest.fixture
def booking_convo(convo):
    """A conversation already in booking mode.

    Intent detection has its own tests; these are about what happens once the
    booking is under way, so they should not fail because a phrase like
    "confirm" is — correctly — not a request to start booking.
    """
    convo.mode = ConversationMode.BOOKING
    convo.save(update_fields=["mode"])
    return convo


@pytest.fixture
def script(monkeypatch):
    """Queue up what the model 'says', turn by turn."""
    queue: list[str] = []

    def fake_chat(*args, **kwargs):
        text = queue.pop(0) if queue else "{}"
        return ChatResult(
            text=text, usage=Usage(50, 20), model="fake", provider="fake", latency_ms=12
        )

    monkeypatch.setattr(gateway, "chat", fake_chat)
    return queue


def turn_json(**overrides) -> str:
    payload = {
        "reply": "ঠিক আছে।",
        "booking": {
            "check_in": TOMORROW,
            "nights": 2,
            "room_code": "DLX",
            "rooms": 1,
            "adults": 2,
            "children": 0,
            "guest_name": "Rina Haque",
            "guest_phone": "01711000000",
        },
        "needs_more_info": False,
        "ready_to_confirm": False,
        "cancelled": False,
    }
    booking = payload["booking"] | overrides.pop("booking", {})
    return json.dumps({**payload, **overrides, "booking": booking}, ensure_ascii=False)


# ==============================================================================
# Intent
# ==============================================================================


class TestIntent:
    @pytest.mark.parametrize(
        "text",
        [
            "আমার একটা রুম দরকার",
            "রুম বুক করতে চাই",
            "আগামীকাল থেকে দুই রাত থাকতে চাই",
            "I want to book a room",
            "can I make a booking for tomorrow",
            "need a room for two nights",
        ],
    )
    def test_booking_intent_is_recognised(self, text):
        assert booking_agent.wants_booking(text) is True

    @pytest.mark.parametrize(
        "text",
        ["চেক আউট কখন?", "ভ্যাট কত?", "what is the wifi password", "where is the lift"],
    )
    def test_ordinary_questions_stay_on_the_cheap_path(self, text):
        """Booking mode costs a much bigger prompt. It must not fire on 'where
        is the lift'."""
        assert booking_agent.wants_booking(text) is False

    @pytest.mark.parametrize("text", ["থাক, লাগবে না", "বাতিল করুন", "never mind", "cancel that"])
    def test_backing_out_is_recognised(self, text):
        assert booking_agent.wants_out(text) is True


# ==============================================================================
# Snapshot
# ==============================================================================


class TestSnapshot:
    def test_carries_live_stock_and_the_real_price(self, hotel, inventory):
        from services.rooms import pricing

        check_in = timezone.localdate() + timedelta(days=1)
        offers = {o.code: o for o in booking_agent.room_snapshot(hotel, check_in, nights=2)}

        assert offers["DLX"].available == 2
        expected = pricing.quote(
            hotel=hotel,
            room_type=inventory,
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
            adults=inventory.base_occupancy,
        ).grand_total
        assert offers["DLX"].total_price == expected

    def test_a_held_room_drops_out(self, hotel, inventory, guest_factory):
        from services.booking import reservations

        check_in = timezone.localdate() + timedelta(days=1)
        reservations.create(
            hotel=hotel,
            guest=guest_factory(),
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
            room_type=inventory,
        )
        offers = {o.code: o for o in booking_agent.room_snapshot(hotel, check_in, nights=2)}
        assert offers["DLX"].available == 1


# ==============================================================================
# Validation — the model proposes, the server disposes
# ==============================================================================


class TestValidation:
    def test_a_hallucinated_room_code_is_dropped(self, convo, script):
        script.append(turn_json(booking={"room_code": "PRESIDENTIAL"}))
        turn = orchestrator.respond(convo, "আমার একটা রুম দরকার")

        assert "room_code" not in turn.booking or not turn.booking["room_code"]
        assert turn.booking["issues"]
        assert not Reservation.objects.exists()

    def test_a_sold_out_room_cannot_be_drafted(
        self, booking_convo, hotel, inventory, script, guest_factory
    ):
        from services.booking import reservations

        check_in = timezone.localdate() + timedelta(days=1)
        for _ in range(2):
            reservations.create(
                hotel=hotel,
                guest=guest_factory(),
                check_in=check_in,
                check_out=check_in + timedelta(days=2),
                room_type=inventory,
            )

        script.append(turn_json(ready_to_confirm=True))
        turn = orchestrator.respond(booking_convo, "ডিলাক্স রুম বুক করুন")

        assert turn.reservation_code == ""
        assert not Reservation.objects.filter(source=BookingSource.KIOSK).exists()
        assert turn.booking["issues"]

    def test_too_many_guests_for_the_room(self, booking_convo, script):
        script.append(turn_json(booking={"room_code": "STD", "adults": 4}))
        turn = orchestrator.respond(booking_convo, "চারজনের জন্য রুম চাই")

        assert not turn.booking.get("room_code")
        assert any("Standard Single" in issue for issue in turn.booking["issues"])

    def test_a_date_in_the_past_is_refused(self, booking_convo, script):
        stale = (timezone.localdate() - timedelta(days=3)).isoformat()
        script.append(turn_json(booking={"check_in": stale}, ready_to_confirm=True))
        turn = orchestrator.respond(booking_convo, "রুম দরকার")

        assert not turn.booking.get("check_in")
        assert turn.reservation_code == ""

    def test_nights_are_capped(self, booking_convo, script):
        script.append(turn_json(booking={"nights": 400}))
        turn = orchestrator.respond(booking_convo, "অনেক দিন থাকব")
        assert turn.booking["nights"] == booking_agent.MAX_NIGHTS

    def test_the_price_is_never_the_model_s(self, convo, hotel, inventory, script):
        """The model is not even asked for a price — the draft's total is
        recomputed from the pricing service every turn."""
        from services.rooms import pricing

        script.append(turn_json())
        turn = orchestrator.respond(convo, "রুম বুক করব")

        check_in = timezone.localdate() + timedelta(days=1)
        expected = pricing.quote(
            hotel=hotel,
            room_type=inventory,
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
            adults=2,
        ).grand_total
        assert turn.booking["total"] == str(expected)


class TestDraftMemory:
    def test_a_field_the_model_forgets_is_not_erased(self, convo, script):
        """The guest said it once. A model lapse must not make the kiosk ask
        again."""
        script.append(turn_json(booking={"guest_name": "", "guest_phone": ""}))
        orchestrator.respond(convo, "রুম দরকার")

        script.append(
            json.dumps(
                {
                    "reply": "ধন্যবাদ।",
                    # Model returns only what it just heard, dropping the rest.
                    "booking": {"guest_name": "Rina Haque"},
                    "needs_more_info": True,
                    "ready_to_confirm": False,
                }
            )
        )
        turn = orchestrator.respond(convo, "আমার নাম রিনা হক")

        assert turn.booking["check_in"] == TOMORROW
        assert turn.booking["nights"] == 2
        assert turn.booking["room_code"] == "DLX"
        assert turn.booking["guest_name"] == "Rina Haque"

    def test_the_draft_survives_on_the_server_not_the_browser(self, convo, script):
        script.append(turn_json(booking={"guest_phone": ""}))
        orchestrator.respond(convo, "রুম দরকার")

        convo.refresh_from_db()
        assert convo.mode == ConversationMode.BOOKING
        assert convo.booking_draft["room_code"] == "DLX"

    def test_bengali_digits_in_a_phone_number_are_normalised(self, convo, script):
        script.append(turn_json(booking={"guest_phone": "০১৭১১০০০০০০"}))
        turn = orchestrator.respond(convo, "রুম দরকার")
        assert turn.booking["guest_phone"] == "01711000000"


# ==============================================================================
# Confirmation
# ==============================================================================


class TestConfirmation:
    def test_confirming_writes_a_real_reservation(self, booking_convo, hotel, script):
        script.append(turn_json(ready_to_confirm=True))
        turn = orchestrator.respond(booking_convo, "হ্যাঁ, কনফার্ম করুন")

        reservation = Reservation.objects.get(code=turn.reservation_code)
        assert reservation.source == BookingSource.KIOSK
        assert reservation.conversation_id == booking_convo.pk
        assert reservation.check_in == timezone.localdate() + timedelta(days=1)
        assert reservation.check_out == timezone.localdate() + timedelta(days=3)
        assert reservation.guest.phone == "01711000000"
        # A real hold, not a note: a room is allocated and blocking inventory.
        assert reservation.allocations.filter(blocks_inventory=True).exists()

    def test_the_guest_is_told_the_reference(self, booking_convo, script):
        script.append(turn_json(ready_to_confirm=True))
        turn = orchestrator.respond(booking_convo, "কনফার্ম")
        assert turn.reservation_code in turn.reply

    def test_the_booking_total_matches_the_folio(self, booking_convo, script):
        """What the kiosk quoted and what the desk will charge are the same
        number, because both come from ``services.rooms.pricing``."""
        script.append(turn_json(ready_to_confirm=True))
        turn = orchestrator.respond(booking_convo, "কনফার্ম করুন")

        reservation = Reservation.objects.get(code=turn.reservation_code)
        assert str(reservation.grand_total) == turn.booking["total"]

    def test_the_conversation_returns_to_normal_afterwards(self, booking_convo, script):
        script.append(turn_json(ready_to_confirm=True))
        orchestrator.respond(booking_convo, "কনফার্ম")

        booking_convo.refresh_from_db()
        assert booking_convo.mode == ConversationMode.CHAT
        assert booking_convo.booking_draft == {}

    def test_a_returning_guest_is_not_duplicated(self, booking_convo, hotel, script):
        from apps.guests.models import Guest

        Guest.all_objects.create(
            tenant=hotel, first_name="Rina", last_name="Haque", phone="01711000000"
        )
        script.append(turn_json(ready_to_confirm=True))
        orchestrator.respond(booking_convo, "কনফার্ম")

        assert Guest.all_objects.filter(tenant=hotel, phone="01711000000").count() == 1

    def test_confirm_is_refused_while_anything_is_missing(self, booking_convo, script):
        """The model claiming the guest agreed does not make it so."""
        script.append(turn_json(booking={"guest_phone": ""}, ready_to_confirm=True))
        turn = orchestrator.respond(booking_convo, "কনফার্ম করুন")

        assert turn.reservation_code == ""
        assert not Reservation.objects.exists()
        assert turn.booking["complete"] is False

    def test_losing_the_race_for_a_room_asks_again_instead_of_crashing(
        self, booking_convo, script, monkeypatch
    ):
        """Somebody at the desk took the last room between quote and commit."""
        from services.booking import reservations

        def taken(*args, **kwargs):
            raise Conflict("Room 101 was taken while this booking was being made.")

        monkeypatch.setattr(reservations, "create", taken)
        script.append(turn_json(ready_to_confirm=True))
        turn = orchestrator.respond(booking_convo, "কনফার্ম")

        assert turn.reservation_code == ""
        assert turn.reply == booking_agent.say("taken", booking_convo.language)
        booking_convo.refresh_from_db()
        # Everything the guest said is kept; only the lost room is re-asked.
        assert booking_convo.booking_draft["guest_name"] == "Rina Haque"
        assert "room_code" not in booking_convo.booking_draft


# ==============================================================================
# Leaving, and failure
# ==============================================================================


class TestExit:
    def test_backing_out_clears_the_draft_immediately(self, convo, script):
        script.append(turn_json())
        orchestrator.respond(convo, "রুম দরকার")

        turn = orchestrator.respond(convo, "থাক, লাগবে না")

        convo.refresh_from_db()
        assert convo.mode == ConversationMode.CHAT
        assert convo.booking_draft == {}
        assert turn.handoff is False

    def test_the_model_declaring_a_cancellation_is_honoured(self, booking_convo, script):
        script.append(turn_json(cancelled=True, reply="ঠিক আছে, বাতিল করলাম।"))
        orchestrator.respond(booking_convo, "রুম দরকার")

        booking_convo.refresh_from_db()
        assert booking_convo.mode == ConversationMode.CHAT


class TestKioskCard:
    def test_the_booking_card_ships_hidden(self, client, hotel):
        """It must exist for the JS to fill, and must not be on screen before
        there is anything to show."""
        response = client.get(f"/reception/kiosk/?hotel={hotel.code}")
        body = response.content.decode()

        assert body.count('id="kiosk-booking"') == 1
        card = body.split('id="kiosk-booking"')[0].rsplit("<div", 1)[1]
        assert "d-none" in card + body.split('id="kiosk-booking"')[1][:80]
        for anchor in ("kiosk-booking-rows", "kiosk-booking-state", "kiosk-booking-note"):
            assert f'id="{anchor}"' in body


class TestRoomGallery:
    """The guest sees the room they are agreeing to.

    Same rule as every other number on this screen: the pictures are chosen by
    the server from the priced snapshot, so the kiosk cannot show a room type the
    hotel does not sell, and cannot show the wrong one when the guest has picked.
    """

    @pytest.fixture
    def photos(self, hotel, inventory):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.rooms.models import RoomTypePhoto

        # Out of order on purpose: sort_order, not insertion, decides the lead.
        for name, caption, order in (
            ("balcony.png", "The balcony", 2),
            ("bed.png", "Sea view", 1),
        ):
            RoomTypePhoto.all_objects.create(
                tenant=hotel,
                room_type=inventory,
                image=SimpleUploadedFile(name, PNG_1PX, content_type="image/png"),
                caption=caption,
                sort_order=order,
            )
        return inventory

    def test_every_room_on_offer_is_pictured_until_one_is_chosen(self, convo, script):
        script.append(turn_json(booking={"room_code": ""}))
        turn = orchestrator.respond(convo, "রুম দরকার")

        codes = {card["code"] for card in turn.booking["gallery"]}
        assert codes == {"DLX", "STD"}
        assert not any(card["chosen"] for card in turn.booking["gallery"])

    def test_once_chosen_only_that_room_is_shown(self, convo, script):
        script.append(turn_json())
        turn = orchestrator.respond(convo, "ডিলাক্স রুম চাই")

        gallery = turn.booking["gallery"]
        assert [card["code"] for card in gallery] == ["DLX"]
        assert gallery[0]["chosen"] is True
        assert gallery[0]["name"] == "Deluxe King"
        assert gallery[0]["view"] == "sea"
        assert gallery[0]["sleeps"] == 3

    def test_the_photos_come_through_in_order_with_their_captions(self, convo, script, photos):
        script.append(turn_json())
        turn = orchestrator.respond(convo, "ডিলাক্স রুম চাই")

        card = turn.booking["gallery"][0]
        assert [photo["caption"] for photo in card["photos"]] == ["Sea view", "The balcony"]
        assert all(photo["url"] for photo in card["photos"])

    def test_a_room_type_with_no_upload_still_gets_a_card(self, convo, script, photos):
        """Empty photos, never a stand-in image: a stock bedroom shown as this
        hotel's room is a picture of a room the guest will not be given."""
        script.append(turn_json(booking={"room_code": ""}))
        turn = orchestrator.respond(convo, "রুম দরকার")

        cards = {card["code"]: card for card in turn.booking["gallery"]}
        assert cards["STD"]["photos"] == []
        assert cards["DLX"]["photos"]

    def test_the_gallery_does_not_eat_the_room_count(self, convo, script):
        """``rooms`` in the draft means "how many rooms" and the booking card
        reads it. The gallery lives under its own key for that reason."""
        script.append(turn_json(booking={"rooms": 2}))
        turn = orchestrator.respond(convo, "দুইটা রুম দরকার")

        assert turn.booking["rooms"] == 2
        assert isinstance(turn.booking["gallery"], list)

    def test_a_photo_flood_is_capped_per_room(self, hotel, inventory, photos):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.rooms.models import RoomTypePhoto
        from services.rooms import media

        for index in range(4):
            RoomTypePhoto.all_objects.create(
                tenant=hotel,
                room_type=inventory,
                image=SimpleUploadedFile(f"extra{index}.png", PNG_1PX, content_type="image/png"),
                sort_order=10 + index,
            )

        assert len(media.gallery([inventory.pk], limit=2)[str(inventory.pk)]) == 2

    def test_the_gallery_ships_hidden_and_empty(self, client, hotel):
        response = client.get(f"/reception/kiosk/?hotel={hotel.code}")
        body = response.content.decode()

        assert body.count('id="kiosk-rooms"') == 1
        assert "is-hidden" in body.split('id="kiosk-rooms"')[0].rsplit("<aside", 1)[1]
        for anchor in ("kiosk-rooms-title", "kiosk-rooms-list"):
            assert f'id="{anchor}"' in body

    def test_the_headings_are_in_the_guest_s_language(self, client, hotel):
        """The gallery's own labels go down in the JSON blob with the rest of the
        guest-facing copy. English hardcoded in kiosk.js is how a Bangla kiosk
        ends up captioned "Rooms available"."""
        hotel.kiosk_language = "bn"
        hotel.save(update_fields=["kiosk_language"])

        body = client.get(f"/reception/kiosk/?hotel={hotel.code}").content.decode()
        assert "আপনার রুম" in body
        assert "ছবি শিগগিরই আসছে" in body


class TestFailure:
    def test_unreadable_model_output_falls_back_to_answering_the_question(self, convo, script):
        """One bad turn must not strand a guest at a dead terminal."""
        script.append("I'm terribly sorry, I can't do that.")
        turn = orchestrator.respond(convo, "আমার একটা রুম দরকার")

        assert turn.reply
        convo.refresh_from_db()
        assert convo.mode == ConversationMode.CHAT

    def test_fenced_json_is_still_read(self, convo, script):
        script.append("```json\n" + turn_json() + "\n```")
        turn = orchestrator.respond(convo, "রুম দরকার")
        assert turn.booking["room_code"] == "DLX"

    def test_prose_around_the_json_is_still_read(self, convo, script):
        script.append("Sure! " + turn_json() + " Hope that helps.")
        turn = orchestrator.respond(convo, "রুম দরকার")
        assert turn.booking["room_code"] == "DLX"

    def test_no_model_means_no_booking_mode(self, convo, settings):
        """Taking a booking with a keyword matcher would be worse than not
        taking one. The offline answerer still describes the rooms."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "আমার একটা রুম দরকার")

        convo.refresh_from_db()
        assert convo.mode == ConversationMode.CHAT
        assert turn.ai_used is False
        assert turn.citations


class TestBookingModeStaysGrounded:
    """A guest mid-booking still asks ordinary questions, and the booking prompt
    used to carry only the room list — so the model invented the rest.

    Observed before the fix: a hotel in Dhaka placed "beside Beach Road", and a
    bill with 15% VAT added described as tax-inclusive. No citations, because
    there was nothing to cite.
    """

    def test_the_prompt_carries_the_hotel_facts(self, hotel, inventory):
        from services.reception import booking_agent

        hotel.address_line1 = "12 Gulshan Avenue"
        hotel.city = "Dhaka"
        hotel.save()

        prompt = booking_agent._system_prompt(hotel, "en", booking_agent.room_snapshot(hotel), {})

        assert "12 Gulshan Avenue" in prompt
        assert "VAT" in prompt
        assert "check-out" in prompt.lower()

    def test_it_is_told_not_to_guess_the_rest(self, hotel, inventory):
        from services.reception import booking_agent

        for language, needle in (("en", "Never guess an address"), ("bn", "অনুমান করে বলবে না")):
            prompt = booking_agent._system_prompt(
                hotel, language, booking_agent.room_snapshot(hotel), {}
            )
            assert needle in prompt, language

    def test_the_facts_are_in_the_guests_language(self, hotel, inventory):
        from services.reception import booking_agent

        prompt = booking_agent._system_prompt(hotel, "bn", booking_agent.room_snapshot(hotel), {})
        assert "ভ্যাট" in prompt
        assert "চেক-আউট" in prompt
