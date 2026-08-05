"""Bangla reception: greeting, and answers pulled from the live database.

The point of these tests is that no number in an answer is ever written by a
language model. Prices come from ``services.rooms.pricing`` and availability
from ``services.booking.availability`` — the same code that prices and holds a
real booking — so what a guest is told at the kiosk and what they are charged
at the desk cannot drift apart.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.reception.models import Channel, GreetingStyle
from apps.rooms.models import RatePlan, Room, RoomType
from services.reception import context as ctx
from services.reception import fallback, guardrails, orchestrator

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def bangla_hotel(hotel):
    hotel.kiosk_language = "bn"
    hotel.kiosk_greeting_style = GreetingStyle.ISLAMIC
    hotel.currency = "BDT"
    hotel.phone = "+8801711000000"
    hotel.address_line1 = "12 Gulshan Avenue"
    hotel.save()
    return hotel


@pytest.fixture
def inventory(bangla_hotel):
    from apps.core.context import set_request_context

    set_request_context(tenant_id=str(bangla_hotel.pk))
    deluxe = RoomType.all_objects.create(
        tenant=bangla_hotel,
        code="DLX",
        name="Deluxe King",
        base_occupancy=2,
        max_occupancy=3,
        base_rate=Decimal("7200.00"),
        view="sea",
    )
    RatePlan.all_objects.create(
        tenant=bangla_hotel, code="BAR", name="Best Available", is_default=True
    )
    for number in ("101", "102"):
        Room.all_objects.create(tenant=bangla_hotel, number=number, room_type=deluxe, floor=1)
    return deluxe


class TestBanglaGreeting:
    def test_uses_the_requested_wording(self, bangla_hotel):
        text = orchestrator.greeting(bangla_hotel)
        assert "আসসালামু আলাইকুম" in text
        assert "স্বাগতম" in text
        assert "সাহায্য" in text
        assert bangla_hotel.name in text

    def test_conversation_inherits_the_hotel_language(self, bangla_hotel):
        conversation = orchestrator.start(hotel=bangla_hotel, channel=Channel.KIOSK)
        assert conversation.language == "bn"

    def test_english_hotel_still_greets_in_english(self, hotel):
        hotel.kiosk_language = "en"
        hotel.save()
        assert "Welcome" in orchestrator.greeting(hotel) or "welcome" in orchestrator.greeting(
            hotel
        )

    def test_returning_guest_is_welcomed_back_in_bangla(self, bangla_hotel):
        text = orchestrator.greeting(bangla_hotel, guest_name="রিনা")
        assert "রিনা" in text
        assert "আবার" in text


class TestLiveRoomSnapshot:
    def test_room_facts_carry_real_availability(self, bangla_hotel, inventory):
        facts = ctx.room_facts(bangla_hotel, "bn")
        text = " ".join(f.text for f in facts)
        assert "Deluxe King" in text
        assert "খালি আছে" in text

    def test_price_comes_from_the_pricing_service(self, bangla_hotel, inventory):
        """A quoted price must be the price the folio would charge."""
        from services.rooms import pricing

        today = timezone.localdate()
        expected = pricing.quote(
            hotel=bangla_hotel,
            room_type=inventory,
            check_in=today,
            check_out=today + timedelta(days=1),
            adults=inventory.base_occupancy,
        ).grand_total

        facts = ctx.room_facts(bangla_hotel, "bn")
        quoted = next(f for f in facts if f.topic == "room_type")
        assert f"{expected:,.0f}" in quoted.text

    def test_a_booked_room_drops_out_of_availability(self, bangla_hotel, inventory, guest_factory):
        from services.booking import reservations as booking

        today = timezone.localdate()
        booking.create(
            hotel=bangla_hotel,
            guest=guest_factory(),
            check_in=today,
            check_out=today + timedelta(days=1),
            room_type=inventory,
        )
        facts = ctx.room_facts(bangla_hotel, "bn")
        total = next(f for f in facts if f.topic == "availability")
        assert "1 টি" in total.text  # two rooms, one now taken

    def test_sold_out_is_said_plainly(self, bangla_hotel, inventory, guest_factory):
        from services.booking import reservations as booking

        today = timezone.localdate()
        for _ in range(2):
            booking.create(
                hotel=bangla_hotel,
                guest=guest_factory(),
                check_in=today,
                check_out=today + timedelta(days=1),
                room_type=inventory,
            )
        facts = ctx.room_facts(bangla_hotel, "bn")
        quoted = next(f for f in facts if f.topic == "room_type")
        assert "কোনোটি খালি নেই" in quoted.text


class TestBanglaAnswers:
    """The path that actually runs today: no LLM key, answers from the DB."""

    @pytest.fixture
    def convo(self, bangla_hotel, inventory):
        return orchestrator.start(hotel=bangla_hotel, channel=Channel.KIOSK)

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("আমার একটা রুম দরকার", "Deluxe King"),
            ("রুম আছে?", "খালি"),
            ("রুমের ভাড়া কত?", "BDT"),
            ("কি ধরনের রুম আছে?", "Deluxe King"),
            ("চেক আউট কখন?", "চেক-আউট"),
            ("চেক ইন কখন?", "চেক-ইন"),
            ("ভ্যাট কত?", "ভ্যাট"),
            ("ঠিকানা কোথায়?", "Gulshan"),
            ("ফোন নম্বর কত?", "8801711000000"),
        ],
    )
    def test_answers_bangla_questions_from_the_database(
        self, convo, bangla_hotel, question, expected, settings
    ):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}  # force the offline path
        turn = orchestrator.respond(convo, question)

        assert turn.handoff is False, turn.reply
        assert turn.ai_used is False
        assert expected in turn.reply
        assert turn.citations

    def test_english_question_still_works_on_a_bangla_kiosk(self, convo, settings):
        """A foreign guest should not have to know the kiosk was set to Bangla."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "do you have a room available?")
        assert turn.handoff is False
        assert turn.citations

    def test_bangla_greeting_is_recognised(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "আসসালামু আলাইকুম")
        assert turn.handoff is False
        assert "সাহায্য" in turn.reply

    def test_unknown_question_escalates_in_bangla(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "সিলেটে হেলিকপ্টার ভাড়া করে দিতে পারবেন?")
        assert turn.handoff is True
        assert "কর্মী" in turn.reply

    def test_asking_for_a_person_in_bangla_wins(self, convo, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        turn = orchestrator.respond(convo, "আমি একজন মানুষের সাথে কথা বলতে চাই")
        assert turn.handoff is True


class TestBanglaMatchingTraps:
    """Bangla has no regex word boundary, so short keywords match inside words."""

    @pytest.fixture
    def facts(self, bangla_hotel, inventory):
        return ctx.retrieve(bangla_hotel, "", "bn")

    @pytest.mark.parametrize(
        "question",
        [
            "সিলেটে হেলিকপ্টার ভাড়া করে দিতে পারবেন?",  # "কর" hides inside "করে"
            "আমার জন্য একটা কাজ করে দিন",
            "কিছু করার আছে?",
        ],
    )
    def test_the_word_for_do_is_not_read_as_the_word_for_tax(self, facts, question):
        answer = fallback.answer(facts, question, "bn")
        assert answer is None or "ভ্যাট" not in answer.text

    def test_a_real_tax_question_still_matches(self, facts):
        answer = fallback.answer(facts, "ভ্যাট কত শতাংশ?", "bn")
        assert answer is not None
        assert "ভ্যাট" in answer.text


class TestCitationParsing:
    """Models group citations. Missing that read as "unsourced" and escalated
    perfectly good answers to a human."""

    @pytest.fixture
    def facts(self, bangla_hotel, inventory):
        return ctx.retrieve(bangla_hotel, "", "bn")

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            ("Check-out is at noon [3].", {3}),
            ("We have the Standard and the Deluxe [9, 10].", {9, 10}),
            ("Rooms [9,10] and policy [3].", {3, 9, 10}),
            ("Two sources [1][2].", {1, 2}),
            ("No citation at all.", set()),
            # The live model writes Bengali numerals on a Bangla kiosk.
            ("চেক-আউট দুপুর ১২টা [৩]।", {3}),
            ("ভাড়া ৪,০৪৮ থেকে [৯, ১০, ১১]।", {9, 10, 11}),
        ],
    )
    def test_every_grouping_a_model_writes(self, answer, expected):
        assert ctx.cited_indexes(answer) == expected

    def test_grouped_citations_become_real_sources(self, facts):
        answer = "Two facts [1, 2] here."
        used = ctx.citations(facts, answer)
        assert {c["index"] for c in used} == {"1", "2"}


