"""Tenant scoping is a security boundary, so it gets negative tests.

The failure this guards against is not theoretical: one missing ``.filter()``
in a list view exposes one hotel's guest list to another hotel's staff.
"""

from __future__ import annotations

import pytest

from apps.ai_center.models import ModelConfig, ModelKind
from apps.core.context import clear_request_context, set_request_context

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def configs(hotel, other_hotel):
    a = ModelConfig.all_objects.create(
        tenant=hotel, kind=ModelKind.LLM, name="A", model_name="m-a", is_default=True
    )
    b = ModelConfig.all_objects.create(
        tenant=other_hotel, kind=ModelKind.LLM, name="B", model_name="m-b", is_default=True
    )
    return a, b


class TestTenantManager:
    def test_default_manager_scopes_to_ambient_tenant(self, hotel, configs):
        set_request_context(tenant_id=str(hotel.pk))
        names = set(ModelConfig.objects.values_list("name", flat=True))
        assert names == {"A"}

    def test_other_tenant_sees_only_its_own(self, other_hotel, configs):
        set_request_context(tenant_id=str(other_hotel.pk))
        assert set(ModelConfig.objects.values_list("name", flat=True)) == {"B"}

    def test_no_tenant_context_returns_everything_for_platform_tools(self, configs):
        """Without a tenant bound, the manager cannot scope — callers must be
        platform-level (migrations, cron). Documented rather than silent."""
        clear_request_context()
        assert ModelConfig.objects.count() == 2

    def test_unscoped_is_explicit(self, hotel, configs):
        set_request_context(tenant_id=str(hotel.pk))
        assert ModelConfig.objects.unscoped().count() == 2

    def test_save_inherits_ambient_tenant(self, hotel):
        set_request_context(tenant_id=str(hotel.pk))
        config = ModelConfig(kind=ModelKind.STT, name="C", model_name="whisper")
        config.save()
        assert str(config.tenant_id) == str(hotel.pk)


class TestSoftDelete:
    def test_delete_hides_but_keeps_the_row(self, hotel, configs):
        set_request_context(tenant_id=str(hotel.pk))
        config = ModelConfig.objects.get(name="A")
        config.delete()

        assert not ModelConfig.objects.filter(name="A").exists()
        assert ModelConfig.all_objects.filter(name="A", is_deleted=True).exists()

    def test_restore_brings_it_back(self, hotel, configs):
        set_request_context(tenant_id=str(hotel.pk))
        config = ModelConfig.objects.get(name="A")
        config.delete()
        config.restore()
        assert ModelConfig.objects.filter(name="A").exists()
