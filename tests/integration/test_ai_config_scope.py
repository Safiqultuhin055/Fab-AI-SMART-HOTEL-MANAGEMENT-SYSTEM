"""Which AI configuration a hotel actually runs on, and who may look at it.

The bug these pin: a hotel onboarded after the first one got a full set of
keyless placeholder rows, those rows were marked default, and because they were
default the resolver used them. Result — seven configured-looking rows in AI
Center and a kiosk saying "AI not configured", with nothing on screen to explain
the contradiction.

Two fixes are under test. A row with no credential is skipped rather than allowed
to shadow a working one, and a configuration can be platform-wide so a group
pastes its key once instead of once per property.
"""

from __future__ import annotations

import io

import pytest
from django.urls import reverse

from apps.accounts.models import RoleCode
from apps.ai_center.models import ModelConfig, ModelKind, Provider
from services.ai import gateway, registry

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture(autouse=True)
def _no_cached_resolution():
    """The resolver caches for 60s; a stale entry would make these pass or fail
    depending on test order."""
    registry.invalidate()
    yield
    registry.invalidate()


@pytest.fixture
def no_env_fallback(settings):
    """Remove the environment layer.

    Test settings pin every capability to the keyless ``fake`` provider, which is
    legitimately usable without a key — so a test about DB resolution has to take
    the env fallback away or it can never observe "nothing configured".
    """
    settings.AI = {
        **settings.AI,
        "LLM": {**settings.AI["LLM"], "provider": "openai_compatible", "api_key": ""},
    }
    registry.invalidate()
    return settings


def config(
    tenant,
    *,
    key="",
    default=True,
    provider=Provider.OPENAI_COMPATIBLE,
    kind=ModelKind.LLM,
    name="cfg",
):
    return ModelConfig.all_objects.create(
        tenant=tenant,
        kind=kind,
        name=name,
        provider=provider,
        model_name="gpt-4o-mini",
        api_key=key,
        is_default=default,
        is_active=True,
    )


# ==============================================================================
# Resolution
# ==============================================================================


class TestKeylessRowsDoNotShadow:
    def test_a_placeholder_row_does_not_make_a_hotel_look_configured(self, hotel, no_env_fallback):
        """The reported bug, exactly: seven rows on the page, kiosk offline."""
        config(hotel, key="")

        assert gateway.is_configured(str(hotel.pk)) is False

    def test_a_platform_key_is_used_when_the_hotel_has_only_a_placeholder(self, hotel):
        config(hotel, key="", name="seeded placeholder")
        config(None, key="sk-platform", name="group key")

        resolved = registry.resolve("llm", str(hotel.pk))
        assert resolved.api_key == "sk-platform"
        assert gateway.is_configured(str(hotel.pk)) is True

    def test_the_hotel_s_own_key_wins_over_the_platform_one(self, hotel):
        config(None, key="sk-platform", name="group key")
        config(hotel, key="sk-hotel", name="own key")

        assert registry.resolve("llm", str(hotel.pk)).api_key == "sk-hotel"

    def test_a_usable_non_default_row_beats_a_keyless_default(self, hotel):
        """An operator ticking "default" on the wrong row must not take reception
        offline."""
        config(hotel, key="", default=True, name="wrong default")
        config(hotel, key="sk-works", default=False, name="the one with a key")

        assert registry.resolve("llm", str(hotel.pk)).api_key == "sk-works"

    def test_a_keyless_provider_is_usable_without_a_key(self, hotel):
        """Local inference legitimately has no credential."""
        row = config(hotel, key="", provider=Provider.LOCAL, name="ollama")
        assert row.is_usable is True
        assert gateway.is_configured(str(hotel.pk)) is True

    def test_an_inactive_row_is_never_used(self, hotel):
        active = config(hotel, key="sk-live", default=False, name="live")
        dead = config(hotel, key="sk-dead", default=True, name="switched off")
        dead.is_active = False
        dead.save()

        assert registry.resolve("llm", str(hotel.pk)).api_key == active.api_key

    def test_falls_through_to_the_environment_when_nothing_is_configured(self, hotel, settings):
        """A fresh clone must answer before anyone opens the admin."""
        settings.AI = {**settings.AI, "LLM": {**settings.AI["LLM"], "api_key": "sk-env"}}
        registry.invalidate()
        assert registry.resolve("llm", str(hotel.pk)).api_key == "sk-env"

    def test_one_hotel_s_key_never_leaks_to_another(self, hotel, other_hotel, no_env_fallback):
        config(hotel, key="sk-only-theirs")

        assert registry.resolve("llm", str(other_hotel.pk)).api_key == ""


