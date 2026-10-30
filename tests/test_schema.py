import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from generator.schema import (
    AzureService,
    ControlPlane,
    EnvSpec,
    Gateway,
    NewRelicRegion,
    ObservabilityBackend,
    SecretsBackend,
    UpstreamType,
    load_spec,
)

from tests.conftest import AZURE_PROVIDER

ROOT = Path(__file__).resolve().parents[1]

MINIMAL = {
    "customer": "acme",
    "gateway": "api-gateway",
}


def spec_from(**overrides) -> EnvSpec:
    data = {**MINIMAL, **overrides}
    return EnvSpec.model_validate(data)


def test_minimal_spec_defaults():
    spec = spec_from()
    assert spec.control_plane is ControlPlane.KONNECT
    assert spec.region == "us"
    assert spec.observability.backend is ObservabilityBackend.OTEL_LGTM
    # api-gateway は上流が必要なので httpbin が既定で入る
    assert spec.upstream is UpstreamType.HTTPBIN


def test_ai_gateway_defaults_to_no_upstream():
    spec = spec_from(gateway="ai-gateway-v2")
    assert spec.upstream is UpstreamType.NONE


def test_rule1_aigw_v2_rejects_self_managed():
    with pytest.raises(ValidationError) as e:
        spec_from(gateway="ai-gateway-v2", control_plane="self-managed")
    assert "ai-gateway-v2 は Konnect 専用です" in str(e.value)
    assert "control_plane: konnect にするか" in str(e.value)


def test_rule2_semantic_cache_requires_vectordb():
    with pytest.raises(ValidationError) as e:
        spec_from(
            gateway="ai-gateway-v2",
            ai={"semantic_cache": True},
            vectordb={"type": "none"},
        )
    assert "vectordb.type を redis-stack か pgvector にしてください" in str(e.value)


def test_rule2_semantic_routing_requires_vectordb():
    with pytest.raises(ValidationError) as e:
        spec_from(
            gateway="ai-gateway-v2",
            ai={"semantic_routing": True},
            vectordb={"type": "none"},
        )
    assert "vectordb.type を redis-stack か pgvector にしてください" in str(e.value)


@pytest.mark.parametrize("cache_type", ["redis", "none"])
def test_rule3_redis_stack_vectordb_requires_redis_stack_cache(cache_type):
    with pytest.raises(ValidationError) as e:
        spec_from(
            gateway="ai-gateway-v2",
            cache={"type": cache_type},
            vectordb={"type": "redis-stack"},
        )
    assert "cache.type: redis-stack が必要です" in str(e.value)


def test_rule3_allows_redis_stack_for_both_roles():
    spec = spec_from(
        gateway="ai-gateway-v2",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
    )
    assert spec.warnings() == []


def test_rule4_entra_id_warns_but_succeeds():
    spec = spec_from(idp={"type": "entra-id"})
    warnings = spec.warnings()
    assert len(warnings) == 1
    assert "app registration" in warnings[0]


def test_keycloak_without_realm_is_rejected():
    with pytest.raises(ValidationError) as e:
        spec_from(idp={"type": "keycloak"})
    assert "idp.realm を指定してください" in str(e.value)


def test_ldap_is_rejected_for_ai_gateway_v2():
    with pytest.raises(ValidationError) as e:
        spec_from(gateway="ai-gateway-v2", idp={"type": "ldap"})
    assert "idp.type: ldap は ai-gateway-v2 では使えません" in str(e.value)
    assert "gateway: ai-gateway-v1" in str(e.value)


@pytest.mark.parametrize("gateway", ["ai-gateway-v1", "api-gateway"])
def test_ldap_needs_no_realm(gateway):
    spec = spec_from(gateway=gateway, idp={"type": "ldap"})
    assert spec.idp.realm is None
    assert spec.warnings() == []


def test_ldap_users_are_rejected_for_other_idps():
    with pytest.raises(ValidationError) as e:
        spec_from(idp={"type": "keycloak", "realm": "acme", "users": [{"name": "developer"}]})
    assert "idp.users は idp.type: ldap のときだけ指定できます" in str(e.value)


def test_ldap_rejects_duplicate_user_names():
    with pytest.raises(ValidationError) as e:
        spec_from(
            idp={
                "type": "ldap",
                "users": [{"name": "developer"}, {"name": "developer"}],
            }
        )
    assert "idp.users の name が重複しています: developer" in str(e.value)


def test_ldap_users_are_optional():
    assert spec_from(idp={"type": "ldap"}).idp.users == []


def test_konnect_name_defaults_to_unset():
    assert spec_from().konnect_name is None


def test_konnect_name_is_rejected_for_self_managed():
    with pytest.raises(ValidationError) as e:
        spec_from(control_plane="self-managed", konnect_name="bluesky")
    assert "konnect_name は control_plane: konnect のときだけ指定できます" in str(e.value)


@pytest.mark.parametrize("target", ["kubernetes", "cloud-run", "aca"])
def test_unsupported_targets_are_rejected(target):
    with pytest.raises(ValidationError) as e:
        spec_from(target=target)
    assert "現在は compose のみ対応しています" in str(e.value)


def test_load_spec_reads_yaml(tmp_path):
    path = tmp_path / "env.yaml"
    path.write_text(yaml.safe_dump(MINIMAL), encoding="utf-8")
    spec = load_spec(path)
    assert spec.customer == "acme"
    assert spec.gateway is Gateway.API_GATEWAY


