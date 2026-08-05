"""Gateway behaviour that must hold regardless of which provider is configured."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.exceptions import AIDisabled, AIError
from services.ai import gateway
from services.ai.base import ChatMessage, ResolvedModel, Role, Usage
from services.ai.metering import compute_cost
from services.ai.providers.fake import FakeProvider


@pytest.fixture
def fake_model() -> ResolvedModel:
    return ResolvedModel(kind="llm", provider="fake", model_name="fake-model", dimension=8)


class TestFakeProvider:
    def test_chat_echoes_last_user_message(self, fake_model):
        result = FakeProvider().chat([ChatMessage(Role.USER, "where is the pool")], fake_model)
        assert "where is the pool" in result.text
        assert result.provider == "fake"

    def test_embeddings_are_deterministic(self, fake_model):
        provider = FakeProvider()
        first = provider.embed(["sea view room"], fake_model)
        second = provider.embed(["sea view room"], fake_model)
        assert first.vectors == second.vectors

    def test_embeddings_differ_per_input(self, fake_model):
        provider = FakeProvider()
        result = provider.embed(["sea view room", "gym"], fake_model)
        assert result.vectors[0] != result.vectors[1]

    def test_embeddings_are_unit_length(self, fake_model):
        """Normalised vectors keep cosine distance meaningful in pgvector tests."""
        vector = FakeProvider().embed(["anything"], fake_model).vector
        magnitude = sum(v * v for v in vector) ** 0.5
        assert magnitude == pytest.approx(1.0, abs=1e-9)

    def test_respects_requested_dimension(self, fake_model):
        assert len(FakeProvider().embed(["x"], fake_model).vector) == 8


class TestCostModel:
    def test_computes_from_token_counts(self):
        model = ResolvedModel(
            kind="llm",
            provider="fake",
            model_name="m",
            cost_per_1k_input=Decimal("0.15"),
            cost_per_1k_output=Decimal("0.60"),
        )
        cost = compute_cost(Usage(input_tokens=2000, output_tokens=1000), model)
        assert cost == Decimal("0.900000")

    def test_zero_priced_model_is_free(self):
        model = ResolvedModel(kind="llm", provider="fake", model_name="m")
        assert compute_cost(Usage(input_tokens=999_999), model) == Decimal("0")


class TestKillSwitch:
    def test_global_kill_switch_blocks_every_call(self, settings):
        """goal.txt D12: the switch must stop AI everywhere, immediately."""
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        with pytest.raises(AIDisabled):
            gateway.chat([ChatMessage(Role.USER, "hi")])

    def test_is_available_reports_false_instead_of_raising(self, settings):
        settings.AI = {**settings.AI, "KILL_SWITCH": True}
        assert gateway.is_available() is False

    def test_disabled_globally_blocks(self, settings):
        settings.AI = {**settings.AI, "ENABLED": False}
        with pytest.raises(AIDisabled):
            gateway.chat([ChatMessage(Role.USER, "hi")])


class TestMessageNormalisation:
    def test_accepts_plain_dicts(self):
        messages = gateway._normalise([{"role": "user", "content": "hello"}])
        assert isinstance(messages[0], ChatMessage)
        assert messages[0].content == "hello"

    def test_rejects_unknown_types(self):
        with pytest.raises(TypeError):
            gateway._normalise([42])


class TestDimensionGuard:
    def test_mismatch_raises_rather_than_corrupting_the_index(self, monkeypatch):
        """goal.txt D08 — the failure mode this prevents is silent, so it must be loud."""
        from services.ai import registry

        monkeypatch.setattr(
            registry,
            "resolve",
            lambda kind, tenant_id=None: ResolvedModel(
                kind="embedding", provider="fake", model_name="fake", dimension=8
            ),
        )
        with pytest.raises(AIError, match="dimension mismatch"):
            gateway.embed("sea view", expect_dimension=1536)
