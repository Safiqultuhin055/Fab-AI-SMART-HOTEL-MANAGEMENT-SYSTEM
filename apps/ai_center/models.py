"""AI Center — the control plane (SRS §6).

The whole point of this module: **change AI behaviour without a code deploy**.
Model choice, endpoint, temperature, prompts, thresholds and the kill switch are
all rows, not constants. Operations staff at a hotel cannot ship Python; they
can flip a switch at 2am when the concierge starts answering nonsense.

Two things make that safe:
  * prompts are versioned and rollback-able — one bad edit must not brick
    reception permanently (SRS §6.2);
  * every call is metered into ``UsageLog`` so cost and latency are facts, not
    guesses (goal.txt R3).
"""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.fields import EncryptedTextField
from apps.core.models import ActiveModel, BaseModel, TenantOwnedModel


class ModelKind(models.TextChoices):
    LLM = "llm", _("LLM (chat/completion)")
    EMBEDDING = "embedding", _("Text embedding")
    IMAGE_EMBEDDING = "image_embedding", _("Image embedding (CLIP)")
    FACE = "face", _("Face recognition")
    STT = "stt", _("Speech to text")
    TTS = "tts", _("Text to speech")
    OCR = "ocr", _("OCR")


class Provider(models.TextChoices):
    """Vendors ASHOS can talk to.

    A fixed list rather than free text. Free text meant a typo produced a
    provider nobody had written an adapter for, and the failure surfaced as a
    500 on a guest's first question instead of as a validation error in the
    admin.

    Most of these speak the OpenAI wire format, so one adapter serves them;
    ``PROVIDER_ADAPTERS`` in ``services/ai/registry.py`` records which is which.
    """

    OPENAI = "openai", _("OpenAI")
    ANTHROPIC = "anthropic", _("Anthropic (Claude)")
    GEMINI = "gemini", _("Google AI Studio (Gemini)")
    GOOGLE = "google", _("Google Cloud")
    AZURE_OPENAI = "azure_openai", _("Azure OpenAI")
    AZURE_SPEECH = "azure_speech", _("Azure Speech (TTS/STT)")
    MOONSHOT = "moonshot", _("Moonshot / Kimi")
    ZAI = "zai", _("Z.ai")
    GROQ = "groq", _("Groq")
    OPENROUTER = "openrouter", _("OpenRouter")
    LOCAL = "local", _("Local / self-hosted LLM")
    EDGE_TTS = "edge_tts", _("Edge read-aloud (keyless, Bangla + English)")
    OPENAI_COMPATIBLE = "openai_compatible", _("Other OpenAI-compatible endpoint")
    LOCAL_CLIP = "local_clip", _("Local CLIP (image embedding)")
    LOCAL_INSIGHTFACE = "local_insightface", _("Local InsightFace")
    PADDLEOCR = "paddleocr", _("PaddleOCR")
    FAKE = "fake", _("Fake (tests and demos)")
    OTHER = "other", _("Other")


# Sensible endpoint per provider, so the admin can leave Base URL blank for the
# common case and only fill it in for self-hosted boxes.
DEFAULT_BASE_URLS: dict[str, str] = {
    Provider.OPENAI: "https://api.openai.com/v1",
    Provider.ANTHROPIC: "https://api.anthropic.com/v1",
    # Gemini exposes an OpenAI-compatible surface; using it means one adapter
    # instead of a second wire format to maintain.
    Provider.GEMINI: "https://generativelanguage.googleapis.com/v1beta/openai",
    Provider.MOONSHOT: "https://api.moonshot.cn/v1",
    Provider.ZAI: "https://api.z.ai/api/paas/v4",
    Provider.GROQ: "https://api.groq.com/openai/v1",
    Provider.OPENROUTER: "https://openrouter.ai/api/v1",
}


