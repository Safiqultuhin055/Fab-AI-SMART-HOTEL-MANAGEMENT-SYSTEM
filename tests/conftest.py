from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import Role, RoleCode
from apps.core.context import clear_request_context, set_request_context
from apps.tenants.models import Hotel, HotelMembership


@pytest.fixture(autouse=True)
def _clean_context():
    """Ambient tenant/actor must not leak between tests.

    Contextvars persist within a thread; one test setting a tenant would
    silently scope the next test's querysets.
    """
    clear_request_context()
    yield
    clear_request_context()


@pytest.fixture
def hotel(db) -> Hotel:
    return Hotel.objects.create(
        code="TEST-01",
        name="Test Hotel",
        slug="test-hotel",
        city="Dhaka",
        total_rooms=50,
    )


@pytest.fixture
def other_hotel(db) -> Hotel:
    return Hotel.objects.create(
        code="TEST-02", name="Other Hotel", slug="other-hotel", city="Chittagong"
    )


@pytest.fixture
def roles(db) -> dict[str, Role]:
    return {
        code: Role.objects.create(code=code, name=RoleCode(code).label, is_system=True)
        for code in RoleCode
    }


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        email="reception@test.local", password="test-pass-12345", full_name="Rina Haque"
    )


@pytest.fixture
def receptionist(db, user, hotel, roles):
    HotelMembership.objects.create(
        user=user, hotel=hotel, role=roles[RoleCode.AI_RECEPTION], is_default=True
    )
    return user


@pytest.fixture
def tenant_context(hotel):
    set_request_context(tenant_id=str(hotel.pk))
    yield hotel
    clear_request_context()


@pytest.fixture
def guest_factory(db, hotel):
    """Distinct guests on demand — booking tests need several per case."""
    from apps.guests.models import Guest

    counter = {"n": 0}

    def make(**overrides):
        counter["n"] += 1
        defaults = {
            "tenant": hotel,
            "first_name": f"Guest{counter['n']}",
            "last_name": "Test",
            "phone": f"+88017000000{counter['n']:02d}",
        }
        return Guest.all_objects.create(**{**defaults, **overrides})

    return make


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def auth_client(api_client, receptionist):
    api_client.force_authenticate(user=receptionist)
    return api_client
