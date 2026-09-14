import pytest
import yaml
from jinja2 import UndefinedError

from generator.render import build_env, render_all
from tests.conftest import ctx_for, only_services


def test_env_uses_strict_undefined():
    env = build_env()
    template = env.from_string("{{ missing_variable }}")
    with pytest.raises(UndefinedError):
        template.render()


def test_env_trims_blocks():
    env = build_env()
    rendered = env.from_string("a\n{% if true %}\nb\n{% endif %}\nc\n").render()
    assert rendered == "a\nb\nc\n"


def test_compose_is_valid_yaml_with_anchor_resolved(compose_of):
    doc = compose_of(only_services(ctx_for(), "otel-lgtm"))
    assert doc["services"]["otel-lgtm"]["networks"] == ["kong-network"]
    assert doc["services"]["otel-lgtm"]["restart"] == "on-failure"


def test_compose_defines_network(compose_of):
    doc = compose_of(only_services(ctx_for(), "otel-lgtm"))
    assert "kong-network" in doc["networks"]


def test_kong_env_anchor_only_for_self_managed():
    raw = render_all(only_services(ctx_for(), "otel-lgtm"))["compose.yaml"]
    assert "x-kong-env" not in raw

    raw_sm = render_all(
        only_services(ctx_for(control_plane="self-managed"), "otel-lgtm")
    )["compose.yaml"]
    assert "x-kong-env: &kong-env" in raw_sm
    assert "KONG_PG_HOST: database" in raw_sm


def test_otel_ports_come_from_allocation(compose_of):
    doc = compose_of(only_services(ctx_for(), "otel-lgtm"))
    assert doc["services"]["otel-lgtm"]["ports"] == ["3000:3000", "4317:4317", "4318:4318"]


def test_partial_renders_at_indent_zero_and_is_indented_by_skeleton():
    raw = render_all(only_services(ctx_for(), "otel-lgtm"))["compose.yaml"]
    # first=True を省くと 1 行目だけインデントされず compose が壊れる
    assert "\n  otel-lgtm:\n" in raw
    assert "\notel-lgtm:\n" not in raw


def test_services_partial_is_standalone_parseable():
    env = build_env()
    ctx = ctx_for()
    raw = env.get_template("services/otel-lgtm.yaml.j2").render(
        ctx=ctx, spec=ctx.spec, ports=ctx.ports, otel=ctx.otel
    )
    # パーシャルはインデント 0 なので単体でも YAML として読める
    # <<: *default は skeleton (compose.yaml.j2) 側の x-default 定義に依存するため、
    # 単体パースにはダミーの &default を補う（本物の値は他のテストで検証済み）
    doc = yaml.safe_load("default: &default {}\n" + raw)
    assert "otel-lgtm" in doc


def test_plain_redis_image_and_healthcheck(compose_of):
    doc = compose_of(only_services(ctx_for(cache={"type": "redis"}), "redis"))
    svc = doc["services"]["redis"]
    assert svc["image"] == "redis:8.0.2"
    assert svc["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert svc["ports"] == ["6379:6379"]


def test_redis_stack_image(compose_of):
    doc = compose_of(only_services(ctx_for(cache={"type": "redis-stack"}), "redis"))
    assert doc["services"]["redis"]["image"] == "redis/redis-stack:7.4.0-v3"


def test_redis_service_name_matches_wiring():
    ctx = ctx_for(cache={"type": "redis-stack"})
    # kongctl.yaml が vectordb.host として書く値と compose のサービス名がずれると接続できない
    assert ctx.cache.host == "redis"
    assert "redis" in render_all(only_services(ctx, "redis"))["compose.yaml"]


def test_pgvector_service(compose_of):
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        vectordb={"type": "pgvector"},
        ai={"semantic_cache": True},
    )
    doc = compose_of(only_services(ctx, "pgvector"))
    svc = doc["services"]["pgvector"]
    assert svc["image"] == "pgvector/pgvector:pg17"
    assert svc["environment"]["POSTGRES_DB"] == "vectors"
    assert svc["environment"]["POSTGRES_USER"] == "kong"
    assert svc["ports"] == ["5432:5432"]
    assert svc["healthcheck"]["test"] == ["CMD-SHELL", "pg_isready -U kong -d vectors"]


def test_pgvector_host_port_shifts_when_kong_metastore_present(compose_of):
    ctx = ctx_for(
        control_plane="self-managed",
        vectordb={"type": "pgvector"},
    )
    doc = compose_of(only_services(ctx, "pgvector"))
    # ホスト側は 5433 に退避するが、コンテナ内は 5432 のまま
    assert doc["services"]["pgvector"]["ports"] == ["5433:5432"]


