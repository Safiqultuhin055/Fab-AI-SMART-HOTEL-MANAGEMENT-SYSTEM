"""Collapse the eight-role vocabulary into five.

``HotelMembership.role`` is PROTECT, so the old rows cannot simply be deleted —
memberships have to be repointed first. Mapping:

    owner       -> superadmin
    admin       -> admin        (unchanged)
    manager     -> manager      (unchanged)
    reception   -> ai_reception
    housekeeping | restaurant | accountant | readonly -> staff

Reversible: the merge into ``staff`` cannot be undone (the original department
is not recorded anywhere), so the reverse only restores the renames and leaves
staff in place. Documented rather than silently lossy.
"""

from __future__ import annotations

from django.db import migrations

RENAMES = {
    "owner": ("superadmin", "Super Admin"),
    "reception": ("ai_reception", "AI Reception"),
}

MERGE_INTO_STAFF = ("housekeeping", "restaurant", "accountant", "readonly")


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Membership = apps.get_model("tenants", "HotelMembership")

    for old_code, (new_code, new_name) in RENAMES.items():
        old = Role.objects.filter(code=old_code).first()
        if old is None:
            continue
        existing = Role.objects.filter(code=new_code).exclude(pk=old.pk).first()
        if existing:
            Membership.objects.filter(role=old).update(role=existing)
            old.delete()
        else:
            old.code = new_code
            old.name = new_name
            old.save(update_fields=["code", "name"])

    merged = list(Role.objects.filter(code__in=MERGE_INTO_STAFF))
    if not merged and not Role.objects.filter(code="staff").exists():
        return

    staff, _ = Role.objects.get_or_create(
        code="staff",
        defaults={
            "name": "Staff",
            "description": "Operational staff: housekeeping, restaurant, front-desk support.",
            "is_system": True,
            "is_active": True,
        },
    )
    for role in merged:
        Membership.objects.filter(role=role).update(role=staff)
        role.permissions.clear()
        role.delete()


def backwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for old_code, (new_code, _name) in RENAMES.items():
        role = Role.objects.filter(code=new_code).first()
        if role:
            role.code = old_code
            role.save(update_fields=["code"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_initial"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
