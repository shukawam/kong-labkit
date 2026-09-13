import yaml

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for


def kong_yaml_of(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)["config/kong/kong.yaml"])


def test_emitted_for_api_gateway_and_aigw_v1_only():
    assert "config/kong/kong.yaml" in render_all(ctx_for())
    assert "config/kong/kong.yaml" in render_all(
        ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    )
    assert "config/kong/kong.yaml" not in render_all(
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    )


def test_format_version():
    assert kong_yaml_of(ctx_for())["_format_version"] == "3.0"


def test_api_gateway_routes_to_httpbin():
    doc = kong_yaml_of(ctx_for())
    service = doc["services"][0]
    assert service["name"] == "httpbin"
    assert service["url"] == "http://httpbin:80"
    assert service["routes"][0]["paths"] == ["/httpbin"]
    assert service["routes"][0]["strip_path"] is True


def test_api_gateway_without_upstream_has_no_services():
    doc = kong_yaml_of(ctx_for(upstream="none"))
    assert doc.get("services", []) == []


def test_openid_connect_plugin_uses_resolved_issuer():
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = kong_yaml_of(ctx)
    plugin = next(p for p in doc["services"][0]["plugins"] if p["name"] == "openid-connect")
    assert plugin["config"]["issuer"] == "http://keycloak:8080/realms/acme"
    assert plugin["config"]["client_id"] == ["acme-client"]
    assert plugin["config"]["client_secret"] == ["${KEYCLOAK_CLIENT_SECRET}"]


def test_no_openid_connect_when_idp_none():
    doc = kong_yaml_of(ctx_for())
    assert all(p["name"] != "openid-connect" for p in doc["services"][0].get("plugins", []))


def test_opentelemetry_plugin_is_global_when_otel_enabled():
    doc = kong_yaml_of(ctx_for())
    plugin = next(p for p in doc["plugins"] if p["name"] == "opentelemetry")
    assert plugin["config"]["traces_endpoint"] == "http://otel-lgtm:4318/v1/traces"
    assert plugin["config"]["logs_endpoint"] == "http://otel-lgtm:4318/v1/logs"


def test_no_global_plugins_when_otel_disabled():
    doc = kong_yaml_of(ctx_for(observability={"otel_lgtm": False}))
    assert doc.get("plugins", []) == []


def test_aigw_v1_uses_ai_proxy_advanced():
    ctx = ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    doc = kong_yaml_of(ctx)
    service = doc["services"][0]
    assert service["name"] == "llm"
    plugin = next(p for p in service["plugins"] if p["name"] == "ai-proxy-advanced")
    target = plugin["config"]["targets"][0]
    assert target["model"]["provider"] == "azure"
    assert target["model"]["name"] == "gpt-5-6"
    assert target["model"]["options"]["azure_deployment_id"] == "gpt-5.6"
    assert target["route_type"] == "llm/v1/chat"


def test_aigw_v1_semantic_cache_plugin_points_at_vectordb():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
    )
    doc = kong_yaml_of(ctx)
    plugin = next(
        p for p in doc["services"][0]["plugins"] if p["name"] == "ai-semantic-cache"
    )
    assert plugin["config"]["vectordb"]["strategy"] == "redis"
    assert plugin["config"]["vectordb"]["redis"]["host"] == "redis"
    assert plugin["config"]["vectordb"]["dimensions"] == 3072


def test_aigw_v1_without_semantic_cache_has_no_cache_plugin():
    ctx = ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    doc = kong_yaml_of(ctx)
    assert all(
        p["name"] != "ai-semantic-cache" for p in doc["services"][0].get("plugins", [])
    )
