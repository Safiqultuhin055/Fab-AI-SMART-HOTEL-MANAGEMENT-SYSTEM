"""Querysets and managers used by every domain model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

from apps.core.context import current_tenant_id

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable


class SoftDeleteQuerySet(models.QuerySet):
    """Deletes are logical by default.

    Hotels dispute charges months later and auditors ask for records that staff
    "removed". A hard DELETE destroys the evidence, so ``delete()`` marks rows
    instead. ``hard_delete()`` stays available for GDPR erasure requests, which
    is the one case where the row genuinely must disappear.
    """

    def delete(self):  # type: ignore[override]
        return self.update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()

    def alive(self) -> SoftDeleteQuerySet:
        return self.filter(is_deleted=False)

    def dead(self) -> SoftDeleteQuerySet:
        return self.filter(is_deleted=True)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):  # type: ignore[misc]
    """Default manager: hides soft-deleted rows."""

    def get_queryset(self) -> SoftDeleteQuerySet:
        return super().get_queryset().filter(is_deleted=False)


class AllObjectsManager(models.Manager.from_queryset(SoftDeleteQuerySet)):  # type: ignore[misc]
    """Escape hatch: includes soft-deleted rows. Admin and audit views only."""


class TenantQuerySet(SoftDeleteQuerySet):
    def for_tenant(self, tenant_id: str | None) -> TenantQuerySet:
        if not tenant_id:
            return self.none()
        return self.filter(tenant_id=tenant_id)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):  # type: ignore[misc]
    """Tenant-scoped default manager.

    Scoping is applied here rather than in each view because a single forgotten
    ``.filter(tenant=...)`` in a list endpoint leaks one hotel's guest list to
    another hotel. Defaulting to safe and requiring ``unscoped()`` to opt out
    inverts the failure mode: you leak only on purpose.
    """

    def get_queryset(self) -> TenantQuerySet:
        qs = super().get_queryset().filter(is_deleted=False)
        tenant_id = current_tenant_id()
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        return qs

    def unscoped(self) -> TenantQuerySet:
        """All tenants. Cross-tenant reporting, migrations, superuser tooling."""
        return TenantQuerySet(self.model, using=self._db).filter(is_deleted=False)

    def bulk_create_for(self, tenant_id: str, objs: Iterable[models.Model], **kwargs):
        for obj in objs:
            obj.tenant_id = tenant_id  # type: ignore[attr-defined]
        return super().bulk_create(objs, **kwargs)  # type: ignore[arg-type]