class ModelConfig(TenantOwnedModel, ActiveModel):
    """One configured AI backend for one capability.

    ``dimension`` is recorded for vector-producing kinds because changing an
    embedding model silently is the single most destructive AI mistake
    available here: the stored vectors and the new query vectors stop living in
    the same space and retrieval quietly returns garbage (goal.txt D08).

    Two scopes. ``tenant`` set means "this property only"; ``tenant`` NULL means
    platform-wide — every hotel inherits it unless it has its own row. That
    matters operationally: a group running five properties on one API key should
    paste that key once, and rotating it should be one edit rather than five.
    Without this, onboarding a hotel silently produced a property whose kiosk
    said "AI not configured" while the others worked.
    """

    # Overrides the non-null field on TenantOwnedModel. The default manager still
    # filters by ambient tenant, so platform rows are only visible through
    # ``all_objects`` — which is what the registry uses.
    tenant = models.ForeignKey(
        "tenants.Hotel",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="ai_model_configs",
        help_text=_("Leave blank to make this configuration apply to every hotel."),
    )

    kind = models.CharField(
        _("capability"), max_length=20, choices=ModelKind.choices, db_index=True
    )
    name = models.CharField(_("label"), max_length=80, help_text=_("Shown in AI Center."))
    provider = models.CharField(
        _("provider"),
        max_length=40,
        choices=Provider.choices,
        default=Provider.OPENAI_COMPATIBLE,
        db_index=True,
    )
    base_url = models.URLField(
        _("base URL"),
        blank=True,
        help_text=_("Leave blank to use the provider default. Required for self-hosted."),
    )
    api_key = EncryptedTextField(
        _("API key"),
        blank=True,
        default="",
        help_text=_("Encrypted at rest. Shown masked once saved."),
    )
    model_name = models.CharField(_("model"), max_length=120)

    # --- Generation parameters -------------------------------------------------
    temperature = models.FloatField(
        default=0.2, validators=[MinValueValidator(0.0), MaxValueValidator(2.0)]
    )
    max_tokens = models.PositiveIntegerField(default=1024)
    timeout_s = models.PositiveSmallIntegerField(default=30)

    # --- Vector contract -------------------------------------------------------
    dimension = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Vector width. Must match the column it writes into."),
    )

    # --- Reliability -----------------------------------------------------------
    fallback = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_for",
        help_text=_("Used when this backend errors or times out."),
    )
    is_default = models.BooleanField(
        _("default for this capability"),
        default=False,
        help_text=_("Exactly one default per hotel per capability."),
    )

    # --- Cost model (goal.txt R3) ---------------------------------------------
    cost_per_1k_input_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    cost_per_1k_output_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)

    extra = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Provider-specific options as JSON, e.g. "
            '{"params": {"top_p": 0.9}, "api_key_header": "api-key"}.'
        ),
    )

    # --- Provenance -------------------------------------------------------------
    # Who added or last touched a credential is exactly the question asked after
    # an unexpected bill or a leaked key.
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    external_ref = models.CharField(
        _("imported from"),
        max_length=80,
        blank=True,
        db_index=True,
        help_text=_("Source system id, set by importers. Makes re-import idempotent."),
    )
    last_verified_at = models.DateTimeField(
        null=True, blank=True, help_text=_("Last successful live round trip.")
    )
    last_error = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _("AI model configuration")
        verbose_name_plural = _("AI model configurations")
        ordering = ("kind", "provider", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "kind"],
                condition=models.Q(is_default=True, is_deleted=False),
                name="uniq_default_model_per_kind",
            ),
            # Postgres treats NULLs as distinct in a unique index, so the
            # constraint above does not cover platform rows at all — without this
            # second one you could have four platform defaults for "llm" and the
            # resolver would pick whichever came back first.
            models.UniqueConstraint(
                fields=["kind"],
                condition=models.Q(tenant__isnull=True, is_default=True, is_deleted=False),
                name="uniq_platform_default_model_per_kind",
            ),
        ]
        indexes = [models.Index(fields=["provider", "is_active"])]

    def __str__(self) -> str:
        scope = self.tenant.code if self.tenant_id else "platform"
        return f"{self.get_kind_display()}: {self.name} ({self.model_name}) · {scope}"

    @property
    def is_platform_wide(self) -> bool:
        return self.tenant_id is None

    @property
    def is_usable(self) -> bool:
        """Would a call through this row actually work?

        A row with no credential is a placeholder, not a configuration. Seeding
        created exactly those for every new property, and because they were
        marked default they shadowed a working platform key — which is how a
        hotel ended up showing "AI not configured" with seven rows in AI Center.
        """
        from services.ai.gateway import KEYLESS_PROVIDERS

        if not self.is_active or self.is_deleted:
            return False
        if not self.model_name:
            return False
        return bool(self.api_key) or self.provider in KEYLESS_PROVIDERS

    def save(self, *args, **kwargs):
        # Fill the endpoint from the provider default so an operator only has to
        # type a URL when self-hosting.
        if not self.base_url and self.provider in DEFAULT_BASE_URLS:
            self.base_url = DEFAULT_BASE_URLS[self.provider]

        if not self.is_default:
            return super().save(*args, **kwargs)

        # "Make this the default" means "and stop the other one being default".
        # Without this the partial unique index rejects the save and the operator
        # gets a 500 telling them a constraint name — for what is a completely
        # ordinary action. Demote first, in the same transaction, so the index is
        # never momentarily violated and a crash cannot leave zero defaults.
        with transaction.atomic():
            siblings = ModelConfig.all_objects.filter(
                tenant_id=self.tenant_id, kind=self.kind, is_default=True
            ).exclude(pk=self.pk)

            if siblings.exists():
                siblings.update(is_default=False, updated_at=timezone.now())
                # ``update_fields`` would otherwise omit the column when the
                # caller passed a narrow list, silently skipping the promotion.
                if kwargs.get("update_fields") is not None:
                    kwargs["update_fields"] = set(kwargs["update_fields"]) | {"is_default"}

            return super().save(*args, **kwargs)

    @property
    def masked_key(self) -> str:
        """``sk-a…WwAA (108 chars)`` — enough to identify, useless to steal."""
        key = self.api_key or ""
        if not key:
            return "—"
        head, tail = key[:4], key[-4:]
        return f"{head}…{tail} ({len(key)} chars)"

    @property
    def effective_base_url(self) -> str:
        return self.base_url or DEFAULT_BASE_URLS.get(self.provider, "")


