import tomllib

import pytest
import yaml
from pydantic import ValidationError

from generator.render import render_all
from generator.schema import AuthType, EnvSpec
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl

VLLM_CHAT = {
    "type": "vllm",
    "models": [
        {"name": "gemma4-31b", "upstream_url": "http://host.docker.internal:8001/v1/chat/completions"}
    ],
}

OPENAI_EMBEDDINGS = {
    "type": "openai",
    "auth": "none",
    "models": [
        {
            "name": "harrier-oss-v1-0-6b",
            "model_id": "microsoft/harrier-oss-v1-0.6b",
            "capabilities": ["embeddings"],
            "upstream_url": "http://host.docker.internal:8002/v1/embeddings",
        }
    ],
}

VLLM_RERANK = {
    "name": "ruri-v3-reranker-310m",
    "model_id": "cl-nagoya/ruri-v3-reranker-310m",
    "capabilities": ["rerank"],
    "formats": ["passthrough"],
    "upstream_url": "http://host.docker.internal:8003/v1/rerank",
}


def spec(*providers, gateway="ai-gateway-v2"):
    return EnvSpec.model_validate({"customer": "acme", "gateway": gateway, "ai": {"providers": list(providers)}})


def v2(*providers, **overrides):
    return ctx_for(gateway="ai-gateway-v2", ai={"providers": list(providers)}, **overrides)


def gateway_of(ctx) -> dict:
    return load_kongctl(render_all(ctx)["config/kongctl.yaml"])["ai_gateways"][0]


def model_named(ctx, name) -> dict:
    return next(m for m in gateway_of(ctx)["models"] if m["name"] == name)


def with_rerank():
    return {**VLLM_CHAT, "models": [*VLLM_CHAT["models"], VLLM_RERANK]}


# --- schema ---


def test_vllm_defaults_to_no_auth():
    provider = spec(VLLM_CHAT).ai.providers[0]
    assert provider.auth is AuthType.NONE


def test_other_providers_still_default_to_api_key():
    assert spec(AZURE_PROVIDER).ai.providers[0].auth is AuthType.API_KEY


def test_vllm_needs_an_upstream_url():
    with pytest.raises(ValidationError, match="upstream_url"):
        spec({"type": "vllm", "models": [{"name": "gemma4-31b"}]})


def test_vllm_with_an_api_key_is_rejected():
    with pytest.raises(ValidationError, match="auth: api-key"):
        spec({**VLLM_CHAT, "auth": "api-key"})


@pytest.mark.parametrize("provider_type", ["azure", "bedrock", "anthropic"])
def test_auth_none_is_only_for_self_hosted_providers(provider_type):
    with pytest.raises(ValidationError, match="auth: none"):
        spec({"type": provider_type, "auth": "none", "models": [{"name": "m"}]})


def test_vllm_only_generates_through_the_openai_format():
    bad = {**VLLM_CHAT, "models": [{**VLLM_CHAT["models"][0], "capabilities": ["embeddings"]}]}
    with pytest.raises(ValidationError, match="vllm"):
        spec(bad)


def test_rerank_needs_passthrough():
    bad = {**VLLM_RERANK, "formats": ["openai"]}
    with pytest.raises(ValidationError, match="passthrough"):
        spec({**VLLM_CHAT, "models": [bad]})


def test_passthrough_cannot_be_mixed_with_other_formats():
    bad = {**VLLM_RERANK, "formats": ["passthrough", "openai"]}
    with pytest.raises(ValidationError, match="passthrough"):
        spec({**VLLM_CHAT, "models": [bad]})


def test_vllm_rerank_through_passthrough_is_accepted():
    model = spec(with_rerank()).ai.providers[0].models[1]
    assert [c.value for c in model.capabilities] == ["rerank"]
    assert [f.value for f in model.formats] == ["passthrough"]


@pytest.mark.parametrize("gateway", ["ai-gateway-v1", "api-gateway"])
def test_self_hosted_fields_are_v2_only(gateway):
    with pytest.raises(ValidationError, match="ai-gateway-v2"):
        spec(OPENAI_EMBEDDINGS, gateway=gateway)


@pytest.mark.parametrize("gateway", ["ai-gateway-v1", "api-gateway"])
def test_vllm_is_v2_only(gateway):
    with pytest.raises(ValidationError, match="ai-gateway-v2"):
        spec(VLLM_CHAT, gateway=gateway)


