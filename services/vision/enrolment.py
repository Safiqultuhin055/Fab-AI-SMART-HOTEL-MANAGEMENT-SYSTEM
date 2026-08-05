"""Capturing a guest's face after their booking is confirmed.

Every function here exists to make one thing hard: storing a face nobody agreed
to. The gate is checked before the first byte is read, consent is written as its
own dated record before any frame is saved, and the whole set goes in one
transaction so a half-enrolled guest cannot exist.

The order matters and is not negotiable:

    1. is this switched on at all?      (platform flag AND hotel flag)
    2. is this guest an adult?          (goal.txt D10 #7)
    3. record what they were asked and what they answered
    4. only then, store frames — encrypted, with an expiry

Declining is a first-class outcome, not an error. :func:`decline` writes the same
kind of record as a yes, because "they were asked and said no" is exactly what a
hotel needs to be able to show, and it stops the kiosk asking again on the next
booking.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.audit import record as audit
from apps.accounts.models import AuditAction
from apps.core.exceptions import PermissionDenied, ValidationError
from apps.guests.models import ConsentPurpose, GuestConsent
from apps.vision.models import CaptureSource, GuestFace

if TYPE_CHECKING:  # pragma: no cover
    from apps.booking.models import Reservation
    from apps.guests.models import Guest
    from apps.tenants.models import Hotel

logger = logging.getLogger("ashos.vision")

#: How many frames a capture session takes. More than one because a single
#: photograph is a poor basis for a later comparison; not so many that the guest
#: is standing at a kiosk being photographed for a minute.
FRAME_COUNT = 6

#: What the guest is asked to do for each frame. Varying the pose is the point —
#: six identical frames are one frame stored six times.
POSE_HINTS = (
    "look straight ahead",
    "look straight ahead",
    "turn slightly left",
    "turn slightly right",
    "chin up a little",
    "smile",
)

#: A kiosk still is ~40-80 KB. A megabyte is a client sending something else.
MAX_FRAME_BYTES = 1_500_000
ALLOWED_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

#: Bumped whenever the wording of the consent screen changes. Without it, "they
#: consented" is unfalsifiable — you cannot show *what* they agreed to.
CONSENT_TEXT_VERSION = "face-capture-v1"


@dataclass
class Enrolment:
    guest_id: str
    reservation_code: str
    stored: int
    expires_at: object
    consent_id: str
    retention_days: int


# ==============================================================================
# The gate
# ==============================================================================


def is_enabled(hotel: Hotel | None) -> bool:
    """Two switches, both required.

    The platform flag is the operator's ("this deployment may process faces at
    all"); the hotel flag is the property's. Either one off means off, so a
    single mis-set environment variable cannot turn it on everywhere.
    """
    if hotel is None:
        return False
    return bool(settings.BIOMETRIC["ENABLED"]) and bool(hotel.biometric_enabled)


def stores_images() -> bool:
    """Whether frames are kept as images.

    Default off. When off, the flow still runs and still records consent, but the
    ``image`` column stays empty — useful for a property that wants the consent
    ledger in place before it starts holding photographs.
    """
    return bool(settings.BIOMETRIC["STORE_RAW_IMAGE"])


def status(hotel: Hotel | None) -> dict:
    """What the kiosk needs in order to decide whether to even ask."""
    return {
        "enabled": is_enabled(hotel),
        "frames": FRAME_COUNT,
        "stores_images": stores_images(),
        "retention_days": int(settings.BIOMETRIC["RETENTION_DAYS"]),
    }


def _assert_allowed(hotel: Hotel | None, guest: Guest) -> None:
    if not is_enabled(hotel):
        raise PermissionDenied("Face capture is not enabled for this property. Nothing was stored.")
    if guest.is_minor:
        # Not a policy preference — a minor cannot give this consent (D10 #7).
        raise PermissionDenied("Face capture is not offered to guests under 18.")


# ==============================================================================
# Consent
# ==============================================================================


@transaction.atomic
def record_consent(
    *,
    hotel: Hotel,
    guest: Guest,
    granted: bool,
    method: str = "kiosk_touch",
    language: str = "",
) -> GuestConsent:
    """Write what the guest was asked and what they answered.

    A fresh row per answer rather than an update: a guest who agrees in March and
    refuses in June has a history, and overwriting it destroys the only evidence
    of the earlier state.
    """
    now = timezone.now()
    consent = GuestConsent.objects.create(
        tenant=hotel,
        guest=guest,
        purpose=ConsentPurpose.FACE_RECOGNITION,
        granted=granted,
        granted_at=now if granted else None,
        withdrawn_at=None if granted else now,
        expires_at=GuestFace.default_expiry() if granted else None,
        method=f"{method}:{CONSENT_TEXT_VERSION}:{language or guest.language or 'en'}"[:40],
    )
    audit(
        AuditAction.BIOMETRIC,
        summary=(
            f"face capture consent {'granted' if granted else 'declined'} "
            f"by guest {guest.pk} ({CONSENT_TEXT_VERSION})"
        ),
        obj=consent,
        hotel_id=str(hotel.pk),
    )
    return consent


def decline(*, hotel: Hotel, guest: Guest, language: str = "") -> GuestConsent:
    """The guest said no. Recorded, and nothing else happens."""
    return record_consent(
        hotel=hotel, guest=guest, granted=False, method="kiosk_decline", language=language
    )


def has_declined(guest: Guest) -> bool:
    """Most recent answer was no — so do not ask again this stay."""
    latest = (
        GuestConsent.objects.filter(guest=guest, purpose=ConsentPurpose.FACE_RECOGNITION)
        .order_by("-created_at")
        .first()
    )
    return latest is not None and not latest.granted


# ==============================================================================
# Capture
# ==============================================================================


@dataclass
class Frame:
    data: bytes
    content_type: str = "image/jpeg"
    width: int = 0
    height: int = 0


@transaction.atomic
def enrol(
    *,
    hotel: Hotel,
    guest: Guest,
    frames: list[Frame],
    reservation: Reservation | None = None,
    language: str = "",
    source: str = CaptureSource.KIOSK,
    consent: GuestConsent | None = None,
) -> Enrolment:
    """Store one capture session.

    Atomic on purpose: four frames written and two failed leaves a guest whose
    stored face is worse than no stored face, and nobody would notice.
    """
    _assert_allowed(hotel, guest)

    if not frames:
        raise ValidationError("No frames were sent.")
    if len(frames) > FRAME_COUNT:
        raise ValidationError(f"At most {FRAME_COUNT} frames per capture.")

    for frame in frames:
        if frame.content_type not in ALLOWED_TYPES:
            raise ValidationError(f"Unsupported image type: {frame.content_type}.")
        if not frame.data:
            raise ValidationError("An empty frame was sent.")
        if len(frame.data) > MAX_FRAME_BYTES:
            raise ValidationError("One of the frames is too large.")

    if consent is None:
        consent = record_consent(hotel=hotel, guest=guest, granted=True, language=language)
    elif not consent.granted:
        raise PermissionDenied("That consent record is not a grant.")

    # Replace rather than accumulate. Re-running a capture for the same stay is
    # a correction, and keeping the rejected attempt means holding more faces
    # than the hotel has a reason for.
    if reservation is not None:
        GuestFace.all_objects.filter(reservation=reservation).hard_delete()

    expires_at = GuestFace.default_expiry()
    keep_images = stores_images()

    rows = [
        GuestFace(
            tenant=hotel,
            guest=guest,
            reservation=reservation,
            consent=consent,
            sequence=index + 1,
            pose_hint=POSE_HINTS[index] if index < len(POSE_HINTS) else "",
            image=base64.b64encode(frame.data).decode() if keep_images else "",
            content_type=frame.content_type,
            byte_size=len(frame.data),
            width=frame.width,
            height=frame.height,
            source=source,
            expires_at=expires_at,
        )
        for index, frame in enumerate(frames)
    ]
    for row in rows:
        row.save()

    audit(
        AuditAction.BIOMETRIC,
        summary=(
            f"stored {len(rows)} face frame(s) for guest {guest.pk}"
            f"{f' / booking {reservation.code}' if reservation else ''}, "
            f"expires {expires_at:%Y-%m-%d}"
        ),
        obj=rows[0],
        hotel_id=str(hotel.pk),
    )
    logger.info(
        "face capture stored",
        extra={
            "guest": str(guest.pk),
            "frames": len(rows),
            "images_stored": keep_images,
            "expires_at": expires_at.isoformat(),
        },
    )

    return Enrolment(
        guest_id=str(guest.pk),
        reservation_code=reservation.code if reservation else "",
        stored=len(rows),
        expires_at=expires_at,
        consent_id=str(consent.pk),
        retention_days=int(settings.BIOMETRIC["RETENTION_DAYS"]),
    )


def frames_for(reservation: Reservation) -> list[GuestFace]:
    """What the front desk compares the arriving guest against.

    Expired rows are excluded even before the purge task has run, so a late
    Celery beat cannot extend the retention window in practice.
    """
    return list(
        GuestFace.objects.filter(reservation=reservation, expires_at__gt=timezone.now()).order_by(
            "sequence"
        )
    )


def data_url(face: GuestFace) -> str:
    """Render one stored frame for a staff screen. Never for a guest screen."""
    if not face.image:
        return ""
    return f"data:{face.content_type};base64,{face.image}"
