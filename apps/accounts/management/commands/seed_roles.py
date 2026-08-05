"""Create/refresh the system roles and their permission grants.

Idempotent: safe to run on every deploy, and it must be re-run after any
migration that adds models, because permissions only exist once their model
does.

Two kinds of grant, deliberately separate:

*Module access* (``core.access_*``) decides which top-level menu items a role
can open. Coarse, stable, and the thing an operator actually thinks about.

*Model permissions* decide what can be done inside a module. Expressed as
``app_label -> allowed actions`` rather than a hand-listed codename dump, so a
role keeps working when a new model lands in an app it already owns.

Run:  python manage.py seed_roles
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import Role, RoleCode

ALL = ("add", "change", "delete", "view")
READ = ("view",)
WRITE = ("add", "change", "view")

# Every module key that has a core.access_<key> permission.
MODULES = (
    "reception",
    "guests",
    "rooms",
    "reservations",
    "housekeeping",
    "restaurant",
    "billing",
    "ai_center",
    "reports",
    "settings",
)

# role -> module keys the role may open ("*" = all of them)
MODULE_ACCESS: dict[str, tuple[str, ...]] = {
    RoleCode.SUPERADMIN: ("*",),
    RoleCode.ADMIN: ("*",),
    RoleCode.MANAGER: (
        "reception",
        "guests",
        "rooms",
        "reservations",
        "housekeeping",
        "restaurant",
        "billing",
        "ai_center",
        "reports",
    ),
    RoleCode.STAFF: ("guests", "rooms", "reservations", "housekeeping", "restaurant"),
    RoleCode.AI_RECEPTION: (
        "reception",
        "guests",
        "rooms",
        "reservations",
        "billing",
        # Read-only. The person standing at the desk when a guest says "the robot
        # is not answering" needs to see whether the concierge is online, which
        # provider is failing and what it cost — without waiting for a manager.
        # Credentials are masked on the page and the role has no change rights,
        # so seeing the status does not mean touching the keys.
        "ai_center",
    ),
}

# role -> {app_label: allowed actions}. "*" is the fallback for unlisted apps.
ROLE_MATRIX: dict[str, dict[str, tuple[str, ...]]] = {
    RoleCode.SUPERADMIN: {"*": ALL},
    RoleCode.ADMIN: {"*": ALL, "tenants": WRITE},
    RoleCode.MANAGER: {
        "tenants": READ,
        "accounts": READ,
        "guests": ALL,
        "rooms": ALL,
        "booking": ALL,
        "housekeeping": ALL,
        "restaurant": ALL,
        "billing": ALL,
        "reception": ALL,
        "ai_center": WRITE,
        "rag": WRITE,
        "vector_search": READ,
        "vision": READ,
        "notifications": WRITE,
        "dashboard": READ,
        "core": READ,
    },
    RoleCode.STAFF: {
        "guests": WRITE,
        "rooms": READ,
        "booking": WRITE,
        "housekeeping": ALL,
        "restaurant": ALL,
        "billing": READ,
        "dashboard": READ,
    },
    RoleCode.AI_RECEPTION: {
        "reception": ALL,
        "guests": WRITE,
        "rooms": READ,
        "booking": WRITE,
        "billing": WRITE,
        "vision": WRITE,
        # View only, deliberately. A receptionist should be able to see that the
        # concierge is degraded; editing an API key or a cost cap from the front
        # desk is not front-desk work.
        "ai_center": READ,
        "rag": READ,
        "vector_search": READ,
        "notifications": WRITE,
        "housekeeping": READ,
        "restaurant": READ,
        "dashboard": READ,
    },
}

DESCRIPTIONS = {
    RoleCode.SUPERADMIN: (
        "Platform owner. Every module, every hotel, including user management, "
        "the AI kill switch and biometric settings."
    ),
    RoleCode.ADMIN: (
        "Property administrator. Full operational and AI control for their hotel; "
        "cannot delete the hotel record itself."
    ),
    RoleCode.MANAGER: (
        "General manager. Operations, billing, AI configuration and reports. "
        "Read-only on users and hotel settings."
    ),
    RoleCode.STAFF: (
        "Operational staff: housekeeping, restaurant and front-desk support. "
        "Task queues and orders, no financial or AI configuration."
    ),
    RoleCode.AI_RECEPTION: (
        "Front desk and the AI reception kiosk. Guests, bookings, check-in/out, "
        "folio postings, document scanning and face check-in. Can see AI Center "
        "status but not change any AI configuration."
    ),
}

# Never granted to anyone but a superuser: deleting audit rows would defeat the
# purpose of having them.
FORBIDDEN = {("accounts", "delete_auditlog")}


class Command(BaseCommand):
    help = "Create or refresh ASHOS system roles and their permissions."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Remove permissions no longer granted by the matrix.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        prune: bool = options["prune"]
        all_perms = list(Permission.objects.select_related("content_type"))
        empty: list[str] = []

        for code, matrix in ROLE_MATRIX.items():
            role, created = Role.objects.update_or_create(
                code=code,
                defaults={
                    "name": RoleCode(code).label,
                    "description": DESCRIPTIONS[code],
                    "is_system": True,
                    "is_active": True,
                },
            )
            wanted = self._resolve(all_perms, matrix, MODULE_ACCESS[code])

            if prune:
                role.permissions.set(wanted)
            else:
                role.permissions.add(*wanted)

            modules = len([p for p in wanted if p.codename.startswith("access_")])
            verb = "created" if created else "updated"
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {verb:<8} {RoleCode(code).label:<14} "
                    f"{len(wanted):>4} permissions  ({modules} modules)"
                )
            )
            if not wanted:
                empty.append(str(RoleCode(code).label))

        self.stdout.write(self.style.SUCCESS("Roles seeded."))

        if empty:
            self.stdout.write(
                self.style.WARNING(
                    f"\n  {len(empty)} role(s) resolved to zero permissions: "
                    f"{', '.join(empty)}.\n"
                    "  Re-run `seed_roles` after every migration that adds models."
                )
            )

    @staticmethod
    def _resolve(
        all_perms: list[Permission],
        matrix: dict[str, tuple[str, ...]],
        modules: tuple[str, ...],
    ) -> list[Permission]:
        granted: list[Permission] = []
        wildcard = matrix.get("*")
        allowed_modules = set(MODULES) if "*" in modules else set(modules)

        for perm in all_perms:
            app = perm.content_type.app_label
            codename = perm.codename

            if (app, codename) in FORBIDDEN:
                continue

            # Module access is granted explicitly, never by the app wildcard —
            # otherwise "manager owns app core" would silently unlock Settings.
            if codename.startswith("access_"):
                if app == "core" and codename.removeprefix("access_") in allowed_modules:
                    granted.append(perm)
                continue

            actions = matrix.get(app, wildcard)
            if actions and codename.split("_", 1)[0] in actions:
                granted.append(perm)

        return granted
