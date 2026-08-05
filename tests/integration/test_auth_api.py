from __future__ import annotations

import pytest
from django.urls import reverse

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


class TestTokenLogin:
    def test_returns_tokens_and_identity(self, api_client, receptionist):
        response = api_client.post(
            reverse("v1:token_obtain"),
            {"email": "reception@test.local", "password": "test-pass-12345"},
        )
        assert response.status_code == 200
        assert "access" in response.data
        assert "refresh" in response.data
        assert response.data["user"]["email"] == "reception@test.local"
        assert response.data["user"]["memberships"][0]["role"] == "ai_reception"

    def test_wrong_password_is_rejected(self, api_client, receptionist):
        response = api_client.post(
            reverse("v1:token_obtain"),
            {"email": "reception@test.local", "password": "wrong"},
        )
        assert response.status_code == 401

    def test_locked_account_cannot_sign_in(self, api_client, receptionist):
        """Five failures lock the account; the sixth must fail even if correct."""
        url = reverse("v1:token_obtain")
        for _ in range(5):
            api_client.post(url, {"email": receptionist.email, "password": "wrong"})

        receptionist.refresh_from_db()
        assert receptionist.is_locked

        response = api_client.post(
            url, {"email": receptionist.email, "password": "test-pass-12345"}
        )
        assert response.status_code == 401


class TestMe:
    def test_requires_authentication(self, api_client):
        assert api_client.get(reverse("v1:me")).status_code == 401

    def test_returns_current_identity(self, auth_client, receptionist):
        response = auth_client.get(reverse("v1:me"))
        assert response.status_code == 200
        assert response.data["full_name"] == receptionist.full_name


class TestErrorEnvelope:
    def test_uses_rfc7807_shape(self, api_client):
        """goal.txt D17 — one error contract for every client."""
        response = api_client.get(reverse("v1:me"))
        assert response["Content-Type"].startswith("application/problem+json")
        for key in ("type", "title", "status", "detail", "request_id"):
            assert key in response.data