class TestBanglaGuardrails:
    """Guardrails written only in English are guardrails that do not exist on a
    Bangla kiosk."""

    @pytest.mark.parametrize(
        "reply",
        [
            "আমি এই বিষয়ে জানি না। একজন কর্মীকে ডেকে দিচ্ছি।",
            "দুঃখিত, এই তথ্য আমার জানা নেই।",
            "আমি এখনই আমাদের একজন মানব কর্মীকে আপনার সাথে যুক্ত করে দিচ্ছি।",
            "এ বিষয়ে আমি সাহায্য করতে পারছি না।",
        ],
    )
    def test_a_bangla_non_answer_triggers_a_real_handoff(self, reply):
        """The model promised a person; somebody must actually be called."""
        verdict = guardrails.check_outbound(reply)
        assert verdict.handoff is True

    def test_a_normal_bangla_answer_does_not(self):
        verdict = guardrails.check_outbound("চেক-আউটের সময় দুপুর ১২টা। [৩]")
        assert verdict.handoff is False

    @pytest.mark.parametrize(
        "text",
        [
            "আমি একজন মানুষের সাথে কথা বলতে চাই",
            "ম্যানেজারকে ডাকুন",
            "কারো সাথে কথা বলা যাবে?",
            "একজন কর্মীর সাথে কথা বলব",
        ],
    )
    def test_asking_for_a_person_in_bangla_is_recognised(self, bangla_hotel, text):
        conversation = orchestrator.start(hotel=bangla_hotel, channel=Channel.KIOSK)
        verdict = guardrails.check_inbound(conversation, text)
        assert verdict.handoff is True


class TestFallbackDirectly:
    def test_bangla_and_english_reach_the_same_topic(self, bangla_hotel, inventory):
        facts = ctx.retrieve(bangla_hotel, "", "bn")
        assert fallback.answer(facts, "রুম আছে?", "bn") is not None
        assert fallback.answer(facts, "any rooms free?", "bn") is not None

    def test_unavailable_notice_is_localised(self):
        assert "কর্মী" in fallback.unavailable_notice("bn")
        assert "staff" in fallback.unavailable_notice("en")
