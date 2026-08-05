"""Authentication and per-hotel permission resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache

from apps.core.context import current_tenant_id

if TYPE_CHECKING:  # pragma: no cover
    from django.http import HttpRequest

CACHE_TTL = 300


class EmailBackend(ModelBackend):
    """Email + password login that refuses locked accounts.

    ``ModelBackend`` would happily authenticate a user who has just failed five
    passwords in a row; the lockout only means anything if the backend enforces
    it.
    """

    def authenticate(  # type: ignore[override]
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ):
        User = get_user_model()
        email = (username or kwargs.get("email") or "").strip().lower()
        if not email or not password:
            return None

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            # Hash anyway so response time does not reveal which emails exist.
            User().set_password(password)
            return None

        if user.is_locked:
            return None
        if not user.check_password(password) or not self.user_can_authenticate(user):
            return None
        return user


class RolePermissionBackend(ModelBackend):
    """Grants permissions from the user's role **in the current hotel**.

    Django's built-in backends resolve permissions globally. In a multi-property
    deployment that is wrong: being a manager at Hotel A must not grant manager
    rights at Hotel B. This backend intersects the permission lookup with the
    tenant bound to the current request.
    """

    def authenticate(self, request, **kwargs):  # noqa: ARG002
        return None  # authorisation only

    def get_all_permissions(self, user_obj, obj=None) -> set[str]:
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()

        tenant_id = current_tenant_id()
        cache_key = f"perms:{user_obj.pk}:{tenant_id or 'none'}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        perms = self._role_permissions(user_obj, tenant_id)
        cache.set(cache_key, perms, CACHE_TTL)
        return perms

    @staticmethod
    def _role_permissions(user_obj, tenant_id: str) -> set[str]:
        from apps.tenants.models import HotelMembership

        memberships = HotelMembership.objects.filter(user=user_obj, role__is_active=True)
        if tenant_id:
            memberships = memberships.filter(hotel_id=tenant_id)

        rows = (
            memberships.values_list(
                "role__permissions__content_type__app_label",
                "role__permissions__codename",
            )
            .distinct()
            .iterator()
        )
        return {f"{app}.{code}" for app, code in rows if app and code}


def invalidate_permission_cache(user_id: str, tenant_id: str | None = None) -> None:
    """Call after any role or membership change; stale grants are a security bug."""
    cache.delete(f"perms:{user_id}:{tenant_id or 'none'}")
    if tenant_id is None and hasattr(cache, "delete_pattern"):
        # Redis backend only: wipe every tenant variant for this user.
        cache.delete_pattern(f"perms:{user_id}:*")
