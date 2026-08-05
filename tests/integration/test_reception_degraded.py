"""What the kiosk does when the AI provider is broken.

A wrong key, a retired model or a dead network must not turn "what time is
check-out?" into a staff call. The hotel record can answer that, so it should.
"""

from __future__ import annotations

import pytest

from apps.ai_center.models import ModelConfig, ModelKind, Provider
from apps.core.context import set_request_context
from apps.core.exceptions import AIError
from apps.reception.models import Channel, HandoffReason
from services.ai import gateway
from services.reception import orchestrator

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


@pytest.fixture
def convo(hotel):
    set_request_context(tenant_id=str(hotel.pk))
    return orchestrator.start(hotel=hotel, channel=Channel.KIOSK, session_key="s")


@pytest.fixture
def broken_provider(hotel, monkeypatch):
    """A configured model whose every call fails — the real-world 401/400."""
    ModelConfig.all_objects.create(
        tenant=hotel,
        kind=ModelKind.LLM,
        name="Broken",
        provider=Provider.OPENAI,
        model_name="gpt-4o-mini",
        api_key="sk-wrong-key",
        is_default=True,
        is_active=True,
    )

    def explode(*args, **kwargs):
        raise AIError("provider returned 400")

    monkeypatch.setattr(gateway, "chat", explode)
    return hotel


class TestBrokenProvider:
    def test_answers_from_hotel_data_instead_of_escalating(self, convo, broken_provider):
        """The bug this fixes: the guest got 'Something went wrong' for a
        question the database could answer."""
        turn = orchestrator.respond(convo, "What time is check out?")

        assert turn.handoff is False
        assert turn.ai_used is False
        assert "12:00" in turn.reply or "check-out" in turn.reply.lower()
        assert turn.citations

    def test_still_escalates_what_it_cannot_answer(self, convo, broken_provider):
        turn = orchestrator.respond(convo, "Can you arrange a helicopter to Sylhet?")
        assert turn.handoff is True
        assert turn.handoff_reason == HandoffReason.ERROR

    def test_records_the_failure_against_the_config(self, convo, broken_provider):
        orchestrator.respond(convo, "What time is check out?")
        config = ModelConfig.all_objects.get(tenant=broken_provider, name="Broken")
        assert "400" in config.last_error

    def test_badge_turns_amber_not_green(self, convo, broken_provider):
        orchestrator.respond(convo, "What time is check out?")
        status = gateway.status(str(broken_provider.pk))
        assert status["state"] == "degraded"
        assert "hotel data" in status["label"]

    def test_a_key_alone_does_not_make_the_badge_green(self, hotel):
        """'A key exists' was being reported as 'the AI works'."""
        ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="Failing",
            provider=Provider.OPENAI,
            model_name="gpt-4o-mini",
            api_key="sk-something",
            is_default=True,
            is_active=True,
            last_error="provider returned 401",
        )
        assert gateway.status(str(hotel.pk))["state"] == "degraded"

    def test_clearing_the_error_restores_green(self, hotel):
        config = ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="Fixed",
            provider=Provider.OPENAI,
            model_name="gpt-4o-mini",
            api_key="sk-good",
            is_default=True,
            is_active=True,
            last_error="provider returned 401",
        )
        config.last_error = ""
        config.save()
        assert gateway.status(str(hotel.pk))["state"] == "online"
