"""The public booking page: /book/?hotel=CODE

A guest with no account holds a real room against real availability at the real
price. Everything worth testing here is about that word "real":

  the price      comes from services.rooms.pricing, the same call the desk makes
  the stock      comes from services.booking.availability, and is re-checked at
                 the moment of writing, not when the page was drawn
  the money      is not taken (goal.txt D11) — the slip says where to pay
  the write      goes through services.booking.reservations, so the folio, the
                 allocation and the exclusion constraint all still apply
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import BookingSource, Reservation
from apps.core.context import set_request_context
from apps.rooms.models import RatePlan, Room, RoomType
from services.booking import online

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def inventory(hotel):
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


def stay_query(hotel, **overrides) -> str:
    params = {
        "hotel": hotel.code,
        "check_in": (timezone.localdate() + timedelta(days=3)).isoformat(),
        "nights": "2",
        "adults": "2",
        "rooms": "1",
    }
    params.update(overrides)
    return "?" + "&".join(f"{key}={value}" for key, value in params.items())


def page(client, hotel, **overrides) -> str:
    url = reverse("online_booking:book") + stay_query(hotel, **overrides)
    return client.get(url).content.decode()


class TestItIsPublic:
    def test_no_login_needed(self, client, hotel, inventory):
        """The people this page is for do not have accounts — the same reason the
        lobby kiosk has no login."""
        response = client.get(reverse("online_booking:book") + stay_query(hotel))
        assert response.status_code == 200

    def test_without_a_hotel_it_says_how_to_open_it(self, client, db):
        body = client.get(reverse("online_booking:book")).content.decode()
        assert "?hotel=HOTEL-CODE" in body

    def test_it_reaches_nothing_but_its_own_booking(self, client, hotel, inventory):
        """A public page on the same domain as a PMS: what it must not leak is
        anybody else's stay."""
        body = page(client, hotel)
        for leak in ("internal_notes", "audit", 'csrfmiddlewaretoken" value=""'):
            assert leak not in body


class TestSearch:
    def test_it_offers_what_is_actually_free(self, client, hotel, inventory):
        body = page(client, hotel)
        assert "Deluxe King" in body
        assert "2 left for these dates" in body

    def test_a_room_that_cannot_sleep_the_party_is_not_offered(self, client, hotel, inventory):
        """A room type that sleeps one is not an answer to a party of three, and
        offering it so the list looks fuller is how a family arrives to a bed they
        cannot all sleep in."""
        body = page(client, hotel, adults="3")

        assert "Deluxe King" in body
        assert "Standard Single" not in body

    def test_a_sold_out_type_disappears(self, client, hotel, inventory, guest_factory):
        from services.booking import reservations

        check_in = timezone.localdate() + timedelta(days=3)
        for _ in range(2):
            reservations.create(
                hotel=hotel,
                guest=guest_factory(),
                check_in=check_in,
                check_out=check_in + timedelta(days=2),
                room_type=inventory,
            )

        assert "Deluxe King" not in page(client, hotel)

    def test_the_past_is_refused_with_a_reason(self, client, hotel, inventory):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        body = page(client, hotel, check_in=yesterday)
        assert "already passed" in body

    def test_the_url_is_the_search(self, client, hotel, inventory):
        """GET, so a search is shareable and refreshable and back does what a guest
        expects. Nothing is held until the form is posted."""
        page(client, hotel)
        assert not Reservation.all_objects.exists()


class TestTheBill:
    def test_it_is_the_pricing_service_not_a_second_opinion(self, client, hotel, inventory):
        from services.rooms import pricing

        check_in = timezone.localdate() + timedelta(days=3)
        expected = pricing.quote(
            hotel=hotel,
            room_type=inventory,
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
            adults=2,
        )

        body = page(client, hotel, room="DLX")
        assert f"{expected.grand_total:.2f}" in body
        assert f"{expected.service_amount:.2f}" in body
        assert f"{expected.tax_amount:.2f}" in body

    def test_it_is_itemised_night_by_night(self, client, hotel, inventory):
        """A Friday costs more than a Tuesday and a season boundary can fall
        mid-stay. A guest quoted one number and billed another will notice."""
        body = page(client, hotel, room="DLX")
        assert body.count("online-bill__night") >= 4  # two nights, dt + dd each

    def test_nothing_is_billed_until_a_room_is_chosen(self, client, hotel, inventory):
        body = page(client, hotel)
        assert "Pick a room and the bill appears here" in body