class TestPlatformRows:
    def test_only_one_platform_default_per_capability(self, hotel):
        """Postgres treats NULLs as distinct, so the per-hotel constraint does not
        cover platform rows at all."""
        config(None, key="sk-a", name="first")
        second = config(None, key="sk-b", name="second")

        second.refresh_from_db()
        assert (
            ModelConfig.all_objects.filter(
                tenant__isnull=True, kind=ModelKind.LLM, is_default=True
            ).count()
            == 1
        )

    def test_promoting_leaves_the_source_hotel_untouched(self, hotel):
        from django.core.management import call_command

        source = config(hotel, key="sk-hotel", name="Working key")
        call_command("promote_ai_config", hotel.code, "--kind", "llm", stdout=io.StringIO())

        source.refresh_from_db()
        assert source.tenant_id == hotel.pk
        assert source.api_key == "sk-hotel"

        platform = ModelConfig.all_objects.get(tenant__isnull=True, kind=ModelKind.LLM)
        assert platform.api_key == "sk-hotel"
        assert platform.external_ref == f"promoted:{hotel.code}:llm"
        # Verification belongs to the row that made the call, not to a copy.
        assert platform.last_verified_at is None

    def test_promotion_fixes_every_other_hotel(self, hotel, other_hotel, no_env_fallback):
        from django.core.management import call_command

        config(hotel, key="sk-hotel", name="Working key")
        config(other_hotel, key="", name="seeded placeholder")

        assert gateway.is_configured(str(other_hotel.pk)) is False
        call_command("promote_ai_config", hotel.code, "--kind", "llm", stdout=io.StringIO())
        assert gateway.is_configured(str(other_hotel.pk)) is True

    def test_a_dry_run_writes_nothing(self, hotel):
        from django.core.management import call_command

        config(hotel, key="sk-hotel")
        call_command("promote_ai_config", hotel.code, "--dry-run", stdout=io.StringIO())
        assert not ModelConfig.all_objects.filter(tenant__isnull=True).exists()


# ==============================================================================
# Who may see it
# ==============================================================================


@pytest.fixture
def seeded_roles(db):
    """The real shipped matrix, not hand-built roles.

    These tests are about what ``seed_roles`` grants, so building the roles by
    hand here would test the fixture instead of the product.
    """
    from django.core.management import call_command

    from apps.accounts.models import Role

    call_command("seed_roles", stdout=io.StringIO())
    return {role.code: role for role in Role.objects.all()}


@pytest.fixture
def staff_with_role(db, hotel, seeded_roles):
    from apps.accounts.backends import invalidate_permission_cache
    from apps.accounts.models import User
    from apps.core.context import set_request_context
    from apps.tenants.models import HotelMembership

    def make(code: str):
        user = User.objects.create_user(
            email=f"{code}@ashos.local", password="Demo@12345", full_name=str(code)
        )
        HotelMembership.objects.create(
            user=user, hotel=hotel, role=seeded_roles[code], is_default=True
        )
        set_request_context(tenant_id=str(hotel.pk))
        invalidate_permission_cache(str(user.pk), str(hotel.pk))
        return user

    return make


class TestWhoCanSeeAiCenter:
    @pytest.mark.parametrize("code", [RoleCode.SUPERADMIN, RoleCode.ADMIN, RoleCode.AI_RECEPTION])
    def test_the_page_opens(self, client, staff_with_role, code):
        """Front desk included: the person a guest complains to needs to be able
        to see whether the concierge is online."""
        client.force_login(staff_with_role(code))
        assert client.get(reverse("ai_center:home")).status_code == 200

    def test_staff_are_still_shut_out(self, client, staff_with_role):
        client.force_login(staff_with_role(RoleCode.STAFF))
        assert client.get(reverse("ai_center:home")).status_code == 403

    def test_reception_cannot_change_a_credential(self, staff_with_role):
        """Seeing the status is not the same as editing the keys."""
        user = staff_with_role(RoleCode.AI_RECEPTION)
        assert user.has_perm("core.access_ai_center") is True
        assert user.has_perm("ai_center.change_modelconfig") is False
        assert user.has_perm("ai_center.add_modelconfig") is False

    def test_admin_can(self, staff_with_role):
        user = staff_with_role(RoleCode.ADMIN)
        assert user.has_perm("ai_center.change_modelconfig") is True

    def test_the_page_hides_the_edit_links_from_a_read_only_role(
        self, client, staff_with_role, hotel
    ):
        config(hotel, key="sk-hotel", name="Group key")

        client.force_login(staff_with_role(RoleCode.AI_RECEPTION))
        body = client.get(reverse("ai_center:home")).content.decode()
        assert "Group key" in body
        assert "/admin/ai_center/modelconfig/add/" not in body

        client.force_login(staff_with_role(RoleCode.ADMIN))
        body = client.get(reverse("ai_center:home")).content.decode()
        assert "/admin/ai_center/modelconfig/add/" in body


class TestThePageExplainsItself:
    def test_it_says_where_each_capability_is_configured(self, client, staff_with_role, hotel):
        config(None, key="sk-platform", name="Group key")

        client.force_login(staff_with_role(RoleCode.ADMIN))
        body = client.get(reverse("ai_center:home")).content.decode()

        assert "What this hotel is running on" in body
        assert "platform-wide" in body

    def test_platform_rows_are_labelled_as_shared(self, client, staff_with_role, hotel):
        """Somebody editing one is changing every property, and should know
        before rather than after."""
        config(None, key="sk-platform", name="Group key")

        client.force_login(staff_with_role(RoleCode.ADMIN))
        body = client.get(reverse("ai_center:home")).content.decode()
        assert "all hotels" in body

    def test_a_full_key_is_never_rendered(self, client, staff_with_role, hotel):
        config(hotel, key="sk-super-secret-value-1234567890", name="Secret")

        client.force_login(staff_with_role(RoleCode.ADMIN))
        body = client.get(reverse("ai_center:home")).content.decode()
        assert "sk-super-secret-value-1234567890" not in body
