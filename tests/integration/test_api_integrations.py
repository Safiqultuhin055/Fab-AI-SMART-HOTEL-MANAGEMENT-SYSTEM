"""Provider registry, the Anthropic adapter, and importing a legacy table."""

# ruff: noqa: E501 - the fixture below is a verbatim tab-separated export; wrapping
# it would stop it being a faithful reproduction of the real file.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.ai_center.models import DEFAULT_BASE_URLS, ModelConfig, ModelKind, Provider
from apps.core.context import set_request_context
from services.ai import registry
from services.ai.base import ChatMessage, ResolvedModel, Role
from services.ai.providers.anthropic import AnthropicProvider

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

LEGACY_CSV = """id\tprovider\tlabel\tapi_key\tapi_model\tbase_url\textra_config\tis_default\tis_active\tis_deleted
1\tanthropic\tClaude (POS voice)\tsk-ant-test-0123456789\tclaude-sonnet-5\tNULL\t\tFalse\tFalse\tFalse
2\tgemini\tGemini\tAQ.test-0123456789\tgemini-flash-lite-latest\tNULL\t\tFalse\tFalse\tFalse
3\tlocal\tLocal Server\tAI-test-0123456789\tgpt-oss:120b-cloud\thttp://192.168.1.5:360\t\tFalse\tFalse\tFalse
4\topenai\tChatGPT\tsk-proj-test-0123456789\tgpt-5.4-mini\tNULL\t\tTrue\tTrue\tFalse
5\tzai\tZ.ai\t8e8ctest0123456789\tglm-4.7-flash\thttps://api.z.ai/api/paas/v4/\t\tFalse\tTrue\tFalse
6\topenai\tEmbeddings\tsk-test-0123456789\ttext-embedding-3-small\tNULL\t\tFalse\tFalse\tFalse
7\tunknownvendor\tMystery\tkey-0123456789\tsome-model\tNULL\t\tFalse\tFalse\tFalse
8\topenai\tDeleted one\tsk-test-9999999999\tgpt-4\tNULL\t\tFalse\tFalse\tTrue
"""


@pytest.fixture
def legacy_file(tmp_path: Path) -> Path:
    path = tmp_path / "api_integrations.csv"
    path.write_text(LEGACY_CSV, encoding="utf-8")
    return path


def run_import(path: Path, hotel, **flags):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("import_api_integrations", str(path), hotel=hotel.code, stdout=out, **flags)
    return out.getvalue()


# ==============================================================================
# Provider routing
# ==============================================================================


class TestProviderRouting:
    @pytest.mark.parametrize(
        "provider",
        ["openai", "gemini", "zai", "moonshot", "local", "groq", "openrouter", "azure_openai"],
    )
    def test_openai_compatible_vendors_share_one_adapter(self, provider):
        from services.ai.providers.openai_compatible import OpenAICompatibleProvider

        assert isinstance(registry.get_provider(provider), OpenAICompatibleProvider)

    def test_anthropic_gets_its_own_adapter(self):
        assert isinstance(registry.get_provider("anthropic"), AnthropicProvider)

    def test_unknown_provider_fails_loudly(self):
        with pytest.raises(ValueError, match="No adapter"):
            registry.get_provider("not-a-vendor")

    def test_every_choice_has_an_adapter_or_is_explicitly_local(self):
        """A provider selectable in the admin must be callable, or the operator
        picks it and discovers the gap when a guest asks a question."""
        local_only = {
            Provider.LOCAL_CLIP,
            Provider.LOCAL_INSIGHTFACE,
            Provider.PADDLEOCR,
            Provider.AZURE_SPEECH,
        }
        for value, _label in Provider.choices:
            if value in local_only:
                continue
            assert value in registry.PROVIDER_ADAPTERS, f"{value} has no adapter"


# ==============================================================================
# Anthropic wire format
# ==============================================================================