def test_pgvector_enables_extension_on_init(compose_of):
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        vectordb={"type": "pgvector"},
        ai={"semantic_cache": True},
    )
    doc = compose_of(only_services(ctx, "pgvector"))
    # pgvector イメージでも CREATE EXTENSION は自分で実行する必要がある
    assert "/docker-entrypoint-initdb.d/" in doc["services"]["pgvector"]["volumes"][0]


def test_aigw_v2_konnect_wiring(compose_of):
    ctx = ctx_for(gateway="ai-gateway-v2", region="eu")
    doc = compose_of(only_services(ctx, "kong-aigw-v2"))
    env = doc["services"]["kong"]["environment"]
    assert env["KONG_CLUSTER_CONTROL_PLANE"] == "${CONTROL_PLANE_ID:-}.eu.cp.konghq.com:443"
    assert env["KONG_CLUSTER_TELEMETRY_ENDPOINT"] == "${CONTROL_PLANE_ID:-}.eu.tp.konghq.com:443"
    assert env["KONG_CLUSTER_MTLS"] == "pki"
    assert doc["services"]["kong"]["volumes"] == [".certs:/etc/kong/cluster-certs"]


def test_on_off_values_are_quoted_strings(compose_of):
    # 裸の off は PyYAML が False にする。Kong は "false" を受け付けないので必ず文字列で渡す
    doc = compose_of(only_services(ctx_for(gateway="ai-gateway-v2"), "kong-aigw-v2"))
    env = doc["services"]["kong"]["environment"]
    assert env["KONG_DATABASE"] == "off"
    assert env["KONG_VITALS"] == "off"
    assert env["KONG_KONNECT_MODE"] == "on"


def test_aigw_v2_uses_expressions_router(compose_of):
    doc = compose_of(only_services(ctx_for(gateway="ai-gateway-v2"), "kong-aigw-v2"))
    assert doc["services"]["kong"]["environment"]["KONG_ROUTER_FLAVOR"] == "expressions"


def test_tracing_only_when_otel_enabled(compose_of):
    on = compose_of(only_services(ctx_for(gateway="ai-gateway-v2"), "kong-aigw-v2"))
    assert on["services"]["kong"]["environment"]["KONG_TRACING_INSTRUMENTATIONS"] == "all"

    off = compose_of(
        only_services(
            ctx_for(gateway="ai-gateway-v2", observability={"otel_lgtm": False}),
            "kong-aigw-v2",
        )
    )
    assert "KONG_TRACING_INSTRUMENTATIONS" not in off["services"]["kong"]["environment"]


def test_konnect_dp_for_api_gateway(compose_of):
    doc = compose_of(only_services(ctx_for(), "kong-dp"))
    svc = doc["services"]["gateway"]
    assert svc["image"] == "kong/kong-gateway:3.14"
    assert svc["environment"]["KONG_ROLE"] == "data_plane"
    assert svc["environment"]["KONG_KONNECT_MODE"] == "on"
    assert svc["ports"] == ["8000:8000", "8100:8100"]


