"""Per-hotel RBAC (goal.txt §2.2, D06).

Being a manager at one property must never grant rights at another.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from apps.accounts.backends import invalidate_permission_cache
from apps.accounts.models import Role, RoleCode
from apps.ai_center.models import ModelConfig
from apps.core.context import set_request_context
from apps.tenants.models import HotelMembership

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def view_modelconfig() -> Permission:
    ct = ContentType.objects.get_for_model(ModelConfig)
    return Permission.objects.get(content_type=ct, codename="view_modelconfig")


@pytest.fixture
def manager_role(db, view_modelconfig) -> Role:
    role = Role.objects.create(code=RoleCode.MANAGER, name="Manager", is_system=True)
    role.permissions.add(view_modelconfig)
    return role


class TestPerHotelPermissions:
    def test_role_permission_applies_in_its_own_hotel(self, user, hotel, manager_role):
        HotelMembership.objects.create(user=user, hotel=hotel, role=manager_role)
        set_request_context(tenant_id=str(hotel.pk))
        invalidate_permission_cache(str(user.pk), str(hotel.pk))

        assert user.has_perm("ai_center.view_modelconfig")

    def test_permission_does_not_leak_to_another_hotel(
        self, user, hotel, other_hotel, manager_role
    ):
        HotelMembership.objects.create(user=user, hotel=hotel, role=manager_role)

        set_request_context(tenant_id=str(other_hotel.pk))
        invalidate_permission_cache(str(user.pk), str(other_hotel.pk))

        assert not user.has_perm("ai_center.view_modelconfig")

    def test_no_membership_means_no_permission(self, user, hotel):
        set_request_context(tenant_id=str(hotel.pk))
        invalidate_permission_cache(str(user.pk), str(hotel.pk))
        assert not user.has_perm("ai_center.view_modelconfig")

    def test_inactive_role_grants_nothing(self, user, hotel, manager_role):
        HotelMembership.objects.create(user=user, hotel=hotel, role=manager_role)
        manager_role.is_active = False
        manager_role.save(update_fields=["is_active", "updated_at"])

        set_request_context(tenant_id=str(hotel.pk))
        invalidate_permission_cache(str(user.pk), str(hotel.pk))

        assert not user.has_perm("ai_center.view_modelconfig")


class TestAuditTrail:
    def test_login_is_recorded(self, client, receptionist):
        from apps.accounts.models import AuditAction, AuditLog

        client.login(username=receptionist.email, password="test-pass-12345")
        entry = AuditLog.objects.filter(action=AuditAction.LOGIN).first()
        assert entry is not None
        assert receptionist.email in entry.summary

    def test_secrets_are_redacted(self):
        from apps.accounts.audit import _scrub

        scrubbed = _scrub({"api_key": "sk-live-123", "room": "101"})
        assert scrubbed["api_key"] == "<redacted>"
        assert scrubbed["room"] == "101"
