"""Guest profiles, identity documents and consent.

One profile per person, across every stay. Everything else — reservations,
folios, conversations, and from P3 face embeddings — points here.

Privacy shape (goal.txt D10, §13.3):
  * document numbers are encrypted at rest and only ever shown masked
  * consent is a dated record with a purpose, not a boolean nobody can audit
  * ``forget()`` exists because the right to erasure has to be a code path, not
    a promise in a policy document
"""

from __future__ import annotations

import contextlib
from datetime import date

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import EncryptedTextField
from apps.core.models import TenantOwnedModel
from apps.core.utils import mask


class Title(models.TextChoices):
    MR = "mr", _("Mr")
    MRS = "mrs", _("Mrs")
    MS = "ms", _("Ms")
    DR = "dr", _("Dr")
    PROF = "prof", _("Prof")


class DocumentType(models.TextChoices):
    PASSPORT = "passport", _("Passport")
    NID = "nid", _("National ID")
    DRIVING_LICENCE = "driving_licence", _("Driving licence")
    BIRTH_CERTIFICATE = "birth_certificate", _("Birth certificate")
    OTHER = "other", _("Other")


class ConsentPurpose(models.TextChoices):
    FACE_RECOGNITION = "face", _("Face recognition")
    MARKETING = "marketing", _("Marketing messages")
    DATA_PROCESSING = "processing", _("Data processing")
    PHOTO = "photo", _("Photography on premises")


class GuestTier(models.TextChoices):
    STANDARD = "standard", _("Standard")
    SILVER = "silver", _("Silver")
    GOLD = "gold", _("Gold")
    PLATINUM = "platinum", _("Platinum")
    VIP = "vip", _("VIP")


class Guest(TenantOwnedModel):
    title = models.CharField(max_length=8, choices=Title.choices, blank=True)
    first_name = models.CharField(_("first name"), max_length=80)
    last_name = models.CharField(_("last name"), max_length=80, blank=True)

    email = models.EmailField(blank=True, db_index=True)
    phone = models.CharField(max_length=32, blank=True, db_index=True)

    nationality = models.CharField(max_length=2, blank=True, help_text=_("ISO country code."))
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=16, blank=True)

    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=80, blank=True)
    country = models.CharField(max_length=2, blank=True)

    language = models.CharField(
        max_length=8, default="en", help_text=_("Drives the language the AI replies in.")
    )
    tier = models.CharField(max_length=16, choices=GuestTier.choices, default=GuestTier.STANDARD)

    preferences = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            'e.g. {"floor": "high", "bed": "king", "view": "sea"} — feeds AI room '
            "recommendation."
        ),
    )
    notes = models.TextField(blank=True, help_text=_("Internal. The guest never sees this."))
    is_blacklisted = models.BooleanField(default=False)

    # Denormalised because every reservation screen shows them and recomputing
    # from folio history on each render is a needless join.
    total_stays = models.PositiveIntegerField(default=0)
    total_spend = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    last_stay_at = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = _("guest")
        verbose_name_plural = _("guests")
        ordering = ("last_name", "first_name")
        indexes = [
            models.Index(fields=["tenant", "last_name", "first_name"]),
            models.Index(fields=["tenant", "phone"]),
            models.Index(fields=["tenant", "email"]),
        ]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        parts = [self.get_title_display() if self.title else "", self.first_name, self.last_name]
        return " ".join(part for part in parts if part).strip()

    @property
    def is_minor(self) -> bool:
        """Under 18. Biometric enrolment is refused for minors (goal.txt D10 #7)."""
        if not self.date_of_birth:
            return False
        today = date.today()
        years = today.year - self.date_of_birth.year
        if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
            years -= 1
        return years < 18

    def has_consent(self, purpose: str) -> bool:
        return self.consents.filter(
            purpose=purpose, granted=True, withdrawn_at__isnull=True
        ).exists()

    def forget(self) -> dict[str, int]:
        """Right to erasure (goal.txt D10 #6).

        Financial records must survive — a hotel cannot delete an invoice on
        request — so the guest is anonymised rather than deleted, and everything
        personally identifying is removed. Documents and biometrics are hard
        deleted, because those are the data the right actually targets.
        """
        removed = {"documents": 0, "faces": 0}

        removed["documents"] = self.documents.all().hard_delete()[0]
        # The face relation arrives in P3; erasure must work before then.
        with contextlib.suppress(AttributeError):
            removed["faces"] = self.faces.all().hard_delete()[0]

        self.first_name = "Erased"
        self.last_name = "Guest"
        self.email = ""
        self.phone = ""
        self.address = ""
        self.date_of_birth = None
        self.notes = ""
        self.preferences = {}
        self.save()
        self.consents.update(withdrawn_at=timezone.now())
        return removed


class GuestDocument(TenantOwnedModel):
    guest = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=20, choices=DocumentType.choices)

    # Encrypted: a passport number in a plain column is the single most
    # damaging field in a leaked hotel database.
    number = EncryptedTextField(_("document number"))
    issuing_country = models.CharField(max_length=2, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    holder_name = models.CharField(max_length=150, blank=True)
    scan = models.ImageField(upload_to="guests/documents/%Y/%m/", null=True, blank=True)

    # --- OCR provenance (P2) --------------------------------------------------
    extracted = models.JSONField(
        default=dict, blank=True, help_text=_("Raw OCR field extraction, for audit.")
    )
    mrz_valid = models.BooleanField(
        null=True, blank=True, help_text=_("MRZ checksum result. Null means not checked.")
    )
    ocr_confidence = models.FloatField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = _("guest document")
        verbose_name_plural = _("guest documents")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["guest", "doc_type"])]

    def __str__(self) -> str:
        return f"{self.get_doc_type_display()} {self.masked_number}"

    @property
    def masked_number(self) -> str:
        return mask(self.number, keep=4)

    @property
    def is_expired(self) -> bool:
        return bool(self.expiry_date and self.expiry_date < date.today())


class GuestConsent(TenantOwnedModel):
    """A dated, purpose-scoped consent record.

    Not a boolean on the guest: "did they agree, to what, when, and how" is the
    question a regulator asks, and a flag cannot answer it.
    """

    guest = models.ForeignKey(Guest, on_delete=models.CASCADE, related_name="consents")
    purpose = models.CharField(max_length=20, choices=ConsentPurpose.choices)
    granted = models.BooleanField(default=False)

    granted_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    method = models.CharField(
        max_length=40,
        blank=True,
        help_text=_("kiosk_signature · web_form · paper · verbal_to_staff"),
    )
    evidence = models.CharField(
        max_length=255, blank=True, help_text=_("Signature file key, form id, or staff note.")
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = _("guest consent")
        verbose_name_plural = _("guest consents")
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["guest", "purpose", "granted"])]

    def __str__(self) -> str:
        state = "granted" if self.granted and not self.withdrawn_at else "not granted"
        return f"{self.guest} · {self.get_purpose_display()} · {state}"

    def withdraw(self) -> None:
        self.withdrawn_at = timezone.now()
        self.granted = False
        self.save(update_fields=["withdrawn_at", "granted", "updated_at"])