def test_upstream_url_rejects_the_data_plane_itself():
    bad = {**VLLM_CHAT, "models": [{"name": "m", "upstream_url": "http://localhost:8001/v1/chat/completions"}]}
    with pytest.raises(ValidationError, match="localhost"):
        spec(bad)


def test_semantic_features_need_a_provider_that_embeds():
    with pytest.raises(ValidationError, match="vllm"):
        EnvSpec.model_validate({
            "customer": "acme", "gateway": "ai-gateway-v2",
            "cache": {"type": "redis-stack"}, "vectordb": {"type": "redis-stack"},
            "ai": {"providers": [VLLM_CHAT], "semantic_cache": True},
        })


# --- kongctl ---


def test_vllm_provider_has_basic_auth_without_headers():
    provider = gateway_of(v2(VLLM_CHAT))["model_providers"][0]
    assert provider["type"] == "vllm"
    assert provider["config"] == {"auth": {"type": "basic"}}


def test_openai_without_auth_needs_no_key():
    ctx = v2(OPENAI_EMBEDDINGS)
    assert gateway_of(ctx)["model_providers"][0]["config"] == {"auth": {"type": "basic"}}
    assert "OPENAI_API_KEY" not in ctx.env_vars


def test_vllm_target_carries_the_full_upstream_url():
    model = model_named(v2(VLLM_CHAT), "gemma4-31b")
    assert model["capabilities"] == ["generate"]
    assert model["formats"] == [{"type": "openai"}]
    target = model["targets"][0]
    assert target["provider"] == "vllm"
    assert target["config"] == {
        "type": "vllm",
        "upstream_url": "http://host.docker.internal:8001/v1/chat/completions",
    }


def test_embeddings_model_points_at_the_embeddings_endpoint():
    model = model_named(v2(OPENAI_EMBEDDINGS), "harrier-oss-v1-0-6b")
    assert model["capabilities"] == ["embeddings"]
    assert model["formats"] == [{"type": "openai"}]
    assert model["targets"][0]["name"] == "microsoft/harrier-oss-v1-0.6b"
    assert model["targets"][0]["config"]["upstream_url"] == "http://host.docker.internal:8002/v1/embeddings"


def test_rerank_model_is_passthrough():
    model = model_named(v2(with_rerank()), "ruri-v3-reranker-310m")
    assert model["capabilities"] == ["rerank"]
    assert model["formats"] == [{"type": "passthrough"}]
    assert model["targets"][0]["provider"] == "vllm"
    assert model["targets"][0]["config"]["upstream_url"] == "http://host.docker.internal:8003/v1/rerank"
    assert model["config"]["route"]["paths"] == ["/v1/ruri-v3-reranker-310m"]


def test_existing_providers_keep_generate_and_openai():
    model = gateway_of(v2(AZURE_PROVIDER))["models"][0]
    assert model["capabilities"] == ["generate"]
    assert model["formats"] == [{"type": "openai"}]
    assert "upstream_url" not in model["targets"][0]["config"]


def test_prompt_compressor_stays_off_models_that_do_not_chat():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [with_rerank(), OPENAI_EMBEDDINGS], "prompt_compressor": True})
    assert model_named(ctx, "gemma4-31b")["policies"] == ["ai-prompt-compressor"]
    assert "policies" not in model_named(ctx, "ruri-v3-reranker-310m")
    assert "policies" not in model_named(ctx, "harrier-oss-v1-0-6b")


# --- compose / mise ---


def test_data_plane_resolves_the_host_when_an_upstream_lives_there():
    kong = yaml.safe_load(render_all(v2(VLLM_CHAT))["compose.yaml"])["services"]["kong"]
    # Docker Desktop は自前で解決するが、Linux の Docker Engine は host-gateway の明示が要る
    assert kong["extra_hosts"] == ["host.docker.internal:host-gateway"]


def test_no_extra_hosts_for_cloud_providers():
    kong = yaml.safe_load(render_all(v2(AZURE_PROVIDER))["compose.yaml"])["services"]["kong"]
    assert "extra_hosts" not in kong


def smoke_of(ctx) -> str:
    return tomllib.loads(render_all(ctx)["mise.toml"])["tasks"]["smoke"]["run"]


def test_smoke_calls_each_capability_on_its_own_path():
    run = smoke_of(v2(with_rerank(), OPENAI_EMBEDDINGS))
    assert "/v1/gemma4-31b/chat/completions" in run
    assert "/v1/harrier-oss-v1-0-6b/embeddings" in run
    assert '"input"' in run
    assert "/v1/ruri-v3-reranker-310m/rerank" in run
    assert '"documents"' in run
