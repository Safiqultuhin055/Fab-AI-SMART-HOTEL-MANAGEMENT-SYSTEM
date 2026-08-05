"""Tenancy root.

One ``Hotel`` row is one tenant. Every domain table carries ``tenant_id``
pointing here (goal.txt §2.2). The MVP UI operates a single hotel, but the
schema, managers and permissions are multi-tenant from day one so the SaaS
ambition in Prompt.txt does not require a rewrite later.
"""

from __future__ import annotations

from datetime import time

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import ActiveModel, BaseModel


class HotelPlan(models.TextChoices):
    PILOT = "pilot", _("Pilot")
    STANDARD = "standard", _("Standard")
    ENTERPRISE = "enterprise", _("Enterprise")


class PaymentTiming(models.TextChoices):
    """When the guest's money is due, for a booking made online.

    Two values, not three. There is no "pay online now" here because there is no
    gateway wired to take it: offering it would be a button that cannot charge a
    card. When a gateway is integrated it arrives as a third value plus an adapter,
    and the sentences guests read come from the same place they do today.
    """

    ON_ARRIVAL = "on_arrival", _("Nothing online — settled at the desk")
    ADVANCE = "advance", _("Advance to the hotel's wallet before the room is held")


class AdvanceWallet(models.TextChoices):
    NONE = "", _("No advance accepted")
    BKASH = "bkash", _("bKash")
    NAGAD = "nagad", _("Nagad")


