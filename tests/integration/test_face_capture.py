"""Face capture: who may take a photo, and everything that must stop them.

This is the first place in ASHOS where a photograph of a person reaches the
database, so most of these tests are about refusal. Each one names a way the
guard could be lost — a flag defaulting the wrong way, a consent field treated
as optional, a confirmation code overheard in a lobby, a retention window that
quietly never applies.

The one thing not tested here is recognition, because there is nothing to test:
no embeddings, no matcher, no endpoint that takes a face and returns a name. The
frames exist for a person at the desk to compare against.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AuditAction, AuditLog
from apps.guests.models import ConsentPurpose, GuestConsent
from apps.vision.models import GuestFace
from services.vision import enrolment

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

URL = "/api/v1/vision/enrolment/"
STATUS_URL = "/api/v1/vision/enrolment/status/"

# Smallest thing a server will accept as a JPEG body. The bytes are never
# decoded — nothing here is an image processing test.
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 512


def frames(count=6):
    return [enrolment.Frame(data=JPEG, content_type="image/jpeg") for _ in range(count)]


@pytest.fixture
def capture_on(settings, hotel):
    """Both switches on, images kept. The only state where capture may happen."""
    settings.BIOMETRIC = {**settings.BIOMETRIC, "ENABLED": True, "STORE_RAW_IMAGE": True}
    hotel.biometric_enabled = True
    hotel.save(update_fields=["biometric_enabled"])
    return hotel


def bind_session(client) -> str:
    """Give the test client a real session, the way a kiosk browser has one.

    The endpoint identifies an anonymous caller by session key alone, so a
    conversation with no session behind it is correctly unreachable. Faking that
    up here keeps the guard strict instead of loosening it for the tests.
    """
    from django.conf import settings as dj_settings

    session = client.session
    session["kiosk"] = True
    session.save()
    client.cookies[dj_settings.SESSION_COOKIE_NAME] = session.session_key
    return session.session_key


@pytest.fixture
def booked(client, hotel, guest_factory):
    """A guest with a confirmed kiosk booking — the precondition for capture."""
    from apps.booking.models import BookingSource
    from apps.core.context import set_request_context
    from apps.rooms.models import RatePlan, Room, RoomType
    from services.booking import reservations
    from services.reception import orchestrator

    set_request_context(tenant_id=str(hotel.pk))
    room_type = RoomType.all_objects.create(
        tenant=hotel,
        code="DLX",
        name="Deluxe King",
        base_occupancy=2,
        max_occupancy=3,
        base_rate=Decimal("7200.00"),
    )
    RatePlan.all_objects.create(tenant=hotel, code="BAR", name="Best Available", is_default=True)
    Room.all_objects.create(tenant=hotel, number="101", room_type=room_type, floor=1)

    conversation = orchestrator.start(
        hotel=hotel, channel="kiosk", session_key=bind_session(client)
    )
    guest = guest_factory()
    check_in = timezone.localdate() + timedelta(days=1)
    reservation = reservations.create(
        hotel=hotel,
        guest=guest,
        check_in=check_in,
        check_out=check_in + timedelta(days=2),
        room_type=room_type,
        source=BookingSource.KIOSK,
        conversation=conversation,
    )
    return reservation


# ==============================================================================
# The gate
# ==============================================================================


class TestTheGate:
    def test_both_switches_are_required(self, hotel, settings):
        """Either flag alone is not enough. One mis-set environment variable must
        not turn face capture on for every tenant."""
        assert enrolment.is_enabled(hotel) is False

        settings.BIOMETRIC = {**settings.BIOMETRIC, "ENABLED": True}
        assert enrolment.is_enabled(hotel) is False, "hotel flag alone must gate it"

        hotel.biometric_enabled = True
        hotel.save()
        assert enrolment.is_enabled(hotel) is True

    def test_a_hotel_defaults_to_off(self, other_hotel):
        """A newly onboarded property does not inherit face capture."""
        assert other_hotel.biometric_enabled is False

    def test_the_shipped_configuration_is_off(self):
        """What a new deployment gets before anybody makes a decision.

        Read from .env.example rather than from live settings: the point is what
        ASHOS ships with, not what the machine running the tests happens to have
        in its own .env.
        """
        from pathlib import Path

        template = (Path(__file__).parents[2] / ".env.example").read_text(encoding="utf-8")
        assert "BIOMETRIC_ENABLED=False" in template
        assert "BIOMETRIC_STORE_RAW_IMAGE=False" in template

    def test_platform_flag_alone_is_not_enough(self, hotel, settings):
        settings.BIOMETRIC = {**settings.BIOMETRIC, "ENABLED": False}
        hotel.biometric_enabled = True
        hotel.save()
        assert enrolment.is_enabled(hotel) is False

    def test_retention_is_always_set(self, capture_on, booked):
        """A capture with no expiry is a capture kept forever."""
        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked)
        for face in GuestFace.all_objects.all():
            assert face.expires_at is not None
            assert face.expires_at > timezone.now()

    def test_a_minor_is_refused(self, capture_on, booked):
        from apps.core.exceptions import PermissionDenied

        booked.guest.date_of_birth = date.today() - timedelta(days=365 * 14)
        booked.guest.save()

        with pytest.raises(PermissionDenied):
            enrolment.enrol(
                hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked
            )
        assert GuestFace.all_objects.count() == 0

    def test_the_service_refuses_when_the_property_has_it_off(self, hotel, booked):
        from apps.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            enrolment.enrol(hotel=hotel, guest=booked.guest, frames=frames(), reservation=booked)
        assert GuestFace.all_objects.count() == 0


# ==============================================================================
# Consent
# ==============================================================================


class TestConsent:
    def test_a_capture_always_has_a_granted_consent_row(self, capture_on, booked):
        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked)

        for face in GuestFace.all_objects.all():
            assert face.consent is not None
            assert face.consent.granted is True
            assert face.consent.purpose == ConsentPurpose.FACE_RECOGNITION

    def test_a_withdrawn_consent_cannot_be_reused(self, capture_on, booked):
        from apps.core.exceptions import PermissionDenied

        refusal = enrolment.decline(hotel=capture_on, guest=booked.guest)
        with pytest.raises(PermissionDenied):
            enrolment.enrol(
                hotel=capture_on,
                guest=booked.guest,
                frames=frames(),
                reservation=booked,
                consent=refusal,
            )

    def test_declining_is_recorded_rather_than_forgotten(self, capture_on, booked):
        """ "We asked and they said no" is what a hotel has to be able to show —
        and it is what stops the kiosk asking twice."""
        enrolment.decline(hotel=capture_on, guest=booked.guest)

        consent = GuestConsent.objects.get(guest=booked.guest)
        assert consent.granted is False
        assert consent.withdrawn_at is not None
        assert enrolment.has_declined(booked.guest) is True
        assert GuestFace.all_objects.count() == 0

    def test_the_wording_version_is_recorded(self, capture_on, booked):
        """Without it, "they consented" is unfalsifiable — you cannot show what
        they agreed to."""
        consent = enrolment.record_consent(
            hotel=capture_on, guest=booked.guest, granted=True, language="bn"
        )
        assert enrolment.CONSENT_TEXT_VERSION in consent.method
        assert "bn" in consent.method

    def test_both_answers_are_audited(self, capture_on, booked):
        enrolment.record_consent(hotel=capture_on, guest=booked.guest, granted=True)
        enrolment.decline(hotel=capture_on, guest=booked.guest)

        entries = AuditLog.objects.filter(action=AuditAction.BIOMETRIC)
        assert any("granted" in e.summary for e in entries)
        assert any("declined" in e.summary for e in entries)


# ==============================================================================
# Storage
# ==============================================================================


class TestStorage:
    def test_six_frames_with_different_pose_prompts(self, capture_on, booked):
        result = enrolment.enrol(
            hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked
        )

        assert result.stored == enrolment.FRAME_COUNT == 6
        rows = list(GuestFace.objects.order_by("sequence"))
        assert [r.sequence for r in rows] == [1, 2, 3, 4, 5, 6]
        # Varying the pose is the point; six identical frames are one frame.
        assert len({r.pose_hint for r in rows}) > 1

    def test_the_image_is_encrypted_in_the_column(self, capture_on, booked):
        """A database dump must not hand anybody a face."""
        from django.db import connection

        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(1), reservation=booked)
        with connection.cursor() as cursor:
            cursor.execute("SELECT image FROM vision_guestface LIMIT 1")
            stored = cursor.fetchone()[0]

        assert stored.startswith("enc:v1:")
        # ...and still readable through the ORM.
        assert GuestFace.objects.first().image

    def test_nothing_is_stored_when_raw_storage_is_off(self, capture_on, booked, settings):
        settings.BIOMETRIC = {**settings.BIOMETRIC, "STORE_RAW_IMAGE": False}
        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked)

        rows = GuestFace.objects.all()
        assert rows.count() == 6, "the consent trail is still written"
        assert all(not row.image for row in rows)

    def test_recapturing_replaces_rather_than_accumulates(self, capture_on, booked):
        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked)
        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked)
        assert GuestFace.objects.count() == 6

    def test_too_many_frames_is_refused(self, capture_on, booked):
        from apps.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            enrolment.enrol(
                hotel=capture_on, guest=booked.guest, frames=frames(9), reservation=booked
            )

    def test_a_non_image_is_refused(self, capture_on, booked):
        from apps.core.exceptions import ValidationError

        bad = [enrolment.Frame(data=b"MZ\x90\x00", content_type="application/x-msdownload")]
        with pytest.raises(ValidationError):
            enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=bad, reservation=booked)

    def test_an_oversized_frame_is_refused(self, capture_on, booked):
        from apps.core.exceptions import ValidationError

        huge = [enrolment.Frame(data=b"0" * (enrolment.MAX_FRAME_BYTES + 1))]
        with pytest.raises(ValidationError):
            enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=huge, reservation=booked)

    def test_expired_frames_are_not_served_even_before_the_purge_runs(self, capture_on, booked):
        """A late Celery beat must not extend the retention window in practice."""
        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked)
        GuestFace.all_objects.update(expires_at=timezone.now() - timedelta(minutes=1))

        assert enrolment.frames_for(booked) == []

    def test_the_purge_hard_deletes(self, capture_on, booked):
        """Soft-deleting a biometric row leaves the biometric data in place."""
        from apps.vision.tasks import purge_expired_biometrics

        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked)
        GuestFace.all_objects.update(expires_at=timezone.now() - timedelta(days=1))

        assert purge_expired_biometrics()["deleted"] == 6
        assert GuestFace.all_objects.count() == 0

    def test_erasing_a_guest_removes_their_faces(self, capture_on, booked):
        enrolment.enrol(hotel=capture_on, guest=booked.guest, frames=frames(), reservation=booked)
        removed = booked.guest.forget()

        assert removed["faces"] == 6
        assert GuestFace.all_objects.count() == 0


# ==============================================================================
# The endpoint
# ==============================================================================


class TestEndpoint:
    def test_refused_outright_when_capture_is_off(self, client, hotel, booked):
        """Refused before a byte is read. An uploaded face that is then rejected
        has still been uploaded."""
        response = client.post(
            URL,
            {
                "conversation": str(booked.conversation_id),
                "reservation": booked.code,
                "consent": "true",
            },
            HTTP_X_HOTEL_CODE=hotel.code,
        )
        assert response.status_code == 403
        assert GuestFace.all_objects.count() == 0

    def test_status_tells_the_kiosk_not_to_ask(self, client, hotel):
        response = client.get(STATUS_URL, HTTP_X_HOTEL_CODE=hotel.code)
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_status_reports_the_terms_when_on(self, client, capture_on):
        body = client.get(STATUS_URL, HTTP_X_HOTEL_CODE=capture_on.code).json()
        assert body == {
            "enabled": True,
            "frames": 6,
            "stores_images": True,
            "retention_days": 90,
        }

    def test_consent_must_be_sent_explicitly(self, client, capture_on, booked):
        """Omitting the field means no. A default-true checkbox is not consent."""
        response = client.post(
            URL,
            {"conversation": str(booked.conversation_id), "reservation": booked.code},
            HTTP_X_HOTEL_CODE=capture_on.code,
        )
        assert response.status_code == 200
        assert response.json()["declined"] is True
        assert GuestFace.all_objects.count() == 0
        assert GuestConsent.objects.get(guest=booked.guest).granted is False

    def test_a_booking_from_another_conversation_is_refused(self, client, capture_on, booked):
        """Confirmation codes are read aloud across a lobby. Knowing one must not
        be enough to attach photographs to somebody else's stay."""
        from services.reception import orchestrator

        # Same browser session, different conversation — so the only thing this
        # test can be failing on is the conversation/booking mismatch.
        other = orchestrator.start(
            hotel=capture_on, channel="kiosk", session_key=client.session.session_key
        )
        response = client.post(
            URL,
            {"conversation": str(other.pk), "reservation": booked.code, "consent": "true"},
            HTTP_X_HOTEL_CODE=capture_on.code,
        )
        assert response.status_code == 404
        assert GuestFace.all_objects.count() == 0

    def test_an_unknown_booking_is_refused(self, client, capture_on, booked):
        response = client.post(
            URL,
            {
                "conversation": str(booked.conversation_id),
                "reservation": "NOPE1234",
                "consent": "true",
            },
            HTTP_X_HOTEL_CODE=capture_on.code,
        )
        assert response.status_code == 404

    def test_consent_without_frames_is_a_validation_error(self, client, capture_on, booked):
        response = client.post(
            URL,
            {
                "conversation": str(booked.conversation_id),
                "reservation": booked.code,
                "consent": "true",
            },
            HTTP_X_HOTEL_CODE=capture_on.code,
        )
        assert response.status_code == 422
        assert GuestFace.all_objects.count() == 0

    def test_a_full_upload_stores_the_set(self, client, capture_on, booked):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile

        payload = {
            "conversation": str(booked.conversation_id),
            "reservation": booked.code,
            "consent": "true",
            "frames": [
                SimpleUploadedFile(f"f{i}.jpg", io.BytesIO(JPEG).read(), content_type="image/jpeg")
                for i in range(6)
            ],
        }
        response = client.post(URL, payload, HTTP_X_HOTEL_CODE=capture_on.code)

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["stored"] == 6
        assert body["declined"] is False
        assert body["reservation"] == booked.code
        assert body["retention_days"] == 90
        assert GuestFace.objects.filter(reservation=booked).count() == 6


class TestNoRecognitionYet:
    def test_there_is_no_endpoint_that_identifies_anyone(self):
        """Storing a face for a person to compare is one feature. Matching one
        automatically is another, with different legal weight (goal.txt D10, R1)
        — and there must be nowhere to ask for it."""
        from django.urls import NoReverseMatch

        for name in ("vision_face_identify", "vision_face_match", "vision_face_search"):
            with pytest.raises(NoReverseMatch):
                reverse(f"v1:{name}")

    def test_the_model_holds_no_embedding(self):
        fields = {f.name for f in GuestFace._meta.get_fields()}
        assert "embedding" not in fields
        assert "vector" not in fields

    def test_loading_the_kiosk_stores_nothing(self, client, hotel):
        client.get(f"{reverse('reception:kiosk')}?hotel={hotel.code}")
        assert GuestConsent.all_objects.count() == 0
        assert GuestFace.all_objects.count() == 0
