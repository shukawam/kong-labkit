import pytest
import yaml
from pydantic import ValidationError

from generator.render import render_all
from generator.schema import EnvSpec
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl


def v2(**overrides):
    return ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}, **overrides)


def gateway_of(ctx) -> dict:
    return load_kongctl(render_all(ctx)["config/kongctl.yaml"])["ai_gateways"][0]


def kong_image_of(ctx) -> str:
    return yaml.safe_load(render_all(ctx)["compose.yaml"])["services"]["kong"]["image"]


def test_default_image_and_no_runtime_pin():
    ctx = v2()
    assert kong_image_of(ctx) == "kong/kong-ai-gateway:2.0.3"
    gateway = gateway_of(ctx)
    assert "min_runtime_version" not in gateway
    assert "runtime_auto_upgrade" not in gateway


def test_gateway_version_pins_the_data_plane_image():
    assert kong_image_of(v2(gateway_version="2.2.0")) == "kong/kong-ai-gateway:2.2.0"


def test_gateway_version_pins_the_ai_gateway_runtime_to_major_minor():
    gateway = gateway_of(v2(gateway_version="2.2.0"))
    # 既定は作成時点の最新 GA（2026-11 時点で 2.3）。2.2 の DP は CP と版が完全一致しないと同期を拒む
    assert gateway["min_runtime_version"] == "2.2"
    # 自動昇格を残すと、接続した DP 次第で最低版が上がり、2.2 に戻せなくなる
    assert gateway["runtime_auto_upgrade"] is False


def test_gateway_version_accepts_a_hotfix_suffix():
    ctx = v2(gateway_version="2.2.0-mcpfix.1")
    assert kong_image_of(ctx) == "kong/kong-ai-gateway:2.2.0-mcpfix.1"
    assert gateway_of(ctx)["min_runtime_version"] == "2.2"


@pytest.mark.parametrize("gateway", ["ai-gateway-v1", "api-gateway"])
def test_gateway_version_on_v1_only_swaps_the_image_tag(gateway):
    ctx = ctx_for(gateway=gateway, gateway_version="3.14.0.0", ai={"providers": [AZURE_PROVIDER]})
    compose = yaml.safe_load(render_all(ctx)["compose.yaml"])["services"]
    images = [s["image"] for s in compose.values() if s.get("image", "").startswith("kong/kong-gateway")]
    assert images == ["kong/kong-gateway:3.14.0.0"]


@pytest.mark.parametrize("value", ["2.2", "latest", "v2.2.0", "2.2.0 ", "2..0"])
def test_gateway_version_rejects_floating_or_malformed_tags(value):
    with pytest.raises(ValidationError, match="gateway_version"):
        EnvSpec.model_validate({"customer": "acme", "gateway": "ai-gateway-v2", "gateway_version": value})


def test_konnect_cp_name_is_used_verbatim():
    ctx = v2(konnect_cp_name="bluesky-ai-gateway-v2")
    gateway = gateway_of(ctx)
    assert gateway["ref"] == gateway["name"] == gateway["display_name"] == "bluesky-ai-gateway-v2"
    assert ctx.certs.common_name == "bluesky-ai-gateway-v2"
    # namespace は customer のまま。ここが変わると別環境の sync が削除対象を取り違える
    doc = load_kongctl(render_all(ctx)["config/kongctl.yaml"])
    assert doc["_defaults"]["kongctl"]["namespace"] == "acme"


def test_konnect_cp_name_and_konnect_name_are_exclusive():
    with pytest.raises(ValidationError, match="konnect_cp_name"):
        EnvSpec.model_validate({
            "customer": "acme", "gateway": "ai-gateway-v2",
            "konnect_name": "bluesky", "konnect_cp_name": "bluesky-ai-gateway-v2",
        })


def test_konnect_cp_name_needs_konnect():
    with pytest.raises(ValidationError, match="konnect_cp_name"):
        EnvSpec.model_validate({
            "customer": "acme", "gateway": "api-gateway", "control_plane": "self-managed",
            "konnect_cp_name": "bluesky-gateway",
        })


@pytest.mark.parametrize("value", ["Bluesky", "blue_sky", "-bluesky", "blue sky"])
def test_konnect_cp_name_must_be_a_safe_ref(value):
    with pytest.raises(ValidationError, match="konnect_cp_name"):
        EnvSpec.model_validate({"customer": "acme", "gateway": "ai-gateway-v2", "konnect_cp_name": value})