class TestAnthropicAdapter:
    @pytest.fixture
    def model(self) -> ResolvedModel:
        return ResolvedModel(
            kind="llm",
            provider="anthropic",
            model_name="claude-sonnet-5",
            api_key="sk-ant-test",
            max_tokens=256,
        )

    def test_system_prompt_is_hoisted_out_of_messages(self, model):
        """The classic mistake: Claude takes `system` as a top-level field."""
        payload = AnthropicProvider()._payload(
            [
                ChatMessage(Role.SYSTEM, "You are a receptionist."),
                ChatMessage(Role.USER, "hello"),
            ],
            model,
        )
        assert payload["system"] == "You are a receptionist."
        assert all(m["role"] != "system" for m in payload["messages"])

    def test_max_tokens_is_always_sent(self, model):
        """Required by the API, unlike OpenAI where it is optional."""
        payload = AnthropicProvider()._payload([ChatMessage(Role.USER, "hi")], model)
        assert payload["max_tokens"] == 256

    def test_consecutive_same_role_turns_are_merged(self, model):
        payload = AnthropicProvider()._payload(
            [
                ChatMessage(Role.USER, "first"),
                ChatMessage(Role.USER, "second"),
                ChatMessage(Role.ASSISTANT, "reply"),
            ],
            model,
        )
        roles = [m["role"] for m in payload["messages"]]
        assert roles == ["user", "assistant"]
        assert "first" in payload["messages"][0]["content"]
        assert "second" in payload["messages"][0]["content"]

    def test_conversation_starts_with_the_user(self, model):
        payload = AnthropicProvider()._payload([ChatMessage(Role.ASSISTANT, "hi there")], model)
        assert payload["messages"][0]["role"] == "user"

    def test_auth_uses_x_api_key_and_a_version_header(self, model):
        headers = AnthropicProvider()._headers(model)
        assert headers["x-api-key"] == "sk-ant-test"
        assert headers["anthropic-version"]
        assert "Authorization" not in headers

    def test_text_blocks_are_concatenated(self):
        text = AnthropicProvider()._text_of(
            {
                "content": [
                    {"type": "text", "text": "Check-out is "},
                    {"type": "tool_use", "name": "ignored"},
                    {"type": "text", "text": "at 12:00."},
                ]
            }
        )
        assert text == "Check-out is at 12:00."

    def test_url_appends_messages(self, model):
        assert AnthropicProvider()._url(model).endswith("/messages")


# ==============================================================================
# ModelConfig
# ==============================================================================


class TestModelConfig:
    def test_base_url_defaults_from_the_provider(self, hotel):
        set_request_context(tenant_id=str(hotel.pk))
        config = ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="Claude",
            provider=Provider.ANTHROPIC,
            model_name="claude-sonnet-5",
        )
        assert config.base_url == DEFAULT_BASE_URLS[Provider.ANTHROPIC]

    def test_explicit_base_url_is_kept(self, hotel):
        config = ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="Local",
            provider=Provider.LOCAL,
            model_name="gpt-oss",
            base_url="http://192.168.1.5:360",
        )
        assert config.base_url == "http://192.168.1.5:360"

    def test_key_is_masked_not_exposed(self, hotel):
        config = ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="X",
            provider=Provider.OPENAI,
            model_name="gpt-4o-mini",
            api_key="sk-proj-abcdefghijklmnop",
        )
        masked = config.masked_key
        assert "abcdefghij" not in masked
        assert masked.startswith("sk-p")
        assert "(24 chars)" in masked

    def test_missing_key_masks_to_a_dash(self, hotel):
        config = ModelConfig.all_objects.create(
            tenant=hotel, kind=ModelKind.LLM, name="X", provider=Provider.LOCAL, model_name="m"
        )
        assert config.masked_key == "—"

    def test_key_is_encrypted_in_the_column(self, hotel):
        from django.db import connection

        config = ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="X",
            provider=Provider.OPENAI,
            model_name="m",
            api_key="sk-super-secret-value",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT api_key FROM ai_center_modelconfig WHERE id = %s", [str(config.pk)]
            )
            stored = cursor.fetchone()[0]
        assert "sk-super-secret-value" not in stored
        assert stored.startswith("enc:v1:")


