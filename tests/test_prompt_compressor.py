import pytest
import yaml
from pydantic import ValidationError

from generator.render import render_all
from generator.schema import EnvSpec
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl


def v2_ctx(**ai):
    return ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER], **ai})


def policy_of(ctx, type_: str) -> dict | None:
    gateway = load_kongctl(render_all(ctx)["config/kongctl.yaml"])["ai_gateways"][0]
    return next((p for p in gateway.get("policies") or [] if p["type"] == type_), None)


def compose_services(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)["compose.yaml"])["services"]


def test_prompt_compressor_defaults_to_off():
    spec = EnvSpec.model_validate({"customer": "acme", "gateway": "ai-gateway-v2"})
    assert spec.ai.prompt_compressor is False


@pytest.mark.parametrize("gateway", ["ai-gateway-v1", "api-gateway"])
def test_prompt_compressor_is_rejected_outside_v2(gateway):
    with pytest.raises(ValidationError, match="ai.prompt_compressor"):
        EnvSpec.model_validate(
            {
                "customer": "acme",
                "gateway": gateway,
                "ai": {"providers": [AZURE_PROVIDER], "prompt_compressor": True},
            }
        )


def test_no_sidecar_and_no_policy_when_off():
    ctx = v2_ctx()
    assert "ai-compressor" not in compose_services(ctx)
    assert policy_of(ctx, "ai-prompt-compressor") is None


def test_policy_points_at_the_sidecar():
    ctx = v2_ctx(prompt_compressor=True)
    policy = policy_of(ctx, "ai-prompt-compressor")
    assert policy["global"] is True
    assert policy["enabled"] is True
    config = policy["config"]
    assert config["provider"] == "kong"
    assert config["compressor_url"] == "http://ai-compressor:8080"
    assert "ai-compressor" in compose_services(ctx)


def test_short_prompts_are_left_alone():
    config = policy_of(v2_ctx(prompt_compressor=True), "ai-prompt-compressor")["config"]
    assert config["compressor_type"] == "rate"
    assert config["message_type"] == ["user"]
    ranges = config["compression_ranges"]
    assert min(r["min_tokens"] for r in ranges) == 100
    # 長いほど強く圧縮する（value が小さいほど残す割合が小さい）
    assert [r["value"] for r in sorted(ranges, key=lambda r: r["min_tokens"])] == [0.8, 0.5]


def test_cpu_inference_gets_a_longer_timeout():
    config = policy_of(v2_ctx(prompt_compressor=True), "ai-prompt-compressor")["config"]
    assert config["timeout"] == 30000
    assert config["stop_on_error"] is False


def test_sidecar_runs_on_cpu_and_the_data_plane_waits_for_it():
    services = compose_services(v2_ctx(prompt_compressor=True))
    assert services["ai-compressor"]["environment"]["LLMLINGUA_DEVICE_MAP"] == "cpu"
    assert "healthcheck" in services["ai-compressor"]
    assert services["kong"]["depends_on"]["ai-compressor"]["condition"] == "service_healthy"


def test_readme_lists_the_compressor():
    readme = render_all(v2_ctx(prompt_compressor=True))["README.md"]
    assert "| Prompt Compressor | 有効 |" in readme
    assert "## Prompt Compressor" in readme
    assert "docker.cloudsmith.io" in readme


def test_readme_lists_the_compressor_as_off_without_explaining_it():
    readme = render_all(v2_ctx())["README.md"]
    assert "| Prompt Compressor | なし |" in readme
    assert "## Prompt Compressor" not in readme
