from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


class TestSystemHealth:
    def test_is_public(self, api_client):
        """The load balancer probes this without credentials."""
        response = api_client.get(reverse("v1:health"))
        assert response.status_code in (200, 503)
        assert set(response.data["checks"]) == {"database", "cache", "pgvector"}

    def test_reports_database_ok(self, api_client):
        assert api_client.get(reverse("v1:health")).data["checks"]["database"] == "ok"

    def test_reports_pgvector_presence(self, api_client):
        """pgvector missing means RAG, face and image search are all dead —
        the probe must surface that separately from 'database ok'."""
        value = api_client.get(reverse("v1:health")).data["checks"]["pgvector"]
        assert value != "missing", "pgvector extension is not installed in the test database"


class TestAIHealth:
    def test_requires_authentication(self, api_client):
        assert api_client.get(reverse("v1:ai_health")).status_code == 401

    def test_round_trips_the_configured_provider(self, auth_client):
        response = auth_client.get(reverse("v1:ai_health"))
        assert response.status_code == 200
        assert response.data["status"] == "ok"
        assert response.data["provider"] == "fake"


class TestAIConfig:
    def test_exposes_no_secrets(self, auth_client):
        response = auth_client.get(reverse("v1:ai_config"))
        assert response.status_code == 200
        body = str(response.data)
        assert "api_key" not in body
        assert "base_url" not in body