def test_self_managed_emits_four_services(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    for name in ("database", "kong-bootstrap", "kong-cp", "kong-dp"):
        assert name in doc["services"], name


def test_self_managed_bootstrap_ordering(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    # bootstrap 完了前に CP が起動すると migrations 未適用で落ちる
    assert doc["services"]["kong-bootstrap"]["depends_on"]["database"]["condition"] == "service_healthy"
    assert (
        doc["services"]["kong-cp"]["depends_on"]["kong-bootstrap"]["condition"]
        == "service_completed_successfully"
    )


def test_self_managed_uses_shared_mtls_and_mounted_certs(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    assert doc["services"]["kong-dp"]["environment"]["KONG_CLUSTER_MTLS"] == "shared"
    assert doc["services"]["kong-cp"]["volumes"] == ["./config/kong/certs:/etc/kong/certs"]


def test_self_managed_admin_and_manager_ports(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    assert doc["services"]["kong-cp"]["ports"] == ["8001:8001", "8002:8002"]


def test_self_managed_dp_trusts_forwarded_ip(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    env = doc["services"]["kong-dp"]["environment"]
    assert env["KONG_TRUSTED_IPS"] == "0.0.0.0/0"
    assert env["KONG_REAL_IP_HEADER"] == "X-Forwarded-For"
    assert env["KONG_REAL_IP_RECURSIVE"] == "on"


def test_keycloak_service(compose_of):
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = compose_of(only_services(ctx, "keycloak"))
    svc = doc["services"]["keycloak"]
    assert svc["image"] == "quay.io/keycloak/keycloak:26.6.1"
    assert svc["command"] == ["start-dev", "--import-realm"]
    assert svc["ports"] == ["8080:8080"]


def test_keycloak_hostname_matches_issuer(compose_of):
    # ブラウザ向けの localhost ではなくコンテナ内の名前を KC_HOSTNAME に使うことで、
    # Keycloak が発行する iss と Kong が検証する issuer を一致させている
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = compose_of(only_services(ctx, "keycloak"))
    kc_hostname = doc["services"]["keycloak"]["environment"]["KC_HOSTNAME"]
    assert ctx.idp.issuer == f"{kc_hostname}/realms/{ctx.idp.realm}"


def test_keycloak_mounts_realm_export(compose_of):
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = compose_of(only_services(ctx, "keycloak"))
    assert doc["services"]["keycloak"]["volumes"] == [
        "./config/keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro"
    ]


def test_httpbin_service(compose_of):
    doc = compose_of(only_services(ctx_for(), "httpbin"))
    svc = doc["services"]["httpbin"]
    assert svc["image"] == "kennethreitz/httpbin"
    assert svc["ports"] == ["8081:80"]


HARDENING = {
    "KONG_ROUTER_FLAVOR": "expressions",
    "KONG_TRUSTED_IPS": "0.0.0.0/0",
    "KONG_REAL_IP_HEADER": "X-Forwarded-For",
    "KONG_REAL_IP_RECURSIVE": "on",
}


@pytest.mark.parametrize(
    "overrides, partial, kong_services",
    [
        ({"gateway": "ai-gateway-v2"}, "kong-aigw-v2", ["kong"]),
        ({}, "kong-dp", ["gateway"]),
        ({"control_plane": "self-managed"}, "kong-self-managed", ["kong-cp", "kong-dp"]),
    ],
    ids=["aigw-v2", "konnect-dp", "self-managed"],
)
def test_every_kong_service_carries_the_same_hardening(
    compose_of, overrides, partial, kong_services
):
    # spec が名指しした過去の罠は構成によらず同じ値で入っていなければ意味がない
    doc = compose_of(only_services(ctx_for(**overrides), partial))
    for name in kong_services:
        env = doc["services"][name]["environment"]
        for key, value in HARDENING.items():
            assert env[key] == value, (name, key)


def test_lldap_service(compose_of):
    ctx = ctx_for(idp={"type": "ldap"})
    doc = compose_of(only_services(ctx, "lldap"))
    svc = doc["services"]["lldap"]
    assert svc["image"] == "lldap/lldap:v0.6.3-alpine"
    assert svc["ports"] == ["3890:3890", "17170:17170"]
    assert svc["environment"]["LLDAP_LDAP_BASE_DN"] == "dc=acme,dc=local"
    # entrypoint が書き込めない /data で終了するため、ボリュームは必須
    assert svc["volumes"] == ["lldap-data:/data"]


def test_lldap_named_volume_is_declared(compose_of):
    doc = compose_of(ctx_for(idp={"type": "ldap"}))
    assert "lldap-data" in doc["volumes"]
    assert "volumes" not in compose_of(ctx_for())


def test_lldap_bootstrap_is_a_one_shot_job(compose_of):
    ctx = ctx_for(idp={"type": "ldap"})
    doc = compose_of(only_services(ctx, "lldap-bootstrap"))
    svc = doc["services"]["lldap-bootstrap"]
    assert svc["entrypoint"] == ["/app/bootstrap.sh"]
    # x-default の on-failure のままだと、投入済みの環境で延々と再実行される
    assert svc["restart"] == "no"
    assert svc["volumes"] == ["./config/lldap:/bootstrap:ro"]
    assert svc["depends_on"]["lldap"]["condition"] == "service_healthy"


def test_lldap_bootstrap_uses_the_same_admin_credentials(compose_of):
    doc = compose_of(only_services(ctx_for(idp={"type": "ldap"}), "lldap", "lldap-bootstrap"))
    server = doc["services"]["lldap"]["environment"]
    job = doc["services"]["lldap-bootstrap"]["environment"]
    assert job["LLDAP_URL"] == "http://lldap:17170"
    assert job["LLDAP_ADMIN_USERNAME"] == server["LLDAP_LDAP_USER_DN"]
    assert job["LLDAP_ADMIN_PASSWORD"] == server["LLDAP_LDAP_USER_PASS"]
