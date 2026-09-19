import json

import yaml

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl

DASHBOARDS = (
    "config/grafana/dashboards/kong-gateway-otel.json",
    "config/grafana/dashboards/kong-ai-gateway-otel.json",
    "config/grafana/dashboards/kong-ai-cost-otel.json",
)


def aigw_ctx(**overrides):
    return ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]}, **overrides)


def test_dashboards_emitted_when_otel_enabled():
    files = render_all(ctx_for())
    assert "config/grafana/dashboards.yaml" in files
    for path in DASHBOARDS:
        assert path in files


def test_nothing_grafana_related_when_otel_disabled():
    files = render_all(ctx_for(observability={"backend": "none"}))
    assert not [p for p in files if p.startswith("config/grafana")]
    assert "config/otel-lgtm/otelcol-config.yaml" not in files


def test_dashboards_are_valid_json_with_stable_uids():
    files = render_all(ctx_for())
    uids = {json.loads(files[path])["uid"] for path in DASHBOARDS}
    assert uids == {"kong-gateway-otel", "kong-ai-gateway-otel", "kong-ai-cost-otel"}


def test_legend_format_braces_survive_rendering():
    """Jinja を通すと {{label}} が消える。assets は素通しでコピーされる必要がある。"""
    body = render_all(ctx_for())["config/grafana/dashboards/kong-gateway-otel.json"]
    assert "{{kong_service_name}}" in body


def test_provisioning_path_stays_under_conf_provisioning():
    """Grafana の permitted_provisioning_paths が conf/provisioning の外を拒否する。"""
    provider = yaml.safe_load(render_all(ctx_for())["config/grafana/dashboards.yaml"])
    path = provider["providers"][0]["options"]["path"]
    assert path.startswith("conf/provisioning/")


def test_collector_config_rewrites_metric_timestamps():
    """これが無いと Kong の古いゲージが too old sample を招き、OTLP バッチ全体が 400 になる。"""
    conf = yaml.safe_load(render_all(ctx_for())["config/otel-lgtm/otelcol-config.yaml"])
    assert "transform/kong" in conf["processors"]
    assert "transform/kong" in conf["service"]["pipelines"]["metrics"]["processors"]


def test_otel_lgtm_mounts_dashboards_and_collector_config(compose_of):
    volumes = compose_of(ctx_for())["services"]["otel-lgtm"]["volumes"]
    assert any(v.endswith("/otel-lgtm/otelcol-config.yaml:ro") for v in volumes)
    assert any(v.endswith("/dashboards/kong.yaml:ro") for v in volumes)
    assert any(v.endswith("/dashboards/kong:ro") for v in volumes)


def test_loki_structured_metadata_limit_is_raised(compose_of):
    """既定 128 では Kong のアクセスログ(属性 156)が 400 になり、同梱の内部ログまで捨てられる。"""
    env = compose_of(ctx_for())["services"]["otel-lgtm"]["environment"]
    assert "max-structured-metadata-entries-count=256" in env["LOKI_EXTRA_ARGS"]


def test_no_otel_lgtm_service_when_otel_disabled(compose_of):
    assert "otel-lgtm" not in compose_of(ctx_for(observability={"backend": "none"}))["services"]


def kong_yaml_of(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)["config/kong/kong.yaml"])


def otel_config(ctx) -> dict:
    for plugin in kong_yaml_of(ctx)["plugins"]:
        if plugin["name"] == "opentelemetry":
            return plugin["config"]
    raise AssertionError("opentelemetry プラグインが無い")


def test_opentelemetry_plugin_exports_metrics():
    metrics = otel_config(ctx_for())["metrics"]
    assert metrics["endpoint"].endswith("/v1/metrics")
    for flag in (
        "enable_request_metrics",
        "enable_latency_metrics",
        "enable_bandwidth_metrics",
        "enable_upstream_health_metrics",
        "enable_consumer_attribute",
        "enable_ai_metrics",
    ):
        assert metrics[flag] is True, flag