class TestBooking:
    def post(self, client, hotel, **overrides):
        data = {
            "hotel": hotel.code,
            "check_in": (timezone.localdate() + timedelta(days=3)).isoformat(),
            "nights": "2",
            "adults": "2",
            "rooms": "1",
            "room": "DLX",
            "guest_name": "Rina Haque",
            "guest_phone": "01711000000",
        }
        data.update(overrides)
        # ?hotel= in the query, like the form's action: the tenant middleware
        # reads the query string and the header, never the body.
        url = reverse("online_booking:book") + f"?hotel={hotel.code}"
        return client.post(url, data)

    def test_it_writes_a_real_reservation_through_the_service(self, client, hotel, inventory):
        response = self.post(client, hotel)
        assert response.status_code == 302

        reservation = Reservation.all_objects.get()
        assert reservation.source == BookingSource.WEBSITE
        assert reservation.check_in == timezone.localdate() + timedelta(days=3)
        assert reservation.nights == 2
        assert reservation.guest.phone == "01711000000"
        # A real hold, not a note: the room is allocated and blocking inventory.
        assert reservation.allocations.filter(blocks_inventory=True).exists()

    def test_it_redirects_so_a_refresh_does_not_book_twice(self, client, hotel, inventory):
        """The most expensive double-submit in a hotel."""
        response = self.post(client, hotel)
        client.get(response["Location"])
        client.get(response["Location"])

        assert Reservation.all_objects.count() == 1

    def test_the_slip_carries_the_reference_and_the_amount(self, client, hotel, inventory):
        response = self.post(client, hotel)
        body = client.get(response["Location"]).content.decode()

        reservation = Reservation.all_objects.get()
        assert reservation.code in body
        assert f"{reservation.grand_total:.2f}" in body
        assert "Show this at reception" in body

    def test_no_money_moves_and_no_card_is_asked_for(self, client, hotel, inventory):
        """goal.txt D11. An unattended page has no operator standing behind it and
        this one has no card reader."""
        response = self.post(client, hotel)
        body = client.get(response["Location"]).content.decode()

        for field in ('name="card', 'autocomplete="cc-', "cvv", "card_number"):
            assert field not in body, field

        from apps.billing.models import Payment

        assert not Payment.all_objects.exists()

    def test_the_terms_shown_are_the_property_s_own(self, client, hotel, inventory):
        """Every sentence about money on this page comes from the Hotel row.

        It used to be two hardcoded English sentences — "Nothing has been charged.
        Payment is taken at the desk" — which described the default and misdescribed
        any property that asks for an advance. Worse, they were the only place the
        page said anything about paying, so a guest who asked the assistant instead
        got an escalation to a member of staff who is not there.
        """
        from services.billing import payment_policy

        response = self.post(client, hotel)
        body = client.get(response["Location"]).content.decode()

        for line in payment_policy.lines(hotel, "en"):
            assert line in body, line
        # And the one thing a booking page must always say about cards.
        assert "never asks for card details" in body

    def test_the_payment_question_is_answerable_with_no_model_at_all(self, hotel):
        """"আমার বিল কি সেখানে গিয়ে দেবো?" — the question that started this.

        It has to be answerable on the offline path too, not only by the LLM: no key,
        no budget or no internet is a Tuesday in Cox's Bazar (goal.txt D12), and a
        guest asking how to pay is not a question worth a staff call in any weather.
        """
        from services.reception import context as ctx
        from services.reception import fallback

        facts = ctx.retrieve(hotel, "", "bn")
        assert any(fact.topic == "payment" for fact in facts)

        answer = fallback.answer(facts, "আমার বিল কি আমি সেখানে গিয়ে দেবো?", "bn")
        assert answer is not None
        assert "রিসেপশনে" in answer.text
        # Sourced, like every offline answer — the citation is what separates it from
        # a guess.
        assert answer.citations
        assert answer.citations[0]["source"] == "পেমেন্ট নীতি"

    def test_asking_about_money_is_not_answered_with_the_currency(self, hotel):
        """"pay" used to hit the currency rule, so "can I pay there?" was answered
        with "prices are quoted in BDT" — true, and not the question."""
        from services.reception import context as ctx
        from services.reception import fallback

        facts = ctx.retrieve(hotel, "", "en")
        answer = fallback.answer(facts, "How do I pay?", "en")
        assert answer is not None
        assert "settled at the reception desk" in answer.text

    def test_an_advance_is_only_published_with_somewhere_to_send_it(self, hotel):
        """A property that ticks "advance" and leaves the number blank must not have
        the assistant demanding money with no way to send it — that reads as a scam,
        and it is the shape a real misconfiguration takes."""
        from apps.tenants.models import AdvanceWallet, PaymentTiming
        from services.billing import payment_policy

        hotel.payment_timing = PaymentTiming.ADVANCE
        hotel.advance_wallet = AdvanceWallet.BKASH
        hotel.advance_wallet_number = ""
        hotel.save(update_fields=["payment_timing", "advance_wallet", "advance_wallet_number"])
        assert payment_policy.for_hotel(hotel).advance_required is False

        hotel.advance_wallet_number = "01711000000"
        hotel.save(update_fields=["advance_wallet_number"])
        policy = payment_policy.for_hotel(hotel)
        assert policy.advance_required is True
        assert "01711000000" in payment_policy.summary(hotel, "en")

    def test_a_returning_guest_is_not_duplicated(self, client, hotel, inventory):
        from apps.guests.models import Guest

        Guest.all_objects.create(
            tenant=hotel, first_name="Rina", last_name="Haque", phone="01711000000"
        )
        self.post(client, hotel)

        assert Guest.all_objects.filter(tenant=hotel, phone="01711000000").count() == 1

    def test_a_stale_tab_cannot_book_a_room_that_has_gone(
        self, client, hotel, inventory, guest_factory
    ):
        """The guest read the price, went for lunch, and the desk sold the last
        one. The offer is looked up again inside the write for exactly this."""
        from services.booking import reservations

        check_in = timezone.localdate() + timedelta(days=3)
        for _ in range(2):
            reservations.create(
                hotel=hotel,
                guest=guest_factory(),
                check_in=check_in,
                check_out=check_in + timedelta(days=2),
                room_type=inventory,
            )

        body = self.post(client, hotel).content.decode()

        assert "no longer available" in body
        assert Reservation.all_objects.filter(source=BookingSource.WEBSITE).count() == 0

    def test_a_nameless_booking_is_refused(self, client, hotel, inventory):
        body = self.post(client, hotel, guest_name=" ").content.decode()

        assert "name the booking is for" in body
        assert not Reservation.all_objects.exists()

    def test_bengali_digits_in_a_number_are_normalised(self, client, hotel, inventory):
        """Typed on a Bangla keypad, dialled from a desk phone."""
        self.post(client, hotel, guest_phone="০১৭১১০০০০০০")
        assert Reservation.all_objects.get().guest.phone == "01711000000"


