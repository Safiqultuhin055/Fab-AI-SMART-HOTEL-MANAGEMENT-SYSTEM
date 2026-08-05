"""Lift one hotel's working AI configuration to platform scope.

The situation this exists for: somebody pastes a real API key into the first
property they set up, it works, and then every hotel onboarded afterwards gets
keyless placeholder rows and a kiosk that says "AI not configured". Copying the
key by hand into each property is the wrong fix — five copies of a credential is
five things to rotate and four chances to miss one.

    manage.py promote_ai_config GLH-001              # every usable capability
    manage.py promote_ai_config GLH-001 --kind llm   # just the chat model
    manage.py promote_ai_config --list               # what resolves where, first

Promotion moves nothing and deletes nothing: it creates (or updates) a
platform-wide row carrying the same credential, and leaves the source property's
row exactly as it was. Any hotel with its own usable row keeps using it.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.ai_center.models import ModelConfig, ModelKind
from apps.tenants.models import Hotel
from services.ai import registry

COPIED_FIELDS = (
    "kind",
    "provider",
    "base_url",
    "api_key",
    "model_name",
    "temperature",
    "max_tokens",
    "timeout_s",
    "dimension",
    "cost_per_1k_input_usd",
    "cost_per_1k_output_usd",
    "extra",
)


class Command(BaseCommand):
    help = "Copy a hotel's usable AI configuration into platform-wide rows."

    def add_arguments(self, parser) -> None:
        parser.add_argument("hotel_code", nargs="?", help="Source property, e.g. GLH-001.")
        parser.add_argument(
            "--kind",
            action="append",
            choices=[k for k, _ in ModelKind.choices],
            help="Limit to these capabilities. Repeatable. Default: all usable ones.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="Show what each hotel currently resolves to, and change nothing.",
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would happen, write nothing."
        )

    def handle(self, *args, **options) -> None:
        if options["list"]:
            self._report()
            return

        code = options["hotel_code"]
        if not code:
            raise CommandError("Give a source hotel code, or --list to see the current state.")

        hotel = Hotel.all_objects.filter(code__iexact=code.strip()).first()
        if hotel is None:
            known = ", ".join(Hotel.all_objects.values_list("code", flat=True))
            raise CommandError(f"No hotel with code {code}. Known: {known or 'none'}")

        kinds = options["kind"] or [k for k, _ in ModelKind.choices]
        dry = options["dry_run"]
        promoted, skipped = 0, []

        for kind in kinds:
            source = self._best_usable(hotel, kind)
            if source is None:
                skipped.append(kind)
                continue

            if dry:
                self.stdout.write(
                    f"  would promote {kind:<16} {source.provider}/{source.model_name}"
                )
                promoted += 1
                continue

            with transaction.atomic():
                target = ModelConfig.all_objects.filter(
                    tenant__isnull=True, kind=kind, is_deleted=False
                ).first()
                if target is None:
                    target = ModelConfig(tenant=None)

                for field in COPIED_FIELDS:
                    setattr(target, field, getattr(source, field))
                target.name = f"{source.name} (platform)"
                target.is_active = True
                target.is_default = True
                # Not inherited: verification and failure state belong to the row
                # that actually made the call, and copying "last verified" would
                # claim a round trip this row has never done.
                target.last_verified_at = None
                target.last_error = ""
                target.external_ref = f"promoted:{hotel.code}:{kind}"
                target.save()

            promoted += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {kind:<16} -> platform   {source.provider}/{source.model_name}"
                )
            )

        if not dry:
            # Every hotel's resolution just changed; a stale minute here looks
            # exactly like the bug this command fixes.
            registry.invalidate()

        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"  no usable credential on {hotel.code} for: {', '.join(skipped)}"
                )
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Would promote' if dry else 'Promoted'} {promoted} capability(ies) "
                f"from {hotel.code} to platform scope."
            )
        )
        if not dry:
            self.stdout.write("")
            self._report()

    # ------------------------------------------------------------------
    def _best_usable(self, hotel: Hotel, kind: str) -> ModelConfig | None:
        rows = ModelConfig.all_objects.filter(
            tenant=hotel, kind=kind, is_active=True, is_deleted=False
        ).order_by("-is_default", "-last_verified_at", "created_at")
        return next((row for row in rows if row.is_usable), None)

    def _report(self) -> None:
        """What each property actually resolves to right now.

        The question an operator has after seeing "AI not configured" on one
        kiosk and a working one next to it.
        """
        platform = {
            row.kind: row
            for row in ModelConfig.all_objects.filter(tenant__isnull=True, is_deleted=False)
            if row.is_usable
        }
        self.stdout.write(self.style.MIGRATE_HEADING("Platform-wide (inherited by every hotel)"))
        if platform:
            for kind, row in sorted(platform.items()):
                self.stdout.write(f"  {kind:<16} {row.provider}/{row.model_name}")
        else:
            self.stdout.write("  (none)")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Per hotel — chat model, and where from"))
        for hotel in Hotel.all_objects.order_by("code"):
            own = self._best_usable(hotel, ModelKind.LLM)
            if own is not None:
                source, detail = "own row", f"{own.provider}/{own.model_name}"
            elif ModelKind.LLM in platform:
                row = platform[ModelKind.LLM]
                source, detail = "platform", f"{row.provider}/{row.model_name}"
            else:
                resolved = registry.resolve("llm", str(hotel.pk))
                configured = bool(resolved.api_key) or resolved.provider in {"fake", "local"}
                source = "env" if configured else "NOTHING USABLE"
                detail = f"{resolved.provider}/{resolved.model_name or '-'}"

            style = self.style.SUCCESS if source != "NOTHING USABLE" else self.style.ERROR
            self.stdout.write(
                f"  {hotel.code:<10} {style(f'{source:<15}')} {detail}   "
                f"(ai_enabled={hotel.ai_enabled})"
            )
