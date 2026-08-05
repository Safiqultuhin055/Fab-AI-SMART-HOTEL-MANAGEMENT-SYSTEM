"""Abstract base models.

Every concrete model in ASHOS inherits from ``BaseModel`` or
``TenantOwnedModel``. That gives the whole system, for free:
  * time-ordered UUID primary keys (safe to expose in URLs, index-friendly)
  * created_at / updated_at
  * soft delete
  * tenant ownership (goal.txt §2.2 — SaaS at the data layer from day one)
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.context import current_tenant_id
from apps.core.managers import (
    AllObjectsManager,
    SoftDeleteManager,
    TenantManager,
    TenantQuerySet,
)
from apps.core.utils import uuid7


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(_("created at"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(_("deleted"), default=False, db_index=True)
    deleted_at = models.DateTimeField(_("deleted at"), null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):  # type: ignore[override]
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(using=using, update_fields=["is_deleted", "deleted_at", "updated_at"])

    def hard_delete(self, using=None, keep_parents=False):
        """Physical removal. Reserved for right-to-erasure (goal.txt D10 #6)."""
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self) -> None:
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


class BaseModel(UUIDModel, TimeStampedModel, SoftDeleteModel):
    """Non-tenant-scoped base: platform-level tables only."""

    class Meta:
        abstract = True
        ordering = ("-created_at",)


class TenantOwnedModel(UUIDModel, TimeStampedModel, SoftDeleteModel):
    """Base for every row that belongs to one hotel.

    ``tenant`` is non-null and indexed. The default manager filters by the
    ambient tenant, so a view that forgets to scope returns that hotel's data
    rather than everyone's.
    """

    tenant = models.ForeignKey(
        "tenants.Hotel",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
        db_index=True,
        verbose_name=_("hotel"),
    )

    objects = TenantManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        # Fill the tenant from ambient context so service code never has to
        # remember it on create. An explicit tenant always wins.
        if not self.tenant_id:
            ambient = current_tenant_id()
            if ambient:
                self.tenant_id = ambient  # type: ignore[assignment]
        return super().save(*args, **kwargs)


class ActiveModel(models.Model):
    """Adds an enable/disable flag without deleting configuration rows."""

    is_active = models.BooleanField(_("active"), default=True, db_index=True)

    class Meta:
        abstract = True


class ModuleAccess(models.Model):
    """Permission carrier for top-level navigation. Has no table.

    A menu item needs a permission to gate on, but most ASHOS modules have no
    models yet, so there is nothing for Django to auto-generate permissions
    from. ``managed = False`` with an explicit ``permissions`` list creates the
    Permission rows without creating a table.

    Module access is a coarser question than model access anyway: "may this
    person open Housekeeping at all" is not the same as "may they change an
    hk_task", and roles are far easier to reason about at that level. Model
    permissions still apply inside each module.
    """

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = _("module access")
        verbose_name_plural = _("module access")
        permissions = [
            ("access_reception", _("Can open AI Reception")),
            ("access_guests", _("Can open Guests")),
            ("access_rooms", _("Can open Rooms & Inventory")),
            ("access_reservations", _("Can open Reservations")),
            ("access_housekeeping", _("Can open Housekeeping")),
            ("access_restaurant", _("Can open Restaurant & POS")),
            ("access_billing", _("Can open Billing & Finance")),
            ("access_ai_center", _("Can open AI Center")),
            ("access_reports", _("Can open Reports & Analytics")),
            ("access_settings", _("Can open Settings")),
        ]

    def __str__(self) -> str:  # pragma: no cover - never instantiated
        return "module access"


__all__ = [
    "ActiveModel",
    "BaseModel",
    "ModuleAccess",
    "SoftDeleteModel",
    "TenantOwnedModel",
    "TenantQuerySet",
    "TimeStampedModel",
    "UUIDModel",
]