class TestDefaultSwitching:
    """Promoting a new default must demote the old one, not 500.

    The partial unique index on (tenant, kind) where is_default is correct — but
    an operator ticking "Default" on a second LLM row is doing something entirely
    ordinary, and used to get an IntegrityError page quoting a constraint name.
    """

    @pytest.fixture
    def two_llms(self, hotel):
        first = ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="ChatGPT",
            provider=Provider.OPENAI,
            model_name="gpt-4o-mini",
            is_default=True,
        )
        second = ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="Gemini",
            provider=Provider.GEMINI,
            model_name="gemini-flash-lite-latest",
        )
        return first, second

    def test_promoting_demotes_the_previous_default(self, two_llms):
        first, second = two_llms
        second.is_default = True
        second.save()

        first.refresh_from_db()
        second.refresh_from_db()
        assert second.is_default is True
        assert first.is_default is False

    def test_exactly_one_default_survives(self, two_llms, hotel):
        _, second = two_llms
        second.is_default = True
        second.save()

        assert (
            ModelConfig.all_objects.filter(
                tenant=hotel, kind=ModelKind.LLM, is_default=True
            ).count()
            == 1
        )

    def test_narrow_update_fields_still_demotes(self, two_llms):
        """A caller passing update_fields must not bypass the demotion."""
        first, second = two_llms
        second.is_default = True
        second.save(update_fields=["is_default"])

        first.refresh_from_db()
        assert first.is_default is False

    def test_other_capabilities_are_untouched(self, hotel, two_llms):
        embedding = ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.EMBEDDING,
            name="Embeddings",
            provider=Provider.OPENAI,
            model_name="text-embedding-3-small",
            is_default=True,
        )
        _, second = two_llms
        second.is_default = True
        second.save()

        embedding.refresh_from_db()
        assert embedding.is_default is True

    def test_other_hotels_are_untouched(self, hotel, other_hotel, two_llms):
        theirs = ModelConfig.all_objects.create(
            tenant=other_hotel,
            kind=ModelKind.LLM,
            name="Their default",
            provider=Provider.OPENAI,
            model_name="gpt-4o-mini",
            is_default=True,
        )
        _, second = two_llms
        second.is_default = True
        second.save()

        theirs.refresh_from_db()
        assert theirs.is_default is True

    def test_admin_change_form_does_not_500(self, client, db, two_llms, hotel):
        """Reproduces the reported crash through the real admin POST."""
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        admin = get_user_model().objects.create_superuser(
            email="def@test.local", password="test-pass-12345", full_name="Admin"
        )
        client.force_login(admin)

        first, second = two_llms
        response = client.post(
            reverse("admin:ai_center_modelconfig_change", args=[second.pk]),
            {
                "tenant": str(hotel.pk),
                "kind": ModelKind.LLM,
                "name": "Gemini",
                "is_default": "on",
                "is_active": "on",
                "provider": Provider.GEMINI,
                "model_name": "gemini-flash-lite-latest",
                "api_key": "",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "temperature": "0.2",
                "max_tokens": "1024",
                "timeout_s": "30",
                "dimension": "",
                "fallback": "",
                "cost_per_1k_input_usd": "0.000000",
                "cost_per_1k_output_usd": "0.000000",
                "extra": "{}",
                "external_ref": "pos:2",
                "_save": "Save",
            },
        )
        assert response.status_code == 302, "admin save should redirect, not error"

        first.refresh_from_db()
        second.refresh_from_db()
        assert second.is_default is True
        assert first.is_default is False


# ==============================================================================
# Legacy import
# ==============================================================================


