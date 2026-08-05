"""Import an existing ``api_integrations`` table into AI Center.

Written for the SQL Server table used by the sibling POS project:

    id, provider, label, api_key, api_model, base_url, extra_config,
    is_default, is_active, is_deleted, created_by, created_at,
    updated_by, updated_at

Usage — export the table to CSV or JSON first, then:

    python manage.py import_api_integrations integrations.csv --hotel GLH-001
    python manage.py import_api_integrations integrations.json --hotel GLH-001 --dry-run

Idempotent: rows are matched on ``external_ref`` (``<source>:<id>``), so a
re-import updates rather than duplicates.

Two deliberate behaviours:

*Nothing is activated automatically.* An imported credential lands inactive and
non-default. Silently pointing a hotel's live receptionist at a key copied from
another system is not a migration, it is an incident. Review, test, then
activate.

*Capability is inferred, never guessed silently.* The source table has no
concept of capability (LLM vs embedding vs TTS), so it is derived from the
model name and reported per row. Anything ambiguous is flagged.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.ai_center.models import DEFAULT_BASE_URLS, ModelConfig, ModelKind, Provider
from apps.tenants.models import Hotel

# Source ``provider`` string -> ASHOS provider code.
PROVIDER_MAP: dict[str, str] = {
    "anthropic": Provider.ANTHROPIC,
    "claude": Provider.ANTHROPIC,
    "gemini": Provider.GEMINI,
    "google": Provider.GOOGLE,
    "googleai": Provider.GEMINI,
    "openai": Provider.OPENAI,
    "chatgpt": Provider.OPENAI,
    "azure": Provider.AZURE_OPENAI,
    "azure_openai": Provider.AZURE_OPENAI,
    "azure_speech": Provider.AZURE_SPEECH,
    "moonshot": Provider.MOONSHOT,
    "kimi": Provider.MOONSHOT,
    "zai": Provider.ZAI,
    "z.ai": Provider.ZAI,
    "groq": Provider.GROQ,
    "openrouter": Provider.OPENROUTER,
    "local": Provider.LOCAL,
    "ollama": Provider.LOCAL,
    "": Provider.OTHER,
}

# Model-name fragments that reveal what a credential is actually for.
KIND_HINTS: tuple[tuple[str, str], ...] = (
    ("embedding", ModelKind.EMBEDDING),
    ("embed", ModelKind.EMBEDDING),
    ("whisper", ModelKind.STT),
    ("transcribe", ModelKind.STT),
    ("tts", ModelKind.TTS),
    ("speech", ModelKind.TTS),
    ("voice", ModelKind.TTS),
    ("clip", ModelKind.IMAGE_EMBEDDING),
    ("ocr", ModelKind.OCR),
)

TRUTHY = {"1", "true", "t", "yes", "y"}


class Command(BaseCommand):
    help = "Import an api_integrations export (CSV or JSON) into AI Center."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("path", help="CSV or JSON export of api_integrations.")
        parser.add_argument("--hotel", required=True, help="Target hotel code, e.g. GLH-001.")
        parser.add_argument("--source", default="pos", help="Label for external_ref prefix.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would change, write nothing."
        )
        parser.add_argument(
            "--include-deleted",
            action="store_true",
            help="Import rows flagged is_deleted in the source.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        hotel = Hotel.all_objects.filter(code=options["hotel"].upper()).first()
        if hotel is None:
            raise CommandError(f"Unknown hotel code: {options['hotel']}")

        rows = self._load(path)
        if not rows:
            raise CommandError("No rows found in the export.")

        created = updated = skipped = 0
        notes: list[str] = []

        for row in rows:
            if _bool(row.get("is_deleted")) and not options["include_deleted"]:
                skipped += 1
                continue

            key = _text(row.get("api_key"))
            if not key:
                skipped += 1
                notes.append(f"row {row.get('id')}: no api_key, skipped")
                continue

            provider = PROVIDER_MAP.get(_text(row.get("provider")).lower())
            model_name = _text(row.get("api_model"))

            if provider is None:
                provider = Provider.OTHER
                notes.append(
                    f"row {row.get('id')}: unknown provider "
                    f"{row.get('provider')!r} -> 'other'; set it by hand"
                )

            kind, inferred = _infer_kind(model_name)
            if inferred:
                notes.append(
                    f"row {row.get('id')}: capability inferred as {kind} from "
                    f"model name {model_name!r} — confirm before activating"
                )

            ref = f"{options['source']}:{row.get('id')}"
            defaults = {
                "tenant": hotel,
                "kind": kind,
                "name": (_text(row.get("label")) or model_name or provider)[:80],
                "provider": provider,
                "model_name": model_name[:120],
                "api_key": key,
                "base_url": _text(row.get("base_url")) or DEFAULT_BASE_URLS.get(provider, ""),
                "extra": _json(row.get("extra_config")),
                # Never inherit live status from another system.
                "is_active": False,
                "is_default": False,
            }

            if options["dry_run"]:
                exists = ModelConfig.all_objects.filter(external_ref=ref).exists()
                created, updated = (created, updated + 1) if exists else (created + 1, updated)
                self.stdout.write(
                    f"  {'update' if exists else 'create'}  {defaults['name']:<22}"
                    f"{provider:<18}{kind:<10}{_mask(key)}"
                )
                continue

            _, is_new = ModelConfig.all_objects.update_or_create(
                external_ref=ref, defaults=defaults
            )
            created, updated = (created + 1, updated) if is_new else (created, updated + 1)
            self.stdout.write(
                f"  {'created' if is_new else 'updated'}  {defaults['name']:<22}"
                f"{provider:<18}{kind:<10}{_mask(key)}"
            )

        self.stdout.write("")
        for note in notes:
            self.stdout.write(self.style.WARNING(f"  ! {note}"))

        summary = f"{created} created, {updated} updated, {skipped} skipped"
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"\nDRY RUN — nothing written. {summary}"))
            return

        self.stdout.write(self.style.SUCCESS(f"\nImported into {hotel}. {summary}"))
        self.stdout.write(
            "  Everything landed INACTIVE and non-default, on purpose.\n"
            "  Next: /admin/ai_center/modelconfig/ -> select rows -> "
            "'Test connection' -> then activate and set one default per capability."
        )
        self.stdout.write(
            self.style.WARNING(
                "  If these keys were ever pasted into a chat, an email or a ticket, "
                "rotate them at the provider before activating."
            )
        )

    # ---------------------------------------------------------------- loading

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        text = path.read_text(encoding="utf-8-sig")

        if path.suffix.lower() == ".json":
            data = json.loads(text)
            return data if isinstance(data, list) else data.get("rows", [])

        # Sniff the delimiter: SQL Server exports are as often tab as comma.
        sample = text[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel_tab if "\t" in sample else csv.excel

        return [
            {(k or "").strip().lower(): v for k, v in row.items()}
            for row in csv.DictReader(text.splitlines(), dialect=dialect)
        ]


def _infer_kind(model_name: str) -> tuple[str, bool]:
    lowered = (model_name or "").lower()
    for fragment, kind in KIND_HINTS:
        if fragment in lowered:
            return kind, True
    # Chat is the overwhelming majority in these tables, and it is the one an
    # operator would notice immediately if wrong.
    return ModelKind.LLM, False


#: A CSV export of a SQL Server table writes real NULLs as the literal text
#: "NULL". Left unhandled it becomes a base URL of "NULL" and every call to that
#: provider 404s against a nonsense host.
NULL_TOKENS = {"", "null", "none", "nan", "\\n"}


def _text(value: Any) -> str:
    cleaned = str(value or "").strip()
    return "" if cleaned.lower() in NULL_TOKENS else cleaned


def _bool(value: Any) -> bool:
    return _text(value).lower() in TRUTHY


def _json(value: Any) -> dict:
    if not _text(value):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {"raw": str(value)[:500]}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]} ({len(key)} chars)" if len(key) > 8 else "****"