def test_opentelemetry_plugin_exports_access_logs():
    """logs_endpoint が運ぶのは Kong 内部のエラーログだけ。アクセスログは別エクスポータ。"""
    assert otel_config(ctx_for())["access_logs"]["endpoint"].endswith("/v1/logs")


def ai_targets(ctx) -> list[dict]:
    for service in kong_yaml_of(ctx)["services"]:
        for plugin in service.get("plugins", []):
            if plugin["name"] == "ai-proxy-advanced":
                return plugin["config"]["targets"]
    raise AssertionError("ai-proxy-advanced が無い")


def test_ai_targets_enable_statistics_logging():
    """既定の false のままだと gen_ai.* メトリクスが一切出ない。"""
    for target in ai_targets(aigw_ctx()):
        assert target["logging"]["log_statistics"] is True


def test_ai_targets_log_payloads():
    """プロンプトと応答本文をスパンに載せる。検証環境なので既定で有効にしている。"""
    for target in ai_targets(aigw_ctx()):
        assert target["logging"]["log_payloads"] is True


def test_statistics_logging_lives_under_the_target():
    """プラグイン直下に logging を置くと Konnect が unknown field で sync 全体を失敗させる。"""
    for service in kong_yaml_of(aigw_ctx())["services"]:
        for plugin in service.get("plugins", []):
            if plugin["name"] == "ai-proxy-advanced":
                assert "logging" not in plugin["config"]


def test_model_costs_are_emitted_only_when_given():
    provider = {**AZURE_PROVIDER, "models": [
        {**AZURE_PROVIDER["models"][0], "input_cost": 1.25, "output_cost": 10},
    ]}
    options = ai_targets(
        ctx_for(gateway="ai-gateway-v1", ai={"providers": [provider]})
    )[0]["model"]["options"]
    assert options["input_cost"] == 1.25
    assert options["output_cost"] == 10

    options = ai_targets(aigw_ctx())[0]["model"]["options"]
    assert "input_cost" not in options
    assert "output_cost" not in options


def v2_ctx(**overrides):
    return ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}, **overrides)


def v2_gateway(ctx) -> dict:
    return load_kongctl(render_all(ctx)["config/kongctl.yaml"])["ai_gateways"][0]


def test_v2_uses_the_policies_key():
    """kongctl の AI Gateway 配下は policies。plugins だと黙って無視され、sync が既存ポリシーを消しにいく。"""
    gateway = v2_gateway(v2_ctx())
    assert "plugins" not in gateway
    assert gateway["policies"]


def test_v2_generates_the_opentelemetry_policy():
    """v2 は Gateway 設定を Konnect が持つ。ここで生成しないとダッシュボードが空のままになる。"""
    policy = next(p for p in v2_gateway(v2_ctx())["policies"] if p["type"] == "opentelemetry")
    metrics = policy["config"]["metrics"]
    assert metrics["endpoint"].endswith("/v1/metrics")
    for flag in (
        "enable_request_metrics",
        "enable_latency_metrics",
        "enable_bandwidth_metrics",
        "enable_upstream_health_metrics",
        "enable_consumer_attribute",
        "enable_ai_metrics",
    ):
        assert metrics[flag] is True, flag


def test_v2_opentelemetry_policy_exports_access_logs():
    """v2 も同じく、access_logs.endpoint が無いとアクセスログが Loki に出ない。"""
    policy = next(p for p in v2_gateway(v2_ctx())["policies"] if p["type"] == "opentelemetry")
    assert policy["config"]["access_logs"]["endpoint"].endswith("/v1/logs")


def test_v2_models_log_payloads():
    """v2 でのフィールド名は payloads。log_payloads と書くと kongctl が unknown field で弾く。"""
    for model in v2_gateway(v2_ctx())["models"]:
        assert model["config"]["logging"] == {"payloads": True}


def test_v2_omits_the_opentelemetry_policy_when_otel_disabled():
    gateway = v2_gateway(v2_ctx(observability={"backend": "none"}))
    assert not any(p["type"] == "opentelemetry" for p in gateway.get("policies") or [])
