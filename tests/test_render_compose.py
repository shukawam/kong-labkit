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
