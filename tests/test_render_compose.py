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
