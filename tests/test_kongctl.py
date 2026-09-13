import pytest

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl

SEMANTIC = {
    "customer": "acme",
    "gateway": "ai-gateway-v2",
    "cache": {"type": "redis-stack"},
    "vectordb": {"type": "redis-stack"},
    "ai": {"providers": [AZURE_PROVIDER], "semantic_cache": True},
}


def kongctl_of(ctx) -> dict:
    return load_kongctl(render_all(ctx)["config/kongctl.yaml"])


def test_emitted_only_for_ai_gateway_v2():
    assert "config/kongctl.yaml" in render_all(
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    )
    assert "config/kongctl.yaml" not in render_all(ctx_for(upstream="none"))
    assert "config/kongctl.yaml" not in render_all(
        ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    )


def test_namespace_is_customer():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert doc["_defaults"]["kongctl"]["namespace"] == "acme"


def test_ai_gateway_name_matches_cp_name():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    doc = kongctl_of(ctx)
    gw = doc["ai_gateways"][0]
    assert gw["name"] == ctx.kong.cp_name == "acme-ai-gateway"


def test_azure_provider_uses_api_key_from_env():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    provider = doc["ai_gateways"][0]["model_providers"][0]
    assert provider["type"] == "azure"
    assert provider["config"]["instance"] == "acme-foundry"
    header = provider["config"]["auth"]["headers"][0]
    assert header["name"] == "api-key"
    assert header["value"] == {"__tag__": "!env", "value": "AZURE_OPENAI_API_KEY"}


def test_azure_managed_identity_provider():
    provider_spec = {**AZURE_PROVIDER, "auth": "managed-identity"}
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [provider_spec]}))
    auth = doc["ai_gateways"][0]["model_providers"][0]["config"]["auth"]
    assert auth["type"] == "azure"
    assert auth["use_managed_identity"] is True


def test_model_route_path_is_derived_from_model_name():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    model = doc["ai_gateways"][0]["models"][0]
    assert model["name"] == "gpt-5-6"
    assert model["config"]["route"]["paths"] == ["/v1/gpt-5-6"]
    assert model["config"]["route"]["strip_path"] is True
    assert model["formats"] == [{"type": "openai"}]
    assert model["capabilities"] == ["generate"]


def test_model_target_carries_deployment_id_and_api_version():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    target = doc["ai_gateways"][0]["models"][0]["targets"][0]
    assert target["provider"] == "azure"
    assert target["config"]["deployment_id"] == "gpt-5.6"
    assert target["config"]["api_version"] == "2024-12-01-preview"


def test_no_balancer_without_semantic_features():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert "balancer" not in doc["ai_gateways"][0]["models"][0]["config"]


def test_semantic_balancer_wires_vectordb():
    doc = kongctl_of(ctx_for(**{k: v for k, v in SEMANTIC.items() if k != "customer"}))
    balancer = doc["ai_gateways"][0]["models"][0]["config"]["balancer"]
    assert balancer["algorithm"] == "semantic"
    assert balancer["embeddings"]["name"] == "text-embedding-3-large"
    vectordb = balancer["vectordb"]
    assert vectordb["type"] == "redis"
    assert vectordb["host"] == "redis"
    assert vectordb["port"] == 6379
    assert vectordb["dimensions"] == 3072
    assert vectordb["distance_metric"] == "cosine"


def test_semantic_balancer_with_pgvector():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        vectordb={"type": "pgvector"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
    )
    vectordb = kongctl_of(ctx)["ai_gateways"][0]["models"][0]["config"]["balancer"]["vectordb"]
    assert vectordb["type"] == "pgvector"
    assert vectordb["host"] == "pgvector"
    assert vectordb["port"] == 5432


def test_data_plane_certificate_references_generated_file():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    cert = doc["ai_gateways"][0]["data_plane_certificates"][0]
    assert cert["cert"] == {"__tag__": "!file", "value": ".certs/cluster.crt"}


def test_multiple_providers_emit_multiple_model_providers():
    bedrock = {
        "type": "bedrock",
        "region": "us-east-1",
        "models": [{"name": "claude-opus-5"}],
    }
    doc = kongctl_of(
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER, bedrock]})
    )
    gw = doc["ai_gateways"][0]
    assert [p["type"] for p in gw["model_providers"]] == ["azure", "bedrock"]
    assert [m["name"] for m in gw["models"]] == ["gpt-5-6", "claude-opus-5"]
