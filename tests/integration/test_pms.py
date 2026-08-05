"""Core PMS: pricing, availability, the double-booking guarantee, and money.

The tests that matter here are the ones that stop a hotel losing money or
selling the same room twice. Everything else is convenience.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import ChargeType, FolioStatus, Invoice, PaymentMethod
from apps.booking.models import Reservation, ReservationRoom, ReservationStatus
from apps.core.context import set_request_context
from apps.core.exceptions import Conflict, ValidationError
from apps.guests.models import ConsentPurpose, DocumentType, Guest, GuestConsent, GuestDocument
from apps.rooms.models import RatePlan, Room, RoomRate, RoomStatus, RoomType
from services.analytics import kpi
from services.billing import folio as billing
from services.booking import availability
from services.booking import reservations as booking
from services.rooms import pricing

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

TODAY = timezone.localdate()
TOMORROW = TODAY + timedelta(days=1)


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def pms(hotel):
    set_request_context(tenant_id=str(hotel.pk))

    standard = RoomType.all_objects.create(
        tenant=hotel,
        code="STD",
        name="Standard",
        base_occupancy=2,
        max_occupancy=3,
        base_rate=Decimal("5000.00"),
        extra_person_rate=Decimal("1000.00"),
    )
    suite = RoomType.all_objects.create(
        tenant=hotel,
        code="STE",
        name="Suite",
        base_occupancy=2,
        max_occupancy=4,
        base_rate=Decimal("12000.00"),
    )
    plan = RatePlan.all_objects.create(
        tenant=hotel, code="BAR", name="Best Available", is_default=True
    )

    rooms = [
        Room.all_objects.create(tenant=hotel, number=f"10{i}", room_type=standard, floor=1)
        for i in range(1, 4)
    ]
    rooms.append(Room.all_objects.create(tenant=hotel, number="201", room_type=suite, floor=2))

    return {"hotel": hotel, "standard": standard, "suite": suite, "plan": plan, "rooms": rooms}


@pytest.fixture
def guest(hotel):
    return Guest.all_objects.create(
        tenant=hotel, first_name="Rina", last_name="Haque", phone="+8801700000000"
    )


# ==============================================================================
# Pricing
# ==============================================================================


class TestPricing:
    def test_prices_each_night_separately(self, pms):
        quote = pricing.quote(
            hotel=pms["hotel"],
            room_type=pms["standard"],
            check_in=TODAY,
            check_out=TODAY + timedelta(days=3),
            rate_plan=pms["plan"],
        )
        assert quote.nights == 3
        assert len(quote.per_night) == 3
        assert quote.room_total == Decimal("15000.00")

    def test_departure_day_is_not_charged(self, pms):
        quote = pricing.quote(
            hotel=pms["hotel"], room_type=pms["standard"], check_in=TODAY, check_out=TOMORROW
        )
        assert quote.nights == 1

    def test_seasonal_rate_beats_the_base_rate(self, pms):
        RoomRate.all_objects.create(
            tenant=pms["hotel"],
            room_type=pms["standard"],
            rate_plan=pms["plan"],
            valid_from=TODAY,
            valid_to=TODAY + timedelta(days=30),
            price=Decimal("8000.00"),
            label="Peak season",
        )
        amount, source = pricing.nightly_rate(pms["standard"], pms["plan"], TODAY)
        assert amount == Decimal("8000.00")
        assert "Peak" in source

    def test_higher_priority_rate_wins_an_overlap(self, pms):
        common = {
            "tenant": pms["hotel"],
            "room_type": pms["standard"],
            "rate_plan": pms["plan"],
            "valid_from": TODAY,
            "valid_to": TODAY + timedelta(days=30),
        }
        RoomRate.all_objects.create(**common, price=Decimal("8000.00"), priority=1, label="Season")
        RoomRate.all_objects.create(**common, price=Decimal("15000.00"), priority=9, label="Eid")
        amount, source = pricing.nightly_rate(pms["standard"], pms["plan"], TODAY)
        assert amount == Decimal("15000.00")
        assert "Eid" in source

    def test_weekday_mask_is_respected(self, pms):
        friday = TODAY + timedelta(days=(4 - TODAY.weekday()) % 7)
        RoomRate.all_objects.create(
            tenant=pms["hotel"],
            room_type=pms["standard"],
            rate_plan=pms["plan"],
            valid_from=TODAY,
            valid_to=TODAY + timedelta(days=30),
            price=Decimal("9000.00"),
            weekdays="4",
            label="Friday",
        )
        assert pricing.nightly_rate(pms["standard"], pms["plan"], friday)[0] == Decimal("9000.00")
        not_friday = friday + timedelta(days=1)
        assert pricing.nightly_rate(pms["standard"], pms["plan"], not_friday)[0] == Decimal(
            "5000.00"
        )

    def test_rate_plan_discount_applies_to_the_base(self, pms):
        plan = RatePlan.all_objects.create(
            tenant=pms["hotel"], code="NR", name="Non-refundable", discount_percent=Decimal("10")
        )
        amount, _ = pricing.nightly_rate(pms["standard"], plan, TODAY)
        assert amount == Decimal("4500.00")

    def test_extra_person_is_charged_per_night(self, pms):
        quote = pricing.quote(
            hotel=pms["hotel"],
            room_type=pms["standard"],
            check_in=TODAY,
            check_out=TODAY + timedelta(days=2),
            adults=3,
        )
        # One head over the standard occupancy of two, two nights.
        assert quote.extra_person_total == Decimal("2000.00")

    def test_vat_is_charged_on_the_service_inclusive_amount(self, pms):
        hotel = pms["hotel"]
        hotel.service_charge_rate = Decimal("10")
        hotel.tax_rate = Decimal("15")
        hotel.save()

        quote = pricing.quote(
            hotel=hotel, room_type=pms["standard"], check_in=TODAY, check_out=TOMORROW
        )
        assert quote.room_total == Decimal("5000.00")
        assert quote.service_amount == Decimal("500.00")
        assert quote.tax_amount == Decimal("825.00")  # 15% of 5500, not of 5000
        assert quote.grand_total == Decimal("6325.00")

    def test_rejects_a_reversed_range(self, pms):
        with pytest.raises(ValueError, match="after"):
            pricing.quote(
                hotel=pms["hotel"], room_type=pms["standard"], check_in=TOMORROW, check_out=TODAY
            )


# ==============================================================================
# The double-booking guarantee
# ==============================================================================


class TestDoubleBooking:
    def test_database_refuses_two_overlapping_stays_in_one_room(self, pms, guest):
        """The constraint, tested directly — not through the service that avoids it.

        This is the guarantee the whole booking flow rests on. If it ever stops
        holding, every other safeguard is decoration.
        """
        first = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TODAY + timedelta(days=3),
            room_type=pms["standard"],
        )
        room = first.allocations.first().room

        other = Reservation.all_objects.create(
            tenant=pms["hotel"],
            guest=guest,
            check_in=TODAY + timedelta(days=1),
            check_out=TODAY + timedelta(days=4),
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            ReservationRoom.all_objects.create(
                tenant=pms["hotel"],
                reservation=other,
                room_type=pms["standard"],
                room=room,
                stay=(TODAY + timedelta(days=1), TODAY + timedelta(days=4)),
            )

    def test_back_to_back_stays_are_allowed(self, pms, guest):
        """Departure day belongs to the next guest — not an overlap."""
        first = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        room = first.allocations.first().room

        second = Reservation.all_objects.create(
            tenant=pms["hotel"],
            guest=guest,
            check_in=TOMORROW,
            check_out=TOMORROW + timedelta(days=1),
        )
        allocation = ReservationRoom.all_objects.create(
            tenant=pms["hotel"],
            reservation=second,
            room_type=pms["standard"],
            room=room,
            stay=(TOMORROW, TOMORROW + timedelta(days=2)),
        )
        assert allocation.pk

    def test_cancelling_frees_the_room(self, pms, guest):
        first = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TODAY + timedelta(days=2),
            room_type=pms["standard"],
        )
        room = first.allocations.first().room
        before = availability.available_rooms(pms["hotel"], TODAY, TODAY + timedelta(days=2))
        assert room not in list(before)

        booking.cancel(first, reason="guest changed plans")

        after = availability.available_rooms(pms["hotel"], TODAY, TODAY + timedelta(days=2))
        assert room in list(after)

    def test_service_refuses_when_the_type_is_sold_out(self, pms, guest):
        for _ in range(3):  # three standard rooms exist
            booking.create(
                hotel=pms["hotel"],
                guest=guest,
                check_in=TODAY,
                check_out=TOMORROW,
                room_type=pms["standard"],
            )
        with pytest.raises(Conflict, match="free for those dates"):
            booking.create(
                hotel=pms["hotel"],
                guest=guest,
                check_in=TODAY,
                check_out=TOMORROW,
                room_type=pms["standard"],
            )


# ==============================================================================
# Availability
# ==============================================================================


class TestAvailability:
    def test_out_of_order_rooms_are_not_sellable(self, pms):
        pms["rooms"][0].set_status(RoomStatus.OUT_OF_ORDER)
        free = availability.available_rooms(
            pms["hotel"], TODAY, TOMORROW, room_type=pms["standard"]
        )
        assert pms["rooms"][0] not in list(free)
        assert len(list(free)) == 2

    def test_dirty_rooms_are_still_sellable(self, pms):
        """Housekeeping will reach the room before the guest does."""
        pms["rooms"][0].set_status(RoomStatus.VACANT_DIRTY)
        free = availability.available_rooms(
            pms["hotel"], TODAY, TOMORROW, room_type=pms["standard"]
        )
        assert pms["rooms"][0] in list(free)

    def test_by_type_excludes_types_too_small_for_the_party(self, pms):
        rows = availability.by_type(pms["hotel"], TODAY, TOMORROW, guests=4)
        codes = {row.room_type.code for row in rows}
        assert "STE" in codes
        assert "STD" not in codes  # sleeps 3

    def test_occupancy_counts_rooms_not_bookings(self, pms, guest):
        booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
            rooms=2,
            adults=2,
        )
        occ = availability.occupancy(pms["hotel"], TODAY)
        assert occ["occupied"] == 2


# ==============================================================================
# Reservation lifecycle
# ==============================================================================


class TestLifecycle:
    def test_create_opens_a_folio_and_snapshots_the_quote(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TODAY + timedelta(days=2),
            room_type=pms["standard"],
        )
        assert reservation.status == ReservationStatus.CONFIRMED
        assert reservation.room_total == Decimal("10000.00")
        assert reservation.grand_total > reservation.room_total
        assert reservation.folio.status == FolioStatus.OPEN
        assert reservation.code

    def test_check_in_occupies_the_room(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        booking.check_in(reservation)

        reservation.refresh_from_db()
        room = reservation.allocations.first().room
        room.refresh_from_db()
        assert reservation.status == ReservationStatus.CHECKED_IN
        assert room.status == RoomStatus.OCCUPIED
        assert reservation.events.filter(kind="check_in").exists()

    def test_check_out_frees_the_room_and_sends_it_to_housekeeping(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        booking.check_in(reservation)
        room = reservation.allocations.first().room

        invoice = booking.check_out(reservation, force=True)

        reservation.refresh_from_db()
        room.refresh_from_db()
        assert reservation.status == ReservationStatus.CHECKED_OUT
        assert room.status == RoomStatus.VACANT_DIRTY
        assert isinstance(invoice, Invoice)
        assert not reservation.allocations.filter(blocks_inventory=True).exists()

    def test_check_out_refuses_an_unpaid_balance_without_an_override(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        booking.check_in(reservation)
        billing.post_charge(
            reservation.folio,
            charge_type=ChargeType.ROOM,
            description="Night 1",
            amount=Decimal("5000"),
        )
        with pytest.raises(Conflict, match="outstanding"):
            booking.check_out(reservation)

    def test_check_out_updates_the_guest_profile(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        booking.check_in(reservation)
        billing.post_charge(
            reservation.folio,
            charge_type=ChargeType.ROOM,
            description="Night",
            amount=Decimal("5000"),
        )
        billing.post_payment(reservation.folio, method=PaymentMethod.CASH, amount=Decimal("5000"))
        booking.check_out(reservation)

        guest.refresh_from_db()
        assert guest.total_stays == 1
        assert guest.total_spend == Decimal("5000.00")
        assert guest.last_stay_at == TODAY

    def test_past_check_in_is_refused_unless_backfilling(self, pms, guest):
        yesterday = TODAY - timedelta(days=1)
        with pytest.raises(ValidationError, match="past"):
            booking.create(
                hotel=pms["hotel"],
                guest=guest,
                check_in=yesterday,
                check_out=TOMORROW,
                room_type=pms["standard"],
            )
        # Migration and seeding need it, and say so explicitly.
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=yesterday,
            check_out=TOMORROW,
            room_type=pms["standard"],
            allow_past=True,
        )
        assert reservation.check_in == yesterday

    def test_party_too_large_for_the_room_is_refused(self, pms, guest):
        with pytest.raises(ValidationError, match="sleeps"):
            booking.create(
                hotel=pms["hotel"],
                guest=guest,
                check_in=TODAY,
                check_out=TOMORROW,
                room_type=pms["standard"],
                adults=5,
            )

    def test_blacklisted_guest_is_refused(self, pms, guest):
        guest.is_blacklisted = True
        guest.save()
        with pytest.raises(Conflict, match="blacklisted"):
            booking.create(
                hotel=pms["hotel"],
                guest=guest,
                check_in=TODAY,
                check_out=TOMORROW,
                room_type=pms["standard"],
            )

    def test_confirmation_codes_are_unique_and_readable(self, pms, guest):
        codes = set()
        for _ in range(15):
            reservation = booking.create(
                hotel=pms["hotel"],
                guest=guest,
                check_in=TODAY,
                check_out=TOMORROW,
                room_type=pms["standard"],
                assign_rooms=False,
            )
            codes.add(reservation.code)
        assert len(codes) == 15
        # Read aloud over the phone: no 0/O or 1/I confusion.
        assert not set("".join(codes)) & set("01OIL")


# ==============================================================================
# Money
# ==============================================================================


class TestFolio:
    @pytest.fixture
    def folio(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        return billing.open_folio(reservation)

    def test_totals_are_derived_from_lines(self, folio):
        billing.post_charge(
            folio, charge_type=ChargeType.ROOM, description="Night", amount=Decimal("5000")
        )
        billing.post_charge(
            folio, charge_type=ChargeType.RESTAURANT, description="Dinner", amount=Decimal("1500")
        )
        folio.refresh_from_db()
        assert folio.charges_total == Decimal("6500.00")
        assert folio.balance == Decimal("6500.00")

    def test_payment_reduces_the_balance(self, folio):
        billing.post_charge(
            folio, charge_type=ChargeType.ROOM, description="Night", amount=Decimal("5000")
        )
        billing.post_payment(folio, method=PaymentMethod.CASH, amount=Decimal("2000"))
        folio.refresh_from_db()
        assert folio.payments_total == Decimal("2000.00")
        assert folio.balance == Decimal("3000.00")

    def test_voiding_reverses_rather_than_deletes(self, folio):
        line = billing.post_charge(
            folio, charge_type=ChargeType.MINIBAR, description="Minibar", amount=Decimal("800")
        )
        billing.void_line(line, reason="charged in error")

        folio.refresh_from_db()
        line.refresh_from_db()
        assert folio.charges_total == Decimal("0.00")
        assert line.is_voided
        # The original is still there to be read.
        assert folio.lines.count() == 2

    def test_a_repeated_payment_is_not_taken_twice(self, folio):
        """goal.txt D16 — a retried checkout must not charge the guest again."""
        billing.post_charge(
            folio, charge_type=ChargeType.ROOM, description="Night", amount=Decimal("5000")
        )
        first = billing.post_payment(
            folio, method=PaymentMethod.CASH, amount=Decimal("5000"), idempotency_key="checkout-1"
        )
        second = billing.post_payment(
            folio, method=PaymentMethod.CASH, amount=Decimal("5000"), idempotency_key="checkout-1"
        )
        assert first.pk == second.pk
        folio.refresh_from_db()
        assert folio.payments_total == Decimal("5000.00")

    def test_charges_are_refused_on_a_closed_folio(self, folio):
        billing.post_charge(
            folio, charge_type=ChargeType.ROOM, description="Night", amount=Decimal("100")
        )
        billing.post_payment(folio, method=PaymentMethod.CASH, amount=Decimal("100"))
        billing.settle(folio)

        with pytest.raises(Conflict, match="closed"):
            billing.post_charge(
                folio, charge_type=ChargeType.MINIBAR, description="Late", amount=Decimal("50")
            )

    def test_settle_refuses_while_money_is_owed(self, folio):
        billing.post_charge(
            folio, charge_type=ChargeType.ROOM, description="Night", amount=Decimal("5000")
        )
        with pytest.raises(Conflict, match="outstanding"):
            billing.settle(folio)

    def test_invoice_freezes_the_lines(self, folio):
        billing.post_charge(
            folio, charge_type=ChargeType.ROOM, description="Night", amount=Decimal("5000")
        )
        billing.post_charge(
            folio, charge_type=ChargeType.TAX, description="VAT", amount=Decimal("750")
        )
        invoice = billing.issue_invoice(folio)

        assert invoice.total == Decimal("5750.00")
        assert invoice.tax_amount == Decimal("750.00")
        assert invoice.subtotal == Decimal("5000.00")
        assert len(invoice.snapshot["lines"]) == 2
        assert invoice.snapshot["hotel"]["name"] == folio.tenant.name

    def test_invoice_numbers_are_sequential(self, pms, guest):
        numbers = []
        for _ in range(3):
            reservation = booking.create(
                hotel=pms["hotel"],
                guest=guest,
                check_in=TODAY,
                check_out=TOMORROW,
                room_type=pms["standard"],
                assign_rooms=False,
            )
            numbers.append(billing.issue_invoice(billing.open_folio(reservation)).number)
        assert numbers == sorted(numbers)
        assert len(set(numbers)) == 3


class TestNightAudit:
    def test_posts_one_room_night_per_in_house_stay(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TODAY + timedelta(days=3),
            room_type=pms["standard"],
        )
        booking.check_in(reservation)

        result = billing.run_night_audit(pms["hotel"])

        assert result["rooms_charged"] == 1
        folio = reservation.folio
        folio.refresh_from_db()
        assert folio.lines.filter(charge_type=ChargeType.ROOM).count() == 1
        assert folio.lines.filter(charge_type=ChargeType.TAX).exists()
        assert folio.charges_total > Decimal("5000")

    def test_is_idempotent_for_one_business_date(self, pms, guest):
        """Night audit gets re-run by hand more often than anyone admits."""
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TODAY + timedelta(days=3),
            room_type=pms["standard"],
        )
        booking.check_in(reservation)

        billing.run_night_audit(pms["hotel"])
        reservation.folio.refresh_from_db()
        first_total = reservation.folio.charges_total
        assert first_total > 0

        # Roll the date back and run it again for the same day.
        state = billing.ensure_business_date(pms["hotel"])
        state.current_date = TODAY
        state.save()
        billing.run_night_audit(pms["hotel"])

        reservation.folio.refresh_from_db()
        assert reservation.folio.charges_total == first_total

    def test_a_guest_leaving_today_is_not_charged_for_tonight(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
            allow_past=True,
        )
        booking.check_in(reservation)
        state = billing.ensure_business_date(pms["hotel"])
        state.current_date = TOMORROW
        state.save()

        result = billing.run_night_audit(pms["hotel"])
        assert result["rooms_charged"] == 0

    def test_rolls_the_business_date(self, pms):
        state = billing.ensure_business_date(pms["hotel"])
        state.current_date = TODAY
        state.save()

        result = billing.run_night_audit(pms["hotel"])
        assert result["next_business_date"] == TOMORROW


# ==============================================================================
# Guests and privacy
# ==============================================================================


class TestGuestPrivacy:
    def test_document_numbers_are_encrypted_at_rest(self, hotel, guest):
        from django.db import connection

        document = GuestDocument.all_objects.create(
            tenant=hotel, guest=guest, doc_type=DocumentType.PASSPORT, number="BX1234567"
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT number FROM guests_guestdocument WHERE id = %s", [str(document.pk)]
            )
            stored = cursor.fetchone()[0]
        assert "BX1234567" not in stored
        assert document.masked_number == "*****4567"

    def test_erasure_removes_documents_but_keeps_financial_history(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        GuestDocument.all_objects.create(
            tenant=pms["hotel"], guest=guest, doc_type=DocumentType.NID, number="1234567890"
        )
        removed = guest.forget()

        guest.refresh_from_db()
        assert removed["documents"] == 1
        assert guest.first_name == "Erased"
        assert guest.email == ""
        # A hotel cannot delete its own accounting records on request.
        assert Reservation.all_objects.filter(pk=reservation.pk).exists()

    def test_consent_is_dated_and_withdrawable(self, hotel, guest):
        consent = GuestConsent.all_objects.create(
            tenant=hotel,
            guest=guest,
            purpose=ConsentPurpose.FACE_RECOGNITION,
            granted=True,
            granted_at=timezone.now(),
            method="kiosk_signature",
        )
        assert guest.has_consent(ConsentPurpose.FACE_RECOGNITION)

        consent.withdraw()
        assert not guest.has_consent(ConsentPurpose.FACE_RECOGNITION)


# ==============================================================================
# Dashboard maths
# ==============================================================================


class TestKPI:
    def test_adr_uses_room_revenue_only(self, pms, guest):
        """Tax and dinner in ADR would make it incomparable with any other hotel."""
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        booking.check_in(reservation)
        folio = reservation.folio
        billing.post_charge(
            folio,
            charge_type=ChargeType.ROOM,
            description="Night",
            amount=Decimal("5000"),
            on_date=TODAY,
        )
        billing.post_charge(
            folio,
            charge_type=ChargeType.RESTAURANT,
            description="Dinner",
            amount=Decimal("3000"),
            on_date=TODAY,
        )
        stats = kpi.for_day(pms["hotel"], TODAY)
        assert stats.room_revenue == Decimal("5000.00")
        assert stats.adr == Decimal("5000.00")

    def test_revpar_spreads_revenue_over_sellable_rooms(self, pms, guest):
        reservation = booking.create(
            hotel=pms["hotel"],
            guest=guest,
            check_in=TODAY,
            check_out=TOMORROW,
            room_type=pms["standard"],
        )
        booking.check_in(reservation)
        billing.post_charge(
            reservation.folio,
            charge_type=ChargeType.ROOM,
            description="Night",
            amount=Decimal("4000"),
            on_date=TODAY,
        )
        stats = kpi.for_day(pms["hotel"], TODAY)
        assert stats.total_rooms == 4
        assert stats.revpar == Decimal("1000.00")

    def test_out_of_order_rooms_leave_the_occupancy_denominator(self, pms):
        pms["rooms"][0].set_status(RoomStatus.OUT_OF_ORDER)
        stats = kpi.for_day(pms["hotel"], TODAY)
        assert stats.total_rooms == 3

    def test_room_status_breakdown_sums_to_the_room_count(self, pms):
        rows = kpi.room_status_breakdown(pms["hotel"])
        assert sum(row["count"] for row in rows) == 4