def test_json_schema_is_up_to_date():
    sys.path.insert(0, str(ROOT / "scripts"))
    from update_schema import SCHEMA, build

    assert SCHEMA.read_text(encoding="utf-8") == build(), (
        "schemas/env.schema.json が generator/schema.py と食い違っています。"
        "uv run python scripts/update_schema.py で更新してください。"
    )


@pytest.mark.parametrize(
    "example", sorted((ROOT / "examples").glob("*.yaml")), ids=lambda p: p.stem
)
def test_examples_declare_schema(example):
    first = example.read_text(encoding="utf-8").splitlines()[0]
    assert first == "# yaml-language-server: $schema=../schemas/env.schema.json"


def test_newrelic_region_defaults_to_us():
    spec = spec_from(observability={"backend": "newrelic"})
    assert spec.observability.newrelic is None
    spec = spec_from(observability={"backend": "newrelic", "newrelic": {}})
    assert spec.observability.newrelic.region is NewRelicRegion.US


def test_newrelic_block_needs_the_newrelic_backend():
    with pytest.raises(ValidationError) as e:
        spec_from(observability={"backend": "otel-lgtm", "newrelic": {"region": "eu"}})
    assert "observability.newrelic" in str(e.value)


def test_secrets_backend_defaults_to_env():
    assert spec_from().secrets.backend is SecretsBackend.ENV


def test_vault_backend_is_rejected_for_aigw_v2():
    with pytest.raises(ValidationError) as e:
        spec_from(gateway="ai-gateway-v2", secrets={"backend": "vault"})
    assert "secrets.backend: vault" in str(e.value)


def test_vault_backend_is_accepted_for_deck_gateways():
    assert (
        spec_from(idp={"type": "ldap"}, secrets={"backend": "vault"}).secrets.backend
        is SecretsBackend.VAULT
    )
    assert (
        spec_from(
            gateway="ai-gateway-v1", idp={"type": "ldap"}, secrets={"backend": "vault"}
        ).secrets.backend
        is SecretsBackend.VAULT
    )


def test_vault_backend_is_rejected_without_a_secret_to_hold():
    with pytest.raises(ValidationError) as e:
        spec_from(secrets={"backend": "vault"})
    assert "Vault に入れる秘密がありません" in str(e.value)


MANAGED_IDENTITY_PROVIDER = {
    "type": "azure",
    "instance": "acme-foundry",
    "auth": "managed-identity",
    "models": [{"name": "gpt-5-6", "deployment_id": "gpt-5.6", "api_version": "2024-12-01-preview"}],
}


def test_vault_backend_is_rejected_when_no_provider_carries_a_secret():
    """managed-identity は資格情報を持たないので、provider があっても Vault は空になる。"""
    with pytest.raises(ValidationError) as e:
        spec_from(
            gateway="ai-gateway-v1",
            ai={"providers": [MANAGED_IDENTITY_PROVIDER]},
            secrets={"backend": "vault"},
        )
    assert "Vault に入れる秘密がありません" in str(e.value)


def test_vault_backend_accepts_managed_identity_when_the_idp_has_a_secret():
    spec = spec_from(
        gateway="ai-gateway-v1",
        ai={"providers": [MANAGED_IDENTITY_PROVIDER]},
        idp={"type": "ldap"},
        secrets={"backend": "vault"},
    )
    assert spec.secrets.backend is SecretsBackend.VAULT


def test_azure_foundry_accepts_model_id():
    provider = {
        "type": "azure",
        "service": "azure-foundry",
        "instance": "acme-foundry",
        "models": [{"name": "gpt-5-6", "model_id": "gpt-5.6"}],
    }
    spec = spec_from(gateway="ai-gateway-v2", ai={"providers": [provider]})
    assert spec.ai.providers[0].service is AzureService.AZURE_FOUNDRY


def test_azure_service_defaults_to_unset():
    spec = spec_from(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    assert spec.ai.providers[0].service is None


@pytest.mark.parametrize("field", ["deployment_id", "api_version"])
def test_azure_foundry_rejects_deployment_fields(field):
    provider = {
        "type": "azure",
        "service": "azure-foundry",
        "instance": "acme-foundry",
        "models": [{"name": "gpt-5-6", field: "x"}],
    }
    with pytest.raises(ValidationError, match=field):
        spec_from(gateway="ai-gateway-v2", ai={"providers": [provider]})


def test_azure_foundry_requires_instance():
    provider = {
        "type": "azure",
        "service": "azure-foundry",
        "models": [{"name": "gpt-5-6", "model_id": "gpt-5.6"}],
    }
    with pytest.raises(ValidationError, match="instance"):
        spec_from(gateway="ai-gateway-v2", ai={"providers": [provider]})


def test_service_is_rejected_for_non_azure_providers():
    provider = {"type": "openai", "service": "azure-foundry", "models": [{"name": "gpt-4o"}]}
    with pytest.raises(ValidationError, match="type: azure"):
        spec_from(gateway="ai-gateway-v2", ai={"providers": [provider]})


def test_azure_foundry_requires_ai_gateway_v2():
    provider = {
        "type": "azure",
        "service": "azure-foundry",
        "instance": "acme-foundry",
        "models": [{"name": "gpt-5-6", "model_id": "gpt-5.6"}],
    }
    with pytest.raises(ValidationError, match="ai-gateway-v2"):
        spec_from(gateway="ai-gateway-v1", ai={"providers": [provider]})
