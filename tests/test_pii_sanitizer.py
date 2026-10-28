import pytest
from pydantic import ValidationError

from generator.render import render_all
from generator.schema import EnvSpec
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl
from tests.test_prompt_compressor import compose_services, policy_of


def v2_ctx(**ai):
    return ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER], **ai})


def test_pii_sanitizer_defaults_to_off():
    spec = EnvSpec.model_validate({"customer": "acme", "gateway": "ai-gateway-v2"})
    assert spec.ai.pii_sanitizer is False


@pytest.mark.parametrize("gateway", ["ai-gateway-v1", "api-gateway"])
def test_pii_sanitizer_is_rejected_outside_v2(gateway):
    with pytest.raises(ValidationError, match="ai.pii_sanitizer"):
        EnvSpec.model_validate(
            {
                "customer": "acme",
                "gateway": gateway,
                "ai": {"providers": [AZURE_PROVIDER], "pii_sanitizer": True},
            }
        )


def test_nothing_is_emitted_when_off():
    ctx = v2_ctx()
    files = render_all(ctx)
    assert "pii-sanitizer" not in compose_services(ctx)
    assert policy_of(ctx, "ai-sanitizer") is None
    assert not any(path.startswith("build/pii-sanitizer/") for path in files)


def test_policy_masks_and_lets_the_request_through():
    ctx = v2_ctx(pii_sanitizer=True)
    policy = policy_of(ctx, "ai-sanitizer")
    assert policy["global"] is True
    assert policy["enabled"] is True
    config = policy["config"]
    assert config["host"] == "pii-sanitizer"
    assert config["port"] == 8080
    assert config["scheme"] == "http"
    assert config["sanitization_mode"] == "INPUT"
    assert config["anonymize"] == ["all"]
    assert config["block_if_detected"] is False
    assert config["recover_redacted"] is False


def test_image_is_built_locally_with_japanese_and_english_models():
    ctx = v2_ctx(pii_sanitizer=True)
    files = render_all(ctx)
    dockerfile = files["build/pii-sanitizer/Dockerfile"]
    assert "FROM kong/ai-pii-service:" in dockerfile
    assert "-ja" in dockerfile.splitlines()[0] or "-ja\n" in dockerfile
    assert "spacy download en_core_web_lg" in dockerfile
    assert "nlp_engine_conf.yml" in dockerfile
    conf = files["build/pii-sanitizer/nlp_engine_conf.yml"]
    assert "lang_code: ja" in conf and "ja_core_news_lg" in conf
    assert "lang_code: en" in conf and "en_core_web_lg" in conf

    service = compose_services(ctx)["pii-sanitizer"]
    assert service["build"] == "./build/pii-sanitizer"
    assert "image" not in service


def test_data_plane_waits_for_the_sanitizer():
    services = compose_services(v2_ctx(pii_sanitizer=True))
    assert "healthcheck" in services["pii-sanitizer"]
    assert services["kong"]["depends_on"]["pii-sanitizer"]["condition"] == "service_healthy"


def test_data_plane_waits_for_both_sidecars_when_combined():
    services = compose_services(v2_ctx(pii_sanitizer=True, prompt_compressor=True))
    assert set(services["kong"]["depends_on"]) == {"pii-sanitizer", "ai-compressor"}


def test_readme_explains_the_sanitizer_only_when_on():
    readme_on = render_all(v2_ctx(pii_sanitizer=True))["README.md"]
    assert "| PII Sanitizer | 有効 |" in readme_on
    assert "## PII Sanitizer" in readme_on
    assert "docker login" not in readme_on.split("## PII Sanitizer")[1].split("\n## ")[0]

    readme_off = render_all(v2_ctx())["README.md"]
    assert "| PII Sanitizer | なし |" in readme_off
    assert "## PII Sanitizer" not in readme_off


def test_policies_block_appears_with_sanitizer_alone():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        ai={"providers": [AZURE_PROVIDER], "pii_sanitizer": True},
        observability={"backend": "none"},
    )
    gateway = load_kongctl(render_all(ctx)["config/kongctl.yaml"])["ai_gateways"][0]
    assert [p["type"] for p in gateway["policies"]] == ["ai-sanitizer"]