class PromptTemplate(BaseModel):
    """A named prompt slot, e.g. ``reception.system``. Versions hang off it."""

    key = models.SlugField(_("key"), max_length=80, unique=True)
    name = models.CharField(_("name"), max_length=120)
    description = models.TextField(_("what this prompt controls"), blank=True)
    variables = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Placeholder names the template accepts, e.g. ['guest_name', 'context']."),
    )

    class Meta:
        verbose_name = _("prompt template")
        verbose_name_plural = _("prompt templates")
        ordering = ("key",)

    def __str__(self) -> str:
        return self.key

    @property
    def active_version(self) -> PromptVersion | None:
        return self.versions.filter(is_active=True).order_by("-version").first()


class PromptVersion(BaseModel):
    """An immutable prompt revision.

    Edits create a new row; the old text stays readable. Rollback is flipping
    ``is_active`` back, which takes a second — versus reconstructing a prompt
    from memory during an outage, which does not work.
    """

    template = models.ForeignKey(PromptTemplate, on_delete=models.CASCADE, related_name="versions")
    tenant = models.ForeignKey(
        "tenants.Hotel",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="prompt_versions",
        help_text=_("Null = platform default, inherited by every hotel."),
    )
    version = models.PositiveIntegerField(default=1)
    system_prompt = models.TextField(_("system prompt"))
    user_template = models.TextField(_("user message template"), blank=True)
    notes = models.CharField(_("change note"), max_length=255, blank=True)
    is_active = models.BooleanField(default=False, db_index=True)

    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    eval_score = models.FloatField(
        null=True,
        blank=True,
        help_text=_("Latest tests/ai_eval score. A drop blocks activation (goal.txt D14)."),
    )

    class Meta:
        verbose_name = _("prompt version")
        verbose_name_plural = _("prompt versions")
        ordering = ("template__key", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=["template", "tenant", "version"], name="uniq_prompt_version"
            )
        ]

    def __str__(self) -> str:
        scope = self.tenant.code if self.tenant else "platform"
        return f"{self.template.key} v{self.version} [{scope}]"


class UsageLog(models.Model):
    """One row per AI call. Append-only, high volume, partition candidate.

    Deliberately not a ``TenantOwnedModel``: no soft delete, no UUID overhead
    beyond the PK, and writes must stay cheap enough to sit on the hot path of
    every chat turn.
    """

    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    tenant = models.ForeignKey(
        "tenants.Hotel", null=True, on_delete=models.SET_NULL, related_name="ai_usage"
    )
    module = models.CharField(
        max_length=40, db_index=True, help_text=_("reception · rag · vision · housekeeping …")
    )
    kind = models.CharField(max_length=20, choices=ModelKind.choices, db_index=True)
    provider = models.CharField(max_length=40, blank=True)
    model_name = models.CharField(max_length=120, blank=True)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    latency_ms = models.PositiveIntegerField(default=0)

    success = models.BooleanField(default=True, db_index=True)
    error_code = models.CharField(max_length=60, blank=True)
    fallback_used = models.BooleanField(default=False)
    cache_hit = models.BooleanField(default=False)

    request_id = models.CharField(max_length=32, blank=True, db_index=True)
    conversation_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        verbose_name = _("AI usage log")
        verbose_name_plural = _("AI usage log")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["module", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.module}/{self.kind} {self.model_name} {self.latency_ms}ms"


class SafetyPolicy(TenantOwnedModel):
    """Guardrails an operator can tune without a deploy (SRS §6.5)."""

    confidence_threshold = models.FloatField(
        default=0.55,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text=_("Below this, the AI hands off to a human instead of answering."),
    )
    max_conversation_turns = models.PositiveSmallIntegerField(default=30)
    session_token_cap = models.PositiveIntegerField(default=20_000)
    daily_cost_cap_usd = models.DecimalField(max_digits=8, decimal_places=2, default=25)
    blocked_topics = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Phrases the AI must refuse and escalate, e.g. medical advice."),
    )
    handoff_after_repeats = models.PositiveSmallIntegerField(
        default=2,
        help_text=_("Same question asked N times => notify staff (SRS Module 1 fallback)."),
    )
    allow_financial_actions = models.BooleanField(
        default=False,
        help_text=_("Kept off: AI proposes, humans approve money movement (goal.txt D11)."),
    )

    class Meta:
        verbose_name = _("AI safety policy")
        verbose_name_plural = _("AI safety policies")
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_deleted=False),
                name="uniq_safety_policy_per_hotel",
            )
        ]

    def __str__(self) -> str:
        return f"Safety policy — {self.tenant_id}"