class Hotel(BaseModel, ActiveModel):
    code = models.CharField(
        _("code"),
        max_length=12,
        unique=True,
        validators=[RegexValidator(r"^[A-Z0-9\-]{3,12}$", "Uppercase letters, digits, dash.")],
        help_text=_("Short stable identifier, e.g. GLH-001. Appears on invoices."),
    )
    name = models.CharField(_("name"), max_length=150)
    legal_name = models.CharField(_("legal name"), max_length=200, blank=True)
    slug = models.SlugField(_("slug"), max_length=150, unique=True)

    # --- Contact / location ---------------------------------------------------
    address_line1 = models.CharField(_("address line 1"), max_length=200, blank=True)
    address_line2 = models.CharField(_("address line 2"), max_length=200, blank=True)
    city = models.CharField(_("city"), max_length=80, blank=True)
    state = models.CharField(_("state/division"), max_length=80, blank=True)
    postal_code = models.CharField(_("postal code"), max_length=20, blank=True)
    country = models.CharField(_("country"), max_length=2, default="BD")
    phone = models.CharField(_("phone"), max_length=32, blank=True)
    email = models.EmailField(_("email"), blank=True)
    website = models.URLField(_("website"), blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # --- Operational ----------------------------------------------------------
    timezone = models.CharField(_("timezone"), max_length=64, default="Asia/Dhaka")
    currency = models.CharField(_("currency"), max_length=3, default="BDT")
    star_rating = models.PositiveSmallIntegerField(_("star rating"), default=3)
    total_rooms = models.PositiveIntegerField(_("total rooms"), default=0)
    # Real ``time`` objects, not strings: a string default survives a round trip
    # through the database but an unsaved instance carries the raw str, and any
    # code that formats it (``{t:%H:%M}``) blows up only on that path.
    check_in_time = models.TimeField(_("standard check-in"), default=time(14, 0))
    check_out_time = models.TimeField(_("standard check-out"), default=time(12, 0))

    # --- Finance --------------------------------------------------------------
    tax_rate = models.DecimalField(
        _("VAT %"),
        max_digits=5,
        decimal_places=2,
        default=15,
        validators=[MinValueValidator(0)],
    )
    service_charge_rate = models.DecimalField(
        _("service charge %"), max_digits=5, decimal_places=2, default=10
    )
    tax_registration_no = models.CharField(_("BIN/VAT no"), max_length=50, blank=True)

    # --- How a guest pays ------------------------------------------------------
    # On the record because the assistant has to be able to ANSWER it. "Can I pay
    # when I get there?" is one of the three questions every online booking asks,
    # and with nothing here the concierge had nothing to cite — so it said it could
    # not confirm and offered to fetch a human, on a page where no human is coming.
    # A property's payment terms are not something a language model may improvise.
    payment_timing = models.CharField(
        _("when payment is due"),
        max_length=16,
        choices=PaymentTiming.choices,
        default=PaymentTiming.ON_ARRIVAL,
        help_text=_(
            "On arrival: nothing is taken online, the room is held and settled at the desk. "
            "Advance: the guest sends a deposit to the wallet below before the room is held."
        ),
    )
    accepts_cash = models.BooleanField(_("accepts cash"), default=True)
    accepts_card = models.BooleanField(_("accepts cards at the desk"), default=True)
    accepts_bkash = models.BooleanField(_("accepts bKash"), default=False)
    accepts_nagad = models.BooleanField(_("accepts Nagad"), default=False)
    # Which wallet an advance goes to, and its number. Separate from the accepts_*
    # flags: a hotel can take bKash at the desk without publishing a number for
    # strangers on the internet to send money to.
    advance_wallet = models.CharField(
        _("advance wallet"),
        max_length=16,
        choices=AdvanceWallet.choices,
        default=AdvanceWallet.NONE,
        blank=True,
    )
    advance_wallet_number = models.CharField(
        _("advance wallet number"),
        max_length=32,
        blank=True,
        help_text=_("Shown to guests only when an advance is required. Personal/merchant number."),
    )
    payment_note = models.CharField(
        _("payment note"),
        max_length=255,
        blank=True,
        help_text=_(
            "The property's own sentence about payment, shown to guests as written and "
            "quoted by the assistant. Overrides nothing — it is added to the terms above."
        ),
    )

    # --- Branding (drives the dark glassmorphism theme, Prototype.png) --------
    logo = models.ImageField(_("logo"), upload_to="hotels/logo/", blank=True, null=True)
    accent_color = models.CharField(_("accent colour"), max_length=7, default="#6366F1")

    # --- SaaS -----------------------------------------------------------------
    plan = models.CharField(max_length=20, choices=HotelPlan.choices, default=HotelPlan.PILOT)

    # --- AI posture (per-hotel override of the global gateway defaults) -------
    ai_enabled = models.BooleanField(_("AI enabled"), default=True)
    ai_kill_switch = models.BooleanField(
        _("AI kill switch"),
        default=False,
        help_text=_("Immediately routes every AI surface to manual staff mode."),
    )
    biometric_enabled = models.BooleanField(
        _("face recognition enabled"),
        default=False,
        help_text=_("Requires documented legal sign-off before enabling (goal.txt R1)."),
    )
    ai_daily_cost_cap_usd = models.DecimalField(max_digits=8, decimal_places=2, default=25)

    # --- Lobby kiosk behaviour -------------------------------------------------
    kiosk_greeting_style = models.CharField(
        _("kiosk greeting"),
        max_length=16,
        default="neutral",
        help_text=_("How the kiosk opens when a guest steps up. neutral · formal · islamic"),
    )
    kiosk_language = models.CharField(
        _("kiosk language"),
        max_length=8,
        default="en",
        choices=[("en", _("English")), ("bn", _("বাংলা"))],
        help_text=_(
            "The language the kiosk OPENS in. Each answer is then given in whatever "
            "language the guest actually used, so this is the greeting, not a limit."
        ),
    )
    # The lobby scene. Both optional, and both deliberately uploads rather than
    # bundled artwork: a stock photograph of a face implies a receptionist who does
    # not exist, and we hold no licence for anybody's likeness. A property that
    # wants a human figure on its kiosk supplies its own — their image, their
    # consent, their brand. With neither set the kiosk falls back to the stylised
    # orb, which is honest about being software.
    kiosk_avatar = models.ImageField(
        _("kiosk avatar"),
        upload_to="hotels/kiosk/",
        blank=True,
        null=True,
        help_text=_(
            "Portrait shown beside the conversation. Transparent PNG, roughly "
            "900x1200. Leave empty for the stylised orb."
        ),
    )
    kiosk_backdrop = models.ImageField(
        _("kiosk backdrop"),
        upload_to="hotels/kiosk/",
        blank=True,
        null=True,
        help_text=_("Lobby photograph behind the scene. Wide, e.g. 1920x1080."),
    )
    kiosk_hint = models.CharField(
        _("kiosk hint"),
        max_length=120,
        blank=True,
        help_text=_(
            'One short line under the scene, e.g. "You can ask me anything about '
            'our hotel services." Blank uses a sensible default.'
        ),
    )
    kiosk_hands_free = models.BooleanField(
        _("hands-free microphone"),
        default=True,
        help_text=_(
            "Listen automatically instead of making the guest press a button. "
            "Only within an active conversation, and it stands down after a period "
            "of silence — a lobby microphone that is open all day also hears "
            "everyone walking past."
        ),
    )
    kiosk_voice_gender = models.CharField(
        _("kiosk voice"),
        max_length=12,
        default="female",
        choices=[
            ("female", _("Female")),
            ("male", _("Male")),
            ("any", _("Whatever the device offers")),
        ],
        help_text=_("Preferred voice for spoken answers."),
    )
    kiosk_voice_name = models.CharField(
        _("voice name"),
        max_length=40,
        blank=True,
        help_text=_(
            "Exact provider voice id, e.g. 'nova' or 'shimmer' for OpenAI. Leave "
            "blank to let the provider choose one matching the preference above."
        ),
    )
    kiosk_presence_detection = models.BooleanField(
        _("greet on approach"),
        default=True,
        help_text=_(
            "Camera watches for someone at the kiosk and greets automatically. "
            "Presence only — no identification, and nothing leaves the browser."
        ),
    )
    kiosk_capture_photo = models.BooleanField(
        _("hold a photo during the session"),
        default=False,
        help_text=_(
            "Freezes a still for staff to see who is at the desk. Kept in the "
            "browser for the session and never uploaded unless face recognition "
            "is enabled with consent."
        ),
    )

    class Meta:
        verbose_name = _("hotel")
        verbose_name_plural = _("hotels")
        ordering = ("name",)
        indexes = [models.Index(fields=["is_active", "plan"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    @property
    def ai_available(self) -> bool:
        return self.is_active and self.ai_enabled and not self.ai_kill_switch


class HotelMembership(BaseModel):
    """Which staff user may operate which hotel, and in what role.

    Kept separate from ``User`` so one account can serve several properties once
    the multi-property UI ships, without duplicating logins.
    """

    hotel = models.ForeignKey(Hotel, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="hotel_memberships"
    )
    role = models.ForeignKey("accounts.Role", on_delete=models.PROTECT, related_name="memberships")
    is_default = models.BooleanField(
        _("default hotel"),
        default=False,
        help_text=_("Hotel selected automatically when this user signs in."),
    )

    class Meta:
        verbose_name = _("hotel membership")
        verbose_name_plural = _("hotel memberships")
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "user"],
                condition=models.Q(is_deleted=False),
                name="uniq_active_membership_per_hotel_user",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.hotel} as {self.role}"
