"""The demo seeder is developer infrastructure, and it still gets tests.

A seeder that silently duplicates users or writes every row with the same
timestamp produces a demo that looks fine and a dashboard that lies.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import AuditLog
from apps.ai_center.models import ModelConfig, PromptVersion, UsageLog
from apps.core.demo_data import DEMO_EMPLOYEE_PREFIX, DEMO_HOTELS, DEMO_PASSWORD
from apps.tenants.models import Hotel, HotelMembership

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

User = get_user_model()

SMALL = {"ai_days": 2, "calls_per_day": 10, "audit": 5, "staff_per_role": 1}


def run(**kwargs) -> str:
    out = StringIO()
    call_command("seed_demo", stdout=out, **{**SMALL, **kwargs})
    return out.getvalue()


@pytest.fixture
def bootstrapped(db):
    call_command("seed_roles", stdout=StringIO())
    call_command("bootstrap_hotel", stdout=StringIO())


class TestPreconditions:
    def test_refuses_without_a_bootstrapped_hotel(self, db):
        """Better a clear error than a half-seeded database."""
        with pytest.raises(CommandError, match="bootstrap_hotel"):
            run()

    def test_refuses_without_system_roles(self, db):
        call_command("bootstrap_hotel", stdout=StringIO())
        Hotel.all_objects.filter(code="GLH-001").exists()
        from apps.accounts.models import Role

        Role.objects.all().delete()
        with pytest.raises(CommandError, match="seed_roles"):
            run()


class TestSeeding:
    def test_creates_every_demo_hotel(self, bootstrapped):
        run()
        codes = set(Hotel.all_objects.values_list("code", flat=True))
        assert {"GLH-001", *(str(h["code"]) for h in DEMO_HOTELS)} <= codes

    def test_creates_staff_with_memberships(self, bootstrapped):
        run()
        staff = User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX)
        assert staff.count() == 12  # 3 hotels x 4 roles x 1
        assert all(HotelMembership.all_objects.filter(user=u).exists() for u in staff)

    def test_staff_can_authenticate(self, bootstrapped):
        from django.contrib.auth import authenticate

        run()
        user = User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX).first()
        assert authenticate(username=user.email, password=DEMO_PASSWORD) is not None

    def test_no_seeded_account_gets_django_admin(self, bootstrapped):
        """`is_staff` is Django admin access, and the field says so itself:
        "Operational roles do not need this."

        It used to be granted to every manager, which handed six demo accounts a way
        into /admin/ — where none of the app's permissions apply. The role system's
        whole guarantee is that opening a module a role lacks returns 403; Django
        admin does not ask. A manager who needs to edit a room edits it in the app.
        """
        run()
        seeded = User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX)
        assert seeded.exists()
        assert not seeded.filter(is_staff=True).exists()

    def test_seeded_accounts_still_reach_the_staff_app(self, bootstrapped):
        """Which is the point: they are locked out of Django admin, not out of the
        product. The error /admin/ gives them reads like a wrong password and is not
        one — the same credentials work at the staff app."""
        from django.test import Client

        run()
        user = User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX).first()

        client = Client()
        assert client.login(username=user.email, password=DEMO_PASSWORD) is True
        assert client.get("/").status_code == 200
        # ...and Django admin bounces them to its own login rather than letting them in.
        assert client.get("/admin/", follow=False).status_code == 302

    def test_every_hotel_gets_its_own_ai_config(self, bootstrapped):
        """Otherwise the extra hotels price at zero and the cost view lies."""
        run()
        for hotel in Hotel.all_objects.all():
            assert ModelConfig.all_objects.filter(tenant=hotel, is_default=True).count() == 7

    def test_model_configs_are_priced(self, bootstrapped):
        run()
        llm = ModelConfig.all_objects.filter(kind="llm").first()
        assert llm.cost_per_1k_input_usd > 0
        assert llm.cost_per_1k_output_usd > 0

    def test_usage_costs_are_non_zero(self, bootstrapped):
        run()
        assert UsageLog.objects.filter(cost_usd__gt=0).exists()

    def test_usage_history_spans_multiple_days(self, bootstrapped):
        """auto_now_add would collapse the whole history onto today."""
        run(ai_days=3)
        days = {row.created_at.date() for row in UsageLog.objects.all()}
        assert len(days) >= 2

    def test_usage_contains_failures_and_cache_hits(self, bootstrapped):
        run(ai_days=5, calls_per_day=60)
        assert UsageLog.objects.filter(success=False).exists()
        assert UsageLog.objects.filter(cache_hit=True).exists()

    def test_audit_entries_are_spread_over_time(self, bootstrapped):
        run(audit=40)
        stamps = {row.created_at.date() for row in AuditLog.objects.all()}
        assert len(stamps) > 1

    def test_adds_an_inactive_prompt_draft(self, bootstrapped):
        """Version rollback is not demonstrable with a single version."""
        run()
        draft = PromptVersion.objects.filter(template__key="reception.system", version=2).first()
        assert draft is not None
        assert draft.is_active is False


class TestIdempotency:
    def test_rerun_does_not_duplicate_hotels_or_staff(self, bootstrapped):
        run()
        hotels = Hotel.all_objects.count()
        staff = User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX).count()

        run()

        assert Hotel.all_objects.count() == hotels
        assert User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX).count() == staff

    def test_same_seed_produces_the_same_volume(self, bootstrapped):
        run(seed=99)
        first = UsageLog.objects.count()
        run(flush=True, seed=99)
        assert UsageLog.objects.count() == first


class TestFlush:
    def test_clears_before_reseeding(self, bootstrapped):
        """--flush wipes then re-seeds, so counts stay flat instead of doubling."""
        run()
        run(flush=True)
        assert User.objects.filter(employee_code__startswith=DEMO_EMPLOYEE_PREFIX).count() == 12
        assert UsageLog.objects.filter(tenant__code="GLH-001").exists()

    def test_never_deletes_the_bootstrapped_hotel(self, bootstrapped):
        """A demo reset must not destroy a real property."""
        run()
        run(flush=True)
        assert Hotel.all_objects.filter(code="GLH-001").exists()

    def test_preserves_a_hand_made_hotel(self, bootstrapped):
        Hotel.all_objects.create(code="REAL-99", name="Real Hotel", slug="real-hotel")
        run()
        run(flush=True)
        assert Hotel.all_objects.filter(code="REAL-99").exists()
