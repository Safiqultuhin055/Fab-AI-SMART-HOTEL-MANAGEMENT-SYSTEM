"""Generate demo data for everything the schema currently supports.

    python manage.py seed_demo
    python manage.py seed_demo --flush --ai-days 30 --seed 7

Scope note, stated plainly: this seeds **tenants, accounts and ai_center**,
because those are the only domain tables that exist in Phase 0. Rooms, guests,
reservations, folios and housekeeping tasks have no models yet (goal.txt §5,
Phase 1), so there is nothing to seed for them. This command grows with the
schema rather than pretending.

Two properties that matter:

*Deterministic.* ``--seed`` fixes the RNG, so the same invocation produces the
same database. Screenshots, demos and bug reports stay reproducible.

*Idempotent.* Re-running does not duplicate users or hotels. Only the volume
tables (AI usage, audit) append, and ``--flush`` clears exactly what this
command created — never a hand-made property.
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import AuditLog, Role, RoleCode
from apps.ai_center.models import (
    ModelConfig,
    PromptTemplate,
    PromptVersion,
    SafetyPolicy,
    UsageLog,
)
from apps.core.demo_data import (
    AI_CALL_SHAPES,
    AI_ERROR_CODES,
    AUDIT_EVENTS,
    BANGLA_NAMES,
    DEMO_EMPLOYEE_PREFIX,
    DEMO_HOTELS,
    DEMO_PASSWORD,
    INTERNATIONAL_NAMES,
    MODEL_PRICING,
)
from apps.core.utils import uuid7
from apps.tenants.models import Hotel, HotelMembership

User = get_user_model()

# Superadmin is deliberately absent: it is a platform account, created once by
# bootstrap_hotel, not something a demo should mint copies of per property.
STAFF_ROLES = (
    RoleCode.ADMIN,
    RoleCode.MANAGER,
    RoleCode.STAFF,
    RoleCode.AI_RECEPTION,
)


class Command(BaseCommand):
    help = "Seed realistic demo data for the tables that currently exist."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility.")
        parser.add_argument(
            "--staff-per-role", type=int, default=2, help="Staff users per role per hotel."
        )
        parser.add_argument("--ai-days", type=int, default=14, help="Days of AI usage history.")
        parser.add_argument(
            "--calls-per-day", type=int, default=120, help="Average AI calls per hotel per day."
        )
        parser.add_argument("--audit", type=int, default=150, help="Audit entries to generate.")
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete previously seeded demo data first (destructive, scoped).",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        rng = random.Random(options["seed"])  # noqa: S311 - reproducible fixtures, not crypto

        base_hotel = Hotel.all_objects.filter(code="GLH-001").first()
        if base_hotel is None:
            raise CommandError(
                "No hotel found. Run `python manage.py bootstrap_hotel` first — seed_demo "
                "adds to an initialised install, it does not replace it."
            )

        if options["flush"]:
            self._flush()

        roles = self._roles()
        hotels = [base_hotel, *self._hotels()]

        self._clone_ai_models(base_hotel, hotels)
        self._price_models(hotels)
        self._safety(hotels)
        self._staff(rng, hotels, roles, options["staff_per_role"])
        self._prompt_draft()
        usage = self._ai_usage(rng, hotels, options["ai_days"], options["calls_per_day"])
        self._audit(rng, hotels, options["audit"])

        self._report(hotels, usage)

    # ------------------------------------------------------------------ flush

    def _flush(self) -> None:
        """Remove only what this command creates.

        Hotels are matched by their seeded codes and users by the DEMO-
        employee-code prefix, so an operator's real property and real staff
        survive a demo reset. Deletion is hard, not soft: leftover soft-deleted
        demo rows would keep colliding with the unique constraints on re-seed.
        """
        codes = [spec["code"] for spec in DEMO_HOTELS]

        # ``delete()`` returns (total_including_cascades, per_model_counts).
        # Reporting the total would overstate every line, so read the per-model
        # entry instead.
        def count(result: tuple[int, dict[str, int]], label: str) -> int:
            return result[1].get(label, 0)

        usage = count(UsageLog.objects.all().delete(), "ai_center.UsageLog")
        audit = count(AuditLog.objects.all().delete(), "accounts.AuditLog")
        users = count(
            User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX).delete(),
            "accounts.User",
        )
        hotels = count(Hotel.all_objects.filter(code__in=codes).hard_delete(), "tenants.Hotel")

        self.stdout.write(
            self.style.WARNING(
                f"  flushed   {hotels} hotels · {users} users · {usage} usage · {audit} audit"
            )
        )

    # ------------------------------------------------------------------ steps

    def _roles(self) -> dict[str, Role]:
        roles = {role.code: role for role in Role.objects.all()}
        missing = [code for code in STAFF_ROLES if code not in roles]
        if missing:
            raise CommandError(
                f"Missing system roles: {', '.join(missing)}. Run `python manage.py seed_roles`."
            )
        return roles

    def _hotels(self) -> list[Hotel]:
        created: list[Hotel] = []
        for spec in DEMO_HOTELS:
            hotel, _ = Hotel.all_objects.get_or_create(
                code=str(spec["code"]),
                defaults={
                    "name": spec["name"],
                    "slug": slugify(str(spec["name"])),
                    "city": spec["city"],
                    "total_rooms": spec["rooms"],
                    "star_rating": spec["stars"],
                    "accent_color": spec["accent"],
                    "plan": spec["plan"],
                    "country": "BD",
                    "currency": "BDT",
                    "timezone": "Asia/Dhaka",
                },
            )
            created.append(hotel)
        self.stdout.write(f"  hotels    {len(created)} demo properties + GLH-001")
        return created

    def _clone_ai_models(self, source: Hotel, hotels: list[Hotel]) -> None:
        """Every hotel needs its own AI configuration.

        ``bootstrap_hotel`` only configures the property it creates. Without
        this, the extra demo hotels have no ModelConfig, so their usage rows
        price at zero and the multi-tenant cost view looks broken rather than
        empty.
        """
        templates = list(ModelConfig.all_objects.filter(tenant=source, is_default=True))
        created = 0
        for hotel in hotels:
            if hotel.pk == source.pk:
                continue
            for template in templates:
                _, is_new = ModelConfig.all_objects.get_or_create(
                    tenant=hotel,
                    kind=template.kind,
                    is_default=True,
                    defaults={
                        "name": template.name,
                        "provider": template.provider,
                        "base_url": template.base_url,
                        "api_key": template.api_key,
                        "model_name": template.model_name,
                        "temperature": template.temperature,
                        "max_tokens": template.max_tokens,
                        "timeout_s": template.timeout_s,
                        "dimension": template.dimension,
                    },
                )
                created += int(is_new)
        self.stdout.write(f"  ai config {created} model configs cloned to demo hotels")

    def _price_models(self, hotels: list[Hotel]) -> None:
        """Give every model config a real price.

        Zero-cost configs make the AI Center cost panel, the budget cap and the
        hourly rollup task all look like they work when in fact they can never
        fire (goal.txt R3).
        """
        updated = 0
        for hotel in hotels:
            for config in ModelConfig.all_objects.filter(tenant=hotel):
                inp, out = MODEL_PRICING.get(config.kind, ("0", "0"))
                config.cost_per_1k_input_usd = Decimal(inp)
                config.cost_per_1k_output_usd = Decimal(out)
                config.save(
                    update_fields=[
                        "cost_per_1k_input_usd",
                        "cost_per_1k_output_usd",
                        "updated_at",
                    ]
                )
                updated += 1
        self.stdout.write(f"  pricing   {updated} model configs priced")

    def _safety(self, hotels: list[Hotel]) -> None:
        for hotel in hotels:
            SafetyPolicy.all_objects.get_or_create(
                tenant=hotel,
                defaults={
                    "blocked_topics": [
                        "medical advice",
                        "legal advice",
                        "other guests' details",
                    ],
                },
            )

    def _staff(
        self, rng: random.Random, hotels: list[Hotel], roles: dict[str, Role], per_role: int
    ) -> None:
        names = list(BANGLA_NAMES) + list(INTERNATIONAL_NAMES)
        rng.shuffle(names)
        pool = iter(names * 4)

        created = 0
        for hotel in hotels:
            slug = hotel.code.lower().replace("-", "")
            for role_code in STAFF_ROLES:
                for index in range(1, per_role + 1):
                    email = f"{role_code}{index}.{slug}@ashos.local"
                    user, is_new = User.objects.get_or_create(
                        email=email,
                        defaults={
                            "full_name": next(pool),
                            "employee_code": f"{DEMO_EMPLOYEE_PREFIX}{slug.upper()}-"
                            f"{role_code[:3].upper()}{index:02d}",
                            "phone": f"+8801{rng.randint(300000000, 999999999)}",
                            "preferred_language": rng.choice(["en", "bn", "bn"]),
                            "is_staff": role_code == RoleCode.MANAGER,
                        },
                    )
                    if is_new:
                        user.set_password(DEMO_PASSWORD)
                        user.save(update_fields=["password"])
                        created += 1

                    HotelMembership.all_objects.get_or_create(
                        hotel=hotel,
                        user=user,
                        defaults={"role": roles[role_code], "is_default": True},
                    )

        total = len(hotels) * len(STAFF_ROLES) * per_role
        self.stdout.write(f"  staff     {created} created, {total - created} existing")

    def _prompt_draft(self) -> None:
        """Add an inactive v2 so prompt rollback is demonstrable.

        A versioning UI with exactly one version per template proves nothing.
        """
        template = PromptTemplate.objects.filter(key="reception.system").first()
        if template is None or template.versions.filter(version=2).exists():
            return

        active = template.active_version
        PromptVersion.objects.create(
            template=template,
            tenant=None,
            version=2,
            system_prompt=(active.system_prompt if active else "")
            + "\n8. Offer a late checkout when the guest mentions a late flight.",
            user_template=active.user_template if active else "",
            notes="Draft: adds late-checkout upsell. Not activated — awaiting ai_eval score.",
            is_active=False,
            eval_score=None,
        )
        self.stdout.write("  prompts   reception.system v2 draft added (inactive)")

    def _ai_usage(self, rng: random.Random, hotels: list[Hotel], days: int, per_day: int) -> int:
        """Generate a plausible AI call history.

        Shaped, not uniform: a diurnal curve with a check-in peak around 15:00
        and a checkout peak around 11:00, weighted call types, a small error
        rate, occasional fallback and cache hits. The point is that the AI
        Center dashboards, the p95 latency figure and the daily cost rollup all
        have something realistic to render before Phase 2 wires them up.
        """
        now = timezone.now()
        pricing = {
            (str(c.tenant_id), c.kind): (c.cost_per_1k_input_usd, c.cost_per_1k_output_usd)
            for c in ModelConfig.all_objects.all()
        }
        shapes = list(AI_CALL_SHAPES)
        weights = [s[6] for s in shapes]

        rows: list[UsageLog] = []
        stamps: list[Any] = []

        for hotel in hotels:
            # Bigger properties do more AI work.
            volume = max(20, int(per_day * (hotel.total_rooms / 120)))
            for day in range(days):
                # Weekends are busier for resorts; keep it simple but non-flat.
                day_volume = int(volume * rng.uniform(0.65, 1.35))
                for _ in range(day_volume):
                    module, kind, p50, spread, in_tok, out_tok, _w = rng.choices(
                        shapes, weights=weights, k=1
                    )[0]

                    hour = self._diurnal_hour(rng)
                    created = now - timedelta(
                        days=day, hours=now.hour - hour, minutes=rng.randint(0, 59)
                    )

                    latency = max(40, int(rng.gauss(p50, spread / 2)))
                    input_tokens = int(in_tok * rng.uniform(0.6, 1.8)) if in_tok else 0
                    output_tokens = int(out_tok * rng.uniform(0.5, 2.0)) if out_tok else 0

                    cache_hit = kind == "embedding" and rng.random() < 0.18
                    if cache_hit:
                        latency = rng.randint(2, 15)
                        input_tokens = 0

                    success = rng.random() > 0.028
                    fallback = (not success) or rng.random() < 0.04
                    if not success:
                        latency = rng.randint(2000, 31000)
                        output_tokens = 0

                    rate_in, rate_out = pricing.get(
                        (str(hotel.pk), kind), (Decimal("0"), Decimal("0"))
                    )
                    cost = (
                        Decimal(input_tokens) / 1000 * rate_in
                        + Decimal(output_tokens) / 1000 * rate_out
                    ).quantize(Decimal("0.000001"))

                    rows.append(
                        UsageLog(
                            tenant=hotel,
                            module=module,
                            kind=kind,
                            provider="openai_compatible",
                            model_name=self._model_name(kind),
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost_usd=cost if success else Decimal("0"),
                            latency_ms=latency,
                            success=success,
                            error_code="" if success else rng.choice(AI_ERROR_CODES),
                            fallback_used=fallback and success,
                            cache_hit=cache_hit,
                            request_id=uuid7().hex[:16],
                            conversation_id=f"conv-{rng.randint(1000, 9999)}",
                        )
                    )
                    stamps.append(created)

        created = UsageLog.objects.bulk_create(rows, batch_size=1000)

        # auto_now_add stamps every row with "now" on insert, so a second pass
        # rewrites the timestamps and the history actually spans days.
        # bulk_update does not call pre_save, so the value sticks.
        for obj, stamp in zip(created, stamps, strict=True):
            obj.created_at = stamp
        UsageLog.objects.bulk_update(created, ["created_at"], batch_size=1000)

        self.stdout.write(f"  ai usage  {len(rows)} calls across {days} days")
        return len(rows)

    def _audit(self, rng: random.Random, hotels: list[Hotel], count: int) -> None:
        actors = list(User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX)[:40])
        if not actors:
            return

        now = timezone.now()
        rows: list[AuditLog] = []
        stamps: list[Any] = []

        for _ in range(count):
            actor = rng.choice(actors)
            action, template = rng.choice(AUDIT_EVENTS)
            rows.append(
                AuditLog(
                    actor=actor,
                    actor_label=actor.email,
                    hotel=rng.choice(hotels),
                    action=action,
                    object_type=rng.choice(["Hotel", "User", "ModelConfig", ""]),
                    object_id=str(uuid7()),
                    summary=template.format(actor=actor.email),
                    changes={"field": ["old", "new"]} if action == "update" else {},
                    ip_address=f"103.{rng.randint(1, 250)}.{rng.randint(1, 250)}."
                    f"{rng.randint(2, 250)}",
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) ASHOS-Staff",
                    request_id=uuid7().hex[:16],
                )
            )
            stamps.append(now - timedelta(days=rng.randint(0, 20), minutes=rng.randint(0, 1439)))

        # AuditLog PKs are uuid7 defaults assigned at construction, so the same
        # objects can be updated straight after insert.
        AuditLog.objects.bulk_create(rows, batch_size=500)
        for obj, stamp in zip(rows, stamps, strict=True):
            obj.created_at = stamp
        AuditLog.objects.bulk_update(rows, ["created_at"], batch_size=500)

        self.stdout.write(f"  audit     {len(rows)} entries")

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _diurnal_hour(rng: random.Random) -> int:
        """Hotel AI traffic is not uniform: peaks at checkout and check-in."""
        bucket = rng.random()
        if bucket < 0.22:
            return rng.randint(10, 12)  # checkout rush
        if bucket < 0.55:
            return rng.randint(14, 18)  # check-in rush
        if bucket < 0.80:
            return rng.randint(19, 23)  # evening concierge questions
        return rng.randint(0, 9)  # night shift, thin

    @staticmethod
    def _model_name(kind: str) -> str:
        return {
            "llm": "gpt-4o-mini",
            "embedding": "text-embedding-3-small",
            "image_embedding": "ViT-B-32",
            "face": "buffalo_l",
            "stt": "whisper-1",
            "tts": "tts-1",
            "ocr": "paddleocr",
        }.get(kind, kind)

    def _report(self, hotels: list[Hotel], usage_count: int) -> None:
        from django.db.models import Avg, Count, Sum

        totals = UsageLog.objects.aggregate(
            cost=Sum("cost_usd"), latency=Avg("latency_ms"), calls=Count("id")
        )
        failures = UsageLog.objects.filter(success=False).count()

        staff = User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX).count()

        self.stdout.write(self.style.SUCCESS("\nDemo data ready."))
        self.stdout.write(f"  hotels        {len(hotels)}")
        self.stdout.write(f"  staff users   {staff}")
        self.stdout.write(f"  ai calls      {totals['calls']} ({usage_count} new)")
        self.stdout.write(f"  ai spend      ${totals['cost'] or 0:.4f}")
        self.stdout.write(f"  avg latency   {int(totals['latency'] or 0)} ms")
        self.stdout.write(f"  error rate    {failures / max(1, totals['calls']):.2%}")
        self.stdout.write(f"  audit entries {AuditLog.objects.count()}")
        self.stdout.write(f"\n  Staff login:  <role><n>.<hotelcode>@ashos.local / {DEMO_PASSWORD}")
        self.stdout.write(f"  Roles:        {' · '.join(str(r) for r in STAFF_ROLES)}")
        self.stdout.write("  Example:      ai_reception1.glh001@ashos.local")
