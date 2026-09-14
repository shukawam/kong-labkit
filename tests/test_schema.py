import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from generator.schema import ControlPlane, EnvSpec, Gateway, UpstreamType, load_spec

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
    assert spec.observability.otel_lgtm is True
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