class TestTenantIsolation:
    def test_a_reference_from_another_hotel_shows_nothing(
        self, client, hotel, other_hotel, inventory
    ):
        """The slip is looked up by reference within one tenant. A code guessed
        from another property must not print somebody else's stay."""
        set_request_context(tenant_id=str(other_hotel.pk))
        room_type = RoomType.all_objects.create(
            tenant=other_hotel, code="OTH", name="Other", base_rate=Decimal("1000")
        )
        Room.all_objects.create(tenant=other_hotel, number="900", room_type=room_type, floor=9)
        from apps.guests.models import Guest
        from services.booking import reservations

        guest = Guest.objects.create(tenant=other_hotel, first_name="Someone", phone="01799999999")
        theirs = reservations.create(
            hotel=other_hotel,
            guest=guest,
            check_in=timezone.localdate() + timedelta(days=3),
            check_out=timezone.localdate() + timedelta(days=4),
            room_type=room_type,
        )

        set_request_context(tenant_id=str(hotel.pk))
        body = page(client, hotel, ref=theirs.code)

        assert theirs.code not in body
        assert "Your reference appears here" in body


class TestItIsInTheMenu:
    def test_staff_can_reach_it_from_the_sidebar(self):
        """In QUICK_ACTIONS, not the module list. Every NAVIGATION item is a
        permission-gated screen — the navigation tests assert a role without the
        permission gets a 403 at its URL — and this page answers 200 to anyone by
        design, because the guests it is for have no accounts."""
        from apps.core.navigation import NAVIGATION, QUICK_ACTIONS

        item = next(entry for entry in QUICK_ACTIONS if entry.key == "online_booking")
        assert item.url_name == "online_booking:book"
        assert item.ready is True
        assert reverse(item.url_name) == "/book/"
        assert "online_booking" not in {entry.key for entry in NAVIGATION}

    def test_it_is_visible_to_whoever_can_see_reservations(self):
        """Menu visibility still follows a permission even though the page does
        not: it is the desk's shortcut, not a public link on a staff screen."""
        from apps.core.navigation import QUICK_ACTIONS

        item = next(entry for entry in QUICK_ACTIONS if entry.key == "online_booking")
        assert item.permission == "core.access_reservations"


