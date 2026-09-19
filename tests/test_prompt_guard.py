import pytest
import yaml
from pydantic import ValidationError

from generator.context import PROMPT_GUARD_DENY_PATTERNS, build_context
from generator.render import render_all
from generator.schema import EnvSpec
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl


def kong_yaml_of(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)["config/kong/kong.yaml"])


def kongctl_of(ctx) -> dict:
    return load_kongctl(render_all(ctx)["config/kongctl.yaml"])


def plugin_named(doc: dict, name: str) -> dict | None:
    for plugin in doc.get("plugins") or []:
        if plugin.get("name") == name:
            return plugin
    return None


def guard_of_v2(ctx) -> dict:
    gateway = kongctl_of(ctx)["ai_gateways"][0]
    return next(p for p in gateway["policies"] if p["type"] == "ai-prompt-guard")


def test_prompt_guard_defaults_to_none():
    spec = EnvSpec.model_validate({"customer": "acme", "gateway": "ai-gateway-v2"})
    assert spec.ai.prompt_guard.value == "none"


def test_prompt_guard_is_rejected_on_api_gateway():
    with pytest.raises(ValidationError, match="ai.prompt_guard"):
        EnvSpec.model_validate(
            {"customer": "acme", "gateway": "api-gateway", "ai": {"prompt_guard": "block"}}
        )


def test_observe_is_rejected_on_ai_gateway_v1():
    with pytest.raises(ValidationError, match="observe"):
        EnvSpec.model_validate(
            {
                "customer": "acme",
                "gateway": "ai-gateway-v1",
                "ai": {"providers": [AZURE_PROVIDER], "prompt_guard": "observe"},
            }
        )


def test_block_is_allowed_on_ai_gateway_v1():
    spec = EnvSpec.model_validate(
        {
            "customer": "acme",
            "gateway": "ai-gateway-v1",
            "ai": {"providers": [AZURE_PROVIDER], "prompt_guard": "block"},
        }
    )
    assert spec.ai.prompt_guard.value == "block"


def test_v2_emits_no_plugins_when_prompt_guard_is_none():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    assert "plugins" not in kongctl_of(ctx)["ai_gateways"][0]


def test_v2_observe_lets_detected_prompts_through():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "observe"},
    )
    guard = guard_of_v2(ctx)
    assert guard["global"] is True
    assert guard["enabled"] is True
    assert guard["config"]["continue_on_detection"] is True
    assert guard["config"]["rejection_mode"] == "verbose"
    # 生成物の model は formats: openai で出ているので、検査もその形に合わせる
    assert guard["config"]["llm_format"] == "openai"


def test_v2_block_stops_detected_prompts():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "block"},
    )
    assert guard_of_v2(ctx)["config"]["continue_on_detection"] is False


def test_v1_omits_the_fields_kong_3_14_does_not_have():
    # continue_on_detection と rejection_mode は kong-gateway:3.16 の
    # ai-prompt-guard には無く、出すと kong config parse が unknown field で落ちる
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "block"},
    )
    guard = plugin_named(kong_yaml_of(ctx), "ai-prompt-guard")
    assert guard is not None
    assert guard["config"]["llm_format"] == "openai"
    assert "continue_on_detection" not in guard["config"]
    assert "rejection_mode" not in guard["config"]


def test_v1_keeps_opentelemetry_alongside_the_guard():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "block"},
    )
    doc = kong_yaml_of(ctx)
    assert plugin_named(doc, "opentelemetry") is not None
    assert plugin_named(doc, "ai-prompt-guard") is not None


def test_v1_emits_the_guard_alone_when_observability_is_off():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "block"},
        observability={"backend": "none"},
    )
    assert [p["name"] for p in kong_yaml_of(ctx)["plugins"]] == ["ai-prompt-guard"]


def test_plugins_stay_empty_when_nothing_is_enabled():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [AZURE_PROVIDER]},
        observability={"backend": "none"},
    )
    assert kong_yaml_of(ctx)["plugins"] == []


def test_both_gateways_render_the_same_deny_patterns():
    v1 = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "block"},
    )
    v2 = ctx_for(
        gateway="ai-gateway-v2",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "block"},
    )
    v1_patterns = plugin_named(kong_yaml_of(v1), "ai-prompt-guard")["config"]["deny_patterns"]
    v2_patterns = guard_of_v2(v2)["config"]["deny_patterns"]
    assert v1_patterns == v2_patterns == list(PROMPT_GUARD_DENY_PATTERNS)


def test_ai_gateway_v2_runs_the_pinned_image():
    assert build_context(
        EnvSpec.model_validate({"customer": "acme", "gateway": "ai-gateway-v2"})
    ).kong.image == "kong/kong-ai-gateway:2.0.3"


def readme_of(ctx) -> str:
    return render_all(ctx)["README.md"]


def test_readme_lists_the_guard_as_off_without_explaining_it():
    # 構成テーブルは IdP やキャッシュと同じく軸を必ず 1 行出す。解説の節だけ出さない
    readme = readme_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert "| Prompt Guard | なし |" in readme
    assert "## Prompt Guard" not in readme


def test_readme_shows_the_mode_in_the_summary_table():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "observe"},
    )
    assert "| Prompt Guard | observe" in readme_of(ctx)


def test_readme_warns_that_v1_cannot_observe():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [AZURE_PROVIDER], "prompt_guard": "block"},
    )
    assert "検知したら必ずブロック" in readme_of(ctx)
