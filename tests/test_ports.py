import pytest

from generator.context import PortConflictError, allocate_ports
from generator.schema import EnvSpec


def spec_from(**overrides) -> EnvSpec:
    return EnvSpec.model_validate({"customer": "acme", "gateway": "api-gateway", **overrides})


def test_konnect_minimal_ports():
    ports = allocate_ports(spec_from(upstream="none"))
    assert ports == {
        "proxy": 8000,
        "status": 8100,
        "grafana": 3000,
        "otlp_grpc": 4317,
        "otlp_http": 4318,
    }


def test_self_managed_adds_admin_and_manager_and_kong_pg():
    ports = allocate_ports(spec_from(control_plane="self-managed", upstream="none"))
    assert ports["admin"] == 8001
    assert ports["manager"] == 8002
    assert ports["kong_pg"] == 5432


def test_pgvector_takes_5432_when_kong_pg_absent():
    ports = allocate_ports(spec_from(vectordb={"type": "pgvector"}, upstream="none"))
    assert ports["vector_pg"] == 5432
    assert "kong_pg" not in ports


def test_pgvector_moves_to_5433_when_kong_pg_present():
    ports = allocate_ports(
        spec_from(
            control_plane="self-managed",
            vectordb={"type": "pgvector"},
            upstream="none",
        )
    )
    assert ports["kong_pg"] == 5432
    assert ports["vector_pg"] == 5433


def test_keycloak_and_cache_and_httpbin():
    ports = allocate_ports(
        spec_from(
            idp={"type": "keycloak", "realm": "acme"},
            cache={"type": "redis"},
        )
    )
    assert ports["keycloak"] == 8080
    assert ports["cache"] == 6379
    assert ports["httpbin"] == 8081


def test_otel_disabled_drops_observability_ports():
    ports = allocate_ports(spec_from(observability={"otel_lgtm": False}, upstream="none"))
    assert "grafana" not in ports
    assert "otlp_http" not in ports


def test_no_duplicate_ports_across_full_configuration():
    ports = allocate_ports(
        spec_from(
            control_plane="self-managed",
            idp={"type": "keycloak", "realm": "acme"},
            cache={"type": "redis-stack"},
            vectordb={"type": "pgvector"},
        )
    )
    assert len(set(ports.values())) == len(ports), ports


def test_conflict_raises_with_actionable_message(monkeypatch):
    import generator.context as ctxmod

    # 2 つのコンポーネントに同じ固定ポートを割り当てた状態を作る
    monkeypatch.setitem(ctxmod.PREFERRED_PORTS, "keycloak", 8000)
    with pytest.raises(PortConflictError) as e:
        allocate_ports(spec_from(idp={"type": "keycloak", "realm": "acme"}, upstream="none"))
    assert "8000" in str(e.value)
    assert "proxy" in str(e.value)
    assert "keycloak" in str(e.value)


def test_ldap_adds_both_lldap_ports():
    ports = allocate_ports(spec_from(idp={"type": "ldap"}))
    assert ports["ldap"] == 3890
    assert ports["lldap_web"] == 17170
    assert "keycloak" not in ports
