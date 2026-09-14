import pytest
import yaml
from pydantic import ValidationError

from generator.schema import ControlPlane, EnvSpec, Gateway, UpstreamType, load_spec

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