class TestLegacyImport:
    def test_imports_every_usable_row(self, legacy_file, hotel):
        run_import(legacy_file, hotel)
        assert ModelConfig.all_objects.filter(external_ref__startswith="pos:").count() == 7

    def test_deleted_rows_are_skipped(self, legacy_file, hotel):
        run_import(legacy_file, hotel)
        assert not ModelConfig.all_objects.filter(name="Deleted one").exists()

    def test_deleted_rows_can_be_forced(self, legacy_file, hotel):
        run_import(legacy_file, hotel, include_deleted=True)
        assert ModelConfig.all_objects.filter(name="Deleted one").exists()

    def test_providers_are_mapped(self, legacy_file, hotel):
        run_import(legacy_file, hotel)
        by_name = {
            c.name: c for c in ModelConfig.all_objects.filter(external_ref__startswith="pos:")
        }
        assert by_name["Claude (POS voice)"].provider == Provider.ANTHROPIC
        assert by_name["Gemini"].provider == Provider.GEMINI
        assert by_name["Z.ai"].provider == Provider.ZAI
        assert by_name["Local Server"].provider == Provider.LOCAL

    def test_unknown_provider_becomes_other_and_is_reported(self, legacy_file, hotel):
        output = run_import(legacy_file, hotel)
        mystery = ModelConfig.all_objects.get(name="Mystery")
        assert mystery.provider == Provider.OTHER
        assert "unknown provider" in output

    def test_sql_null_text_never_becomes_a_base_url(self, legacy_file, hotel):
        """A CSV export writes NULL as the word NULL; using it 404s every call."""
        run_import(legacy_file, hotel)
        for config in ModelConfig.all_objects.filter(external_ref__startswith="pos:"):
            assert config.base_url.upper() != "NULL"
            # Known vendors get their documented endpoint; "other" is left blank
            # for the operator to fill, which is honest rather than a guess.
            if config.provider in DEFAULT_BASE_URLS or config.base_url:
                assert config.base_url.startswith("http"), config.name
            else:
                assert config.provider == Provider.OTHER

    def test_capability_is_inferred_from_the_model_name(self, legacy_file, hotel):
        output = run_import(legacy_file, hotel)
        assert ModelConfig.all_objects.get(name="Embeddings").kind == ModelKind.EMBEDDING
        assert ModelConfig.all_objects.get(name="ChatGPT").kind == ModelKind.LLM
        assert "inferred" in output

    def test_nothing_is_activated_on_import(self, legacy_file, hotel):
        """Row 4 is default+active in the source. Trusting that would point a
        live hotel at a credential copied from another system."""
        run_import(legacy_file, hotel)
        imported = ModelConfig.all_objects.filter(external_ref__startswith="pos:")
        assert not imported.filter(is_active=True).exists()
        assert not imported.filter(is_default=True).exists()

    def test_reimport_updates_rather_than_duplicates(self, legacy_file, hotel):
        run_import(legacy_file, hotel)
        run_import(legacy_file, hotel)
        assert ModelConfig.all_objects.filter(external_ref__startswith="pos:").count() == 7

    def test_dry_run_writes_nothing(self, legacy_file, hotel):
        output = run_import(legacy_file, hotel, dry_run=True)
        assert ModelConfig.all_objects.filter(external_ref__startswith="pos:").count() == 0
        assert "DRY RUN" in output

    def test_keys_are_never_printed_in_full(self, legacy_file, hotel):
        output = run_import(legacy_file, hotel)
        assert "sk-proj-test-0123456789" not in output
        assert "…" in output

    def test_json_export_is_accepted(self, tmp_path, hotel):
        path = tmp_path / "export.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "id": 42,
                        "provider": "openai",
                        "label": "From JSON",
                        "api_key": "sk-json-000000",
                        "api_model": "gpt-4o-mini",
                        "base_url": None,
                        "is_deleted": False,
                    }
                ]
            ),
            encoding="utf-8",
        )
        run_import(path, hotel)
        assert ModelConfig.all_objects.filter(name="From JSON").exists()

    def test_unknown_hotel_is_rejected(self, legacy_file, db):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="Unknown hotel"):
            run_import(legacy_file, type("H", (), {"code": "NOPE-1"})())


# ==============================================================================
# AI Center page
# ==============================================================================


class TestAICenterPage:
    def test_lists_integrations_with_masked_keys(self, client, hotel, db):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        ModelConfig.all_objects.create(
            tenant=hotel,
            kind=ModelKind.LLM,
            name="Claude (POS voice)",
            provider=Provider.ANTHROPIC,
            model_name="claude-sonnet-5",
            api_key="sk-ant-super-secret-key-value",
        )
        admin = get_user_model().objects.create_superuser(
            email="aic@test.local", password="test-pass-12345", full_name="Admin"
        )
        client.force_login(admin)
        body = client.get(reverse("ai_center:home")).content.decode()

        assert "Claude (POS voice)" in body
        assert "Anthropic" in body
        assert "sk-ant-super-secret-key-value" not in body
        assert "API integrations" in body
