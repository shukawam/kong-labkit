import yaml

from generator.render import render_all
from tests.conftest import ctx_for

COLLECTOR_CONFIG = "config/otel-collector/config.yaml"


def nr_ctx(**overrides):
    return ctx_for(observability={"backend": "newrelic", **overrides})


def collector_config(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)[COLLECTOR_CONFIG])


def test_collector_config_replaces_the_lgtm_assets():
    files = render_all(nr_ctx())
    assert COLLECTOR_CONFIG in files
    assert "config/otel-lgtm/otelcol-config.yaml" not in files
    assert not [p for p in files if p.startswith("config/grafana")]


def test_exporter_targets_new_relic_with_the_license_key():
    exporter = collector_config(nr_ctx())["exporters"]["otlp_http/newrelic"]
    assert exporter["endpoint"] == "https://otlp.nr-data.net"
    # Collector 自身が展開する参照。Jinja に食わせても壊れないことの確認も兼ねる
    assert exporter["headers"]["api-key"] == "${env:NEW_RELIC_LICENSE_KEY}"


def test_eu_region_targets_the_eu_endpoint():
    exporter = collector_config(nr_ctx(newrelic={"region": "eu"}))["exporters"][
        "otlp_http/newrelic"
    ]
    assert exporter["endpoint"] == "https://otlp.eu01.nr-data.net"


def test_every_pipeline_exports_to_new_relic():
    pipelines = collector_config(nr_ctx())["service"]["pipelines"]
    assert set(pipelines) == {"traces", "metrics", "logs"}
    for name, pipeline in pipelines.items():
        assert pipeline["exporters"] == ["otlp_http/newrelic"], name


def test_collector_config_rewrites_metric_timestamps():
    """Kong の古いゲージが弾かれる問題は送り先を変えても消えない。"""
    conf = collector_config(nr_ctx())
    assert "transform/kong" in conf["processors"]
    assert "transform/kong" in conf["service"]["pipelines"]["metrics"]["processors"]


def test_collector_service_mounts_config_and_takes_the_key(compose_of):
    service = compose_of(nr_ctx())["services"]["otel-collector"]
    assert service["volumes"] == [f"./{COLLECTOR_CONFIG}:/etc/otelcol-contrib/config.yaml:ro"]
    assert service["environment"]["NEW_RELIC_LICENSE_KEY"] == "${NEW_RELIC_LICENSE_KEY}"


def test_only_the_otlp_ports_are_published(compose_of):
    doc = compose_of(nr_ctx())
    assert "otel-lgtm" not in doc["services"]
    published = [port.split(":")[1] for port in doc["services"]["otel-collector"]["ports"]]
    assert published == ["4317", "4318"]
