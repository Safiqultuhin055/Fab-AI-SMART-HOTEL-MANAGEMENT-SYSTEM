"""Every menu item must open, and only for roles that may open it.

Two failure modes this guards against:

*A dead menu.* An item that renders but does not navigate reads as a broken
application. Every ``NavItem.url_name`` must reverse and return 200 for a user
who holds its permission.

*A menu that only looks locked.* Hiding an item in the sidebar is a UX
affordance, not access control — anyone can type the URL. Each module view
therefore enforces the same permission, and that is asserted here.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import Role, RoleCode
from apps.core.navigation import NAVIGATION, QUICK_ACTIONS
from apps.tenants.models import HotelMembership

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

User = get_user_model()

# role -> modules that role may open. Mirrors seed_roles.MODULE_ACCESS; kept
# duplicated on purpose so a silent widening of a role fails a test.
EXPECTED_ACCESS = {
    RoleCode.SUPERADMIN: {
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
    },
    RoleCode.ADMIN: {
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
    },
    RoleCode.MANAGER: {
        "reception",
        "guests",
        "rooms",
        "reservations",
        "housekeeping",
        "restaurant",
        "billing",
        "ai_center",
        "reports",
    },
    RoleCode.STAFF: {"guests", "rooms", "reservations", "housekeeping", "restaurant"},
    RoleCode.AI_RECEPTION: {
        "reception",
        "guests",
        "rooms",
        "reservations",
        "billing",
        # Read-only: the front desk needs to see whether the concierge is online
        # when a guest complains. Write rights are checked separately, in
        # tests/integration/test_ai_config_scope.py.
        "ai_center",
    },
}


@pytest.fixture
def seeded_roles(db) -> dict[str, Role]:
    call_command("seed_roles", "--prune", stdout=StringIO())
    return {role.code: role for role in Role.objects.all()}


def make_user(email: str, hotel, role: Role):
    user = User.objects.create_user(email=email, password="test-pass-12345", full_name=email)
    HotelMembership.objects.create(user=user, hotel=hotel, role=role, is_default=True)
    return user


class TestEveryMenuItemResolves:
    def test_all_nav_urls_reverse(self):
        """A NavItem pointing at a non-existent route breaks the whole sidebar."""
        for item in NAVIGATION + QUICK_ACTIONS:
            assert reverse(item.url_name), f"{item.key}: {item.url_name} does not reverse"

    def test_superadmin_can_open_every_page(self, client, hotel, seeded_roles):
        user = make_user("sa@test.local", hotel, seeded_roles[RoleCode.SUPERADMIN])
        client.force_login(user)
        for item in NAVIGATION:
            response = client.get(reverse(item.url_name))
            assert response.status_code == 200, f"{item.key} returned {response.status_code}"


@pytest.mark.parametrize("role_code", list(EXPECTED_ACCESS))
class TestPerRoleAccess:
    def test_permitted_modules_open(self, client, hotel, seeded_roles, role_code):
        user = make_user(f"{role_code}@test.local", hotel, seeded_roles[role_code])
        client.force_login(user)

        for item in NAVIGATION:
            if item.key == "dashboard" or item.key not in EXPECTED_ACCESS[role_code]:
                continue
            response = client.get(reverse(item.url_name))
            assert response.status_code == 200, f"{role_code} blocked from {item.key}"

    def test_forbidden_modules_are_refused_at_the_url(self, client, hotel, seeded_roles, role_code):
        user = make_user(f"{role_code}-deny@test.local", hotel, seeded_roles[role_code])
        client.force_login(user)

        for item in NAVIGATION:
            if item.key == "dashboard" or item.key in EXPECTED_ACCESS[role_code]:
                continue
            response = client.get(reverse(item.url_name))
            assert response.status_code == 403, (
                f"{role_code} reached {item.key} without permission (got {response.status_code})"
            )

    def test_sidebar_shows_exactly_the_permitted_modules(
        self, client, hotel, seeded_roles, role_code
    ):
        user = make_user(f"{role_code}-nav@test.local", hotel, seeded_roles[role_code])
        client.force_login(user)
        page = client.get(reverse("dashboard:home")).content.decode()

        # Scope to the sidebar. Matching the whole page would count any
        # in-content shortcut as a nav item and make this assertion meaningless.
        start = page.index("<aside")
        body = page[start : page.index("</aside>", start)]

        for item in NAVIGATION:
            if item.key == "dashboard":
                continue
            href = f'href="{reverse(item.url_name)}"'
            if item.key in EXPECTED_ACCESS[role_code]:
                assert href in body, f"{role_code}: {item.key} missing from sidebar"
            else:
                assert href not in body, f"{role_code}: {item.key} leaked into sidebar"


class TestModulePages:
    def test_unbuilt_module_states_its_phase_and_plan(self, client, hotel, seeded_roles):
        user = make_user("plan@test.local", hotel, seeded_roles[RoleCode.MANAGER])
        client.force_login(user)
        body = client.get(reverse("housekeeping:home")).content.decode()

        assert "Housekeeping" in body
        assert "Arrives in phase" in body
        assert "priority" in body.lower()
        assert "Not built yet" in body

    def test_ai_center_renders_real_data(self, client, hotel, seeded_roles, tenant_context):
        """AI Center has data in Phase 0, so it must show it, not a roadmap."""
        from apps.ai_center.models import ModelConfig, ModelKind

        ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="Primary",
            model_name="gpt-4o-mini",
            is_default=True,
        )
        user = make_user("aic@test.local", hotel, seeded_roles[RoleCode.MANAGER])
        client.force_login(user)
        body = client.get(reverse("ai_center:home")).content.decode()

        assert "gpt-4o-mini" in body
        assert "API integrations" in body
        assert "Not built yet" not in body

    def test_settings_shows_the_hotel_profile(self, client, hotel, seeded_roles):
        user = make_user("cfg@test.local", hotel, seeded_roles[RoleCode.ADMIN])
        client.force_login(user)
        body = client.get(reverse("tenants:settings")).content.decode()

        assert hotel.name in body
        assert hotel.code in body
        assert "AI posture" in body

    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get(reverse("guests:home"))
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]