class TestTheServiceOwnsTheRules:
    def test_the_view_holds_no_business_logic(self):
        """Repo rule: policy lives in services/. A public page is the last place a
        price or an availability rule should be written down."""
        from pathlib import Path

        source = (Path(__file__).parents[2] / "apps" / "booking" / "public_views.py").read_text(
            encoding="utf-8"
        )

        for forbidden in ("pricing.quote", "availability.by_type", "Reservation.objects.create"):
            assert forbidden not in source, forbidden

    def test_the_ceilings_match_the_kiosk(self):
        """Two front doors to the same hotel should not disagree about how far
        ahead it takes bookings."""
        from services.reception import booking_agent

        assert online.MAX_NIGHTS == booking_agent.MAX_NIGHTS
        assert online.MAX_ADVANCE_DAYS == booking_agent.MAX_ADVANCE_DAYS
        assert online.MAX_ROOMS == booking_agent.MAX_ROOMS


class TestTheAssistantTakesTheBooking:
    """The page is the lobby terminal, on the web — not a form with a chat widget
    bolted to it. Same panel, same agent, same services."""

    def test_the_page_embeds_the_real_assistant(self, client, hotel, inventory):
        body = page(client, hotel)

        # The widget itself, with everything kiosk.js binds to.
        for anchor in ('id="kiosk"', 'id="kiosk-bubbles"', 'id="kiosk-mic"', 'id="kiosk-form"'):
            assert anchor in body, anchor
        # ...and the panel's own context, without which the rail never relabels.
        assert 'data-copy-all="' in body
        assert 'data-panels="' in body

    def test_it_announces_itself_as_the_website(self, client, hotel, inventory):
        """Not the staff console's "web" channel: a receptionist trying a question
        on their laptop and a guest booking from home are different conversations,
        and a manager asking what the website brought in needs them apart."""
        assert 'data-channel="website"' in page(client, hotel)

    def test_no_camera_and_no_device_pickers(self, client, hotel, inventory):
        """A web page asking to photograph somebody at home is not a flow anybody
        designed, and the camera/mic/speaker bar is a lobby rig's control: a guest
        on their own phone has one microphone and has already chosen it."""
        body = page(client, hotel)

        assert "enrol-stage" not in body
        assert "kiosk-enrol.js" not in body
        assert "kiosk-devices.js" not in body
        assert 'id="device-mic"' not in body

    def test_the_microphone_opens_itself(self, client, hotel, inventory):
        """The guest presses nothing. They opened a page whose content is a
        receptionist; having to find a button before speaking to it is a step
        nobody wants. The browser's own permission bubble still gates the
        microphone, and the property switch still turns it off."""
        assert 'data-hands-free="true"' in page(client, hotel)

    def test_a_property_can_still_turn_it_off(self, client, hotel, inventory):
        hotel.kiosk_hands_free = False
        hotel.save(update_fields=["kiosk_hands_free"])
        assert 'data-hands-free="false"' in page(client, hotel)

    def test_nobody_is_promised_a_human_here(self, client, hotel, inventory):
        """No staff member is watching this conversation, so nothing may offer one.

        A guest asked whether they could settle the bill on arrival and was told
        "I am connecting a human staff member" — on a page where no such person
        exists, with a Handoff row queued that nobody would ever claim. Three things
        stop that now: the button is not drawn, the prompt forbids the sentence, and
        the escalation path answers instead of queueing.
        """
        body = page(client, hotel)

        assert 'id="kiosk-human"' not in body
        assert 'data-staffed="false"' in body

    def test_the_escalation_path_answers_instead_of_queueing(self, client, hotel, inventory):
        """Whatever the reason — a blocked topic, a guest asking for a person, an
        unanswerable question — a website conversation gets an answer and a next
        question, not a queue item."""
        from apps.reception.models import Channel, Handoff, MessageRole
        from services.reception import guidance, orchestrator

        conversation = orchestrator.start(hotel=hotel, channel=Channel.WEBSITE)
        # A guest has to have said something before the assistant carries on from it.
        orchestrator._record(conversation, MessageRole.GUEST, "একটা রুম লাগবে")

        turn = orchestrator.request_human(conversation, detail="wants a person")

        assert turn.handoff is False
        assert not Handoff.objects.filter(conversation=conversation).exists()
        assert conversation.status != "handoff"
        # Says what is true, gives a way to reach a person that does not depend on
        # anybody watching a queue, and then keeps the booking moving.
        assert "no member of staff in this chat" in turn.reply
        assert hotel.phone in turn.reply
        assert turn.reply.endswith(guidance.next_question(conversation))

    def test_a_promise_of_a_person_never_reaches_the_guest(self, hotel):
        """The exact failure from production, as a test.

        A model answered "আমি এই বিষয়টি নিশ্চিত করতে পারছি না। আমি একজন মানব স্টাফ
        সদস্যকে যুক্ত করছি [1]" — cited, so it scored 0.8, so nothing was queued and
        nothing was replaced. The guest was promised a person no one had been told
        about, on a page where no person exists. Sourced or not, that sentence does
        not go out from a self-serve channel.
        """
        from apps.reception.models import Channel, Handoff, MessageRole
        from services.reception import guardrails, orchestrator

        promise = "আমি এই বিষয়টি নিশ্চিত করতে পারছি না। আমি একজন মানব স্টাফ সদস্যকে যুক্ত করছি [1]।"
        assert guardrails.promises_a_human(promise) is True
        # ...and it is NOT a non-answer by the old pattern, which is why it got through.
        assert guardrails.NON_ANSWER.search(promise) is None

        conversation = orchestrator.start(hotel=hotel, channel=Channel.WEBSITE)
        orchestrator._record(conversation, MessageRole.GUEST, "আমার বিল কি সেখানে গিয়ে দেবো?")
        turn = orchestrator._self_serve(conversation, "low_confidence", detail=promise)

        assert "স্টাফ" not in turn.reply or "নেই" in turn.reply
        assert not Handoff.objects.filter(conversation=conversation).exists()
        # The discarded wording is never written to the transcript: the next turn's
        # history is built from it, so a false promise left there gets repeated.
        assert not conversation.messages.filter(content__contains="যুক্ত করছি").exists()

    def test_the_lobby_still_calls_somebody(self, hotel):
        """The change is scoped to the channel with nobody behind it. A terminal in a
        lobby has a desk ten metres away and a queue that lights up on it."""
        from apps.reception.models import Channel, Handoff
        from services.reception import orchestrator

        conversation = orchestrator.start(hotel=hotel, channel=Channel.KIOSK)
        turn = orchestrator.request_human(conversation)

        assert turn.handoff is True
        assert Handoff.objects.filter(conversation=conversation).exists()

    def test_a_quiet_guest_is_asked_the_next_question(self, client, hotel, inventory):
        """The page carries a silence timer, and what it fires is deterministic: the
        next unanswered field of the booking, asked in the guest's language, with no
        model call at all."""
        from apps.reception.models import Channel, MessageRole
        from services.reception import guidance, orchestrator

        assert 'data-nudge-after="45"' in page(client, hotel)

        conversation = orchestrator.start(hotel=hotel, channel=Channel.WEBSITE)
        # Silence before the guest has said anything is not silence — the greeting is
        # still the newest thing on their screen.
        assert orchestrator.nudge(conversation) is None

        orchestrator._record(conversation, MessageRole.GUEST, "রুম বুক করব")
        conversation.booking_draft = {"check_in": "2099-01-01", "nights": 2, "room_code": "DLX"}
        conversation.save(update_fields=["booking_draft"])

        turn = orchestrator.nudge(conversation)
        assert turn is not None
        # Name first: it is the first thing the draft is missing. Dates, nights and
        # the room are all already answered and must not be asked for again.
        assert turn.reply == guidance._ASK["en"]["guest_name"]
        assert turn.ai_used is False
        # A question the guest did not ask for must not eat their turn budget.
        assert conversation.turn_count == 0

        # And it follows the guest's language, like every other word on the screen —
        # a Bangla conversation nudged in English is the same bug as an English
        # button on a Bangla page.
        conversation.language = "bn"
        conversation.save(update_fields=["language"])
        assert orchestrator.nudge(conversation).reply == guidance._ASK["bn"]["guest_name"]

    def test_the_silence_timer_is_off_on_the_staff_console(self, hotel):
        """A receptionist's own screen asking them questions while they work is an
        interruption, not service."""
        from apps.reception import panel

        assert panel.panel_context(hotel, lobby=False, channel="web")["kiosk"]["nudge_after"] == 0
        assert panel.panel_context(hotel, lobby=False, channel="web")["kiosk"]["staffed"] is True

    def test_the_widget_drops_its_own_title_bar(self, client, hotel, inventory):
        """The page has a header of its own. Two title bars over one conversation
        is the same 55px of nothing the terminal had."""
        assert "kiosk-stage__header" not in page(client, hotel)

    def test_the_rail_is_the_bill_and_the_slip(self, client, hotel, inventory):
        """A guest at home has no passport to scan into a rail and does have a
        total to check."""
        body = page(client, hotel)

        assert 'id="online-bill-card"' in body
        assert 'id="online-slip-card"' in body
        # The vision steps belong to a machine with a camera.
        assert 'data-panel="scan"' not in body
        assert 'data-panel="recognition"' not in body

    def test_both_rail_cards_start_hidden(self, client, hotel, inventory):
        """Nothing to bill and nothing to show until the assistant has settled a
        room and written a reservation."""
        body = page(client, hotel)
        for anchor in ('id="online-bill-card"', 'id="online-slip-card"'):
            card = body[: body.index(anchor)].rsplit("<div", 1)[1]
            assert "d-none" in card, anchor

    def test_the_rail_column_goes_away_while_it_is_empty(self):
        """...and while they are all hidden the column they sit in must go too. An
        empty 320px gutter beside the conversation is not neutral space: it is a
        column-shaped hole that reads as cards which failed to load, and it holds
        the conversation off the centre of the page for the first half of every
        booking. Driven off the rail's own contents so kiosk.js has nothing extra
        to remember when it unhides a card."""
        css = (Path(__file__).parents[2] / "static" / "css" / "kiosk.css").read_text(
            encoding="utf-8"
        )
        collapse = ".booking-mode .kiosk:not(:has(.kiosk-rail > :not(.d-none)))"
        assert f"{collapse} {{\n  grid-template-columns: minmax(0, 1fr);\n}}" in css
        assert f"{collapse} .kiosk-rail {{\n  display: none;\n}}" in css

    def test_the_bill_and_slip_read_the_validated_draft(self):
        """Not the assistant's sentence. It says what it likes; the server says what
        is true — and the slip only exists once `code` has been issued by the
        write."""
        source = (Path(__file__).parents[2] / "static" / "js" / "kiosk.js").read_text(
            encoding="utf-8"
        )
        block = source[source.index("const renderOnlineRail") : source.index("const renderBooking")]

        assert "booking.total && booking.room_code" in block
        assert "Boolean(booking && booking.code)" in block

    def test_an_assistant_booking_is_sourced_from_the_website(self, hotel, inventory):
        """It was hardcoded to the kiosk, which was true when the kiosk was the only
        place the agent lived."""
        from apps.reception.models import Channel
        from services.reception import booking_agent, orchestrator

        conversation = orchestrator.start(hotel=hotel, channel=Channel.WEBSITE)
        assert booking_agent._source_for(conversation) == BookingSource.WEBSITE

        kiosk_convo = orchestrator.start(hotel=hotel, channel=Channel.KIOSK)
        assert booking_agent._source_for(kiosk_convo) == BookingSource.KIOSK

    def test_one_panel_context_serves_all_three_pages(self):
        """The terminal, the console and this page render the same widget. A third
        copy of its context is a third place to forget data-panels."""
        from apps.reception import views

        assert callable(views.panel_context)
        source = (Path(__file__).parents[2] / "apps" / "booking" / "public_views.py").read_text(
            encoding="utf-8"
        )
        assert "panel_context(" in source


class TestTheFallbackStillWorks:
    """The assistant needs a model, a key and a budget. A hotel whose website stops
    selling rooms when a token runs out has bought a liability."""

    def test_the_form_is_still_there(self, client, hotel, inventory):
        body = page(client, hotel)
        assert 'class="online-fallback"' in body
        assert "Deluxe King" in body

    def test_it_opens_itself_when_the_assistant_cannot_answer(
        self, client, hotel, inventory, settings
    ):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        body = page(client, hotel)

        assert "The assistant is unavailable" in body
        assert 'class="online-fallback" open' in body

    def test_it_stays_folded_when_the_assistant_is_working(
        self, client, hotel, inventory, settings
    ):
        settings.AI = {**settings.AI, "KILL_SWITCH": False}
        body = page(client, hotel)
        # Offered, not pushed: the assistant is the front door.
        assert "Book without the assistant" in body
