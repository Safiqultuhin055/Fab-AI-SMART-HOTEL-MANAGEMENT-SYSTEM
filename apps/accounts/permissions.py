"""Reusable DRF permission classes.

Every ASHOS endpoint gets an explicit permission class. The default
``IsAuthenticated`` in settings is a floor, not a policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework.permissions import SAFE_METHODS, BasePermission

if TYPE_CHECKING:  # pragma: no cover
    pass


class HasTenant(BasePermission):
    """Request must resolve to a hotel. Blocks accidental cross-tenant reads."""

    message = "No hotel is bound to this request."

    def has_permission(self, request, view) -> bool:
        return getattr(request, "tenant", None) is not None


class HasRolePermission(BasePermission):
    """Checks ``view.required_permission`` against the per-hotel role.

    Usage::

        class RoomViewSet(ModelViewSet):
            permission_classes = [IsAuthenticated, HasTenant, HasRolePermission]
            required_permission = "rooms.view_room"
            write_permission = "rooms.change_room"
    """

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True

        if request.method in SAFE_METHODS:
            required = getattr(view, "required_permission", "")
        else:
            required = getattr(view, "write_permission", "") or getattr(
                view, "required_permission", ""
            )
        return not required or user.has_perm(required)


class IsObjectTenant(BasePermission):
    """Object-level guard: the row must belong to the request's hotel.

    Tenant-scoped managers already filter list views; this closes the detail
    route, where an attacker supplies a UUID from another property.
    """

    message = "Object belongs to a different hotel."

    def has_object_permission(self, request, view, obj) -> bool:
        tenant = getattr(request, "tenant", None)
        obj_tenant_id = getattr(obj, "tenant_id", None)
        if obj_tenant_id is None:
            return True
        return tenant is not None and str(obj_tenant_id) == str(tenant.pk)


class ReadOnly(BasePermission):
    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS
