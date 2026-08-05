"""Guest face captures, taken after a booking is confirmed.

Why after, and not at the door: a photograph of somebody who has not yet agreed
to anything is data the hotel had no reason to hold. Once a guest has booked a
room they have a stay to be identified against, they have been asked in plain
language, and their answer is on record. That order — book, ask, then capture —
is the whole design.

What this is for today: **verification by a person**. A receptionist can compare
the guest in front of them with the frames taken at booking. That is a real,
useful check and it needs no biometric model at all.

What this deliberately is not, yet: automatic recognition. There is no embedding
column and no matching code here. Turning a stored face into a searchable vector
is a different feature with a different legal weight (goal.txt D10, R1) and it
lands when the model behind it does.

Three properties hold for every row:

*Consent is a foreign key, not a flag.* A row whose ``consent`` does not point at
a granted ``GuestConsent`` should not exist; the service refuses to create one.

*Frames are encrypted at rest.* ``EncryptedTextField`` means a database dump, a
stray backup or a leaked read-replica does not hand anybody a face.

*Everything expires.* ``expires_at`` is set at capture time from the hotel's
retention window, and ``purge_expired_biometrics`` hard-deletes past it. Retention
is a promise the code keeps rather than a line in a policy document.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import EncryptedTextField
from apps.core.models import TenantOwnedModel


class CaptureSource(models.TextChoices):
    KIOSK = "kiosk", _("Reception kiosk")
    DESK = "desk", _("Front desk")
    PWA = "pwa", _("Guest app")


class GuestFace(TenantOwnedModel):
    """One captured frame of a guest's face.

    Several rows per guest on purpose. A single photograph is a poor basis for
    any later check — one blink, one bad angle, one overhead light and it is
    useless. Six frames from slightly different poses give a person something to
    actually compare against.
    """

    guest = models.ForeignKey("guests.Guest", on_delete=models.CASCADE, related_name="faces")
    reservation = models.ForeignKey(
        "booking.Reservation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="face_captures",
        help_text=_("The stay this capture was taken for."),
    )
    consent = models.ForeignKey(
        "guests.GuestConsent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="face_captures",
        help_text=_("The recorded permission this capture relies on."),
    )

    sequence = models.PositiveSmallIntegerField(
        default=1, help_text=_("1..n within one capture session.")
    )
    pose_hint = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("What the guest was asked to do, e.g. 'look straight', 'turn left'."),
    )

    # Encrypted, therefore unqueryable — which is correct. Nothing should ever
    # filter or sort on the bytes of a face.
    image = EncryptedTextField(
        blank=True, help_text=_("Base64 JPEG. Empty when raw storage is switched off.")
    )
    content_type = models.CharField(max_length=40, default="image/jpeg")
    byte_size = models.PositiveIntegerField(default=0)
    width = models.PositiveSmallIntegerField(default=0)
    height = models.PositiveSmallIntegerField(default=0)

    source = models.CharField(
        max_length=12, choices=CaptureSource.choices, default=CaptureSource.KIOSK
    )
    captured_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(
        db_index=True, help_text=_("Hard-deleted after this instant. Never null.")
    )

    class Meta:
        verbose_name = _("guest face capture")
        verbose_name_plural = _("guest face captures")
        ordering = ("guest", "sequence")
        indexes = [
            models.Index(fields=["tenant", "guest", "sequence"]),
            models.Index(fields=["expires_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "sequence"],
                condition=models.Q(reservation__isnull=False, is_deleted=False),
                name="uniq_face_frame_per_reservation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.guest_id} frame {self.sequence}"

    @property
    def has_image(self) -> bool:
        return bool(self.image)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @staticmethod
    def default_expiry():
        return timezone.now() + timedelta(days=int(settings.BIOMETRIC["RETENTION_DAYS"]))

    def save(self, *args, **kwargs):
        # A capture with no expiry is a capture kept forever. Refusing to let one
        # exist is cheaper than auditing for it later.
        if not self.expires_at:
            self.expires_at = self.default_expiry()
        return super().save(*args, **kwargs)
