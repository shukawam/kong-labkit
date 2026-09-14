from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from generator.gen import main
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


@pytest.mark.parametrize("gateway", ["api-gateway", "ai-gateway-v1"])
def test_v1_control_plane_pins_the_local_certificate_and_delegates_to_deck(gateway):
    ctx = ctx_for(gateway=gateway, ai={"providers": [AZURE_PROVIDER]})
    doc = kongctl_of(ctx)
    assert "ai_gateways" not in doc
    assert len(doc["control_planes"]) == 1
    cp = doc["control_planes"][0]
    assert cp["ref"] == cp["name"] == ctx.kong.cp_name
    assert cp["cluster_type"] == "CLUSTER_TYPE_CONTROL_PLANE"
    assert cp["auth_type"] == "pinned_client_certs"
    assert cp["data_plane_certificates"] == [
        {
            "ref": f"{ctx.kong.cp_name}-cert",
            "cert": {"__tag__": "!file", "value": f"../{ctx.certs.crt_path}"},
        }
    ]
    assert cp["_deck"]["files"] == ["kong/kong.yaml"]
    assert "config/" + cp["_deck"]["files"][0] in render_all(ctx)


@pytest.mark.parametrize("gateway", ["api-gateway", "ai-gateway-v1"])
def test_self_managed_emits_kongctl_without_konnect_resources(gateway):
    ctx = ctx_for(gateway=gateway, control_plane="self-managed")
    assert kongctl_of(ctx) == {"_defaults": {"kongctl": {"namespace": "acme"}}}


@pytest.mark.parametrize("gateway", ["api-gateway", "ai-gateway-v1", "ai-gateway-v2"])
def test_cli_emits_kongctl_referencing_the_generated_data_plane_certificate(tmp_path, gateway):
    source = tmp_path / "env.yaml"
    source.write_text(f"customer: acme\ngateway: {gateway}\n", encoding="utf-8")
    out = tmp_path / "out"
    result = CliRunner().invoke(main, [str(source), "-o", str(out)])
    assert result.exit_code == 0, result.output
    config = out / "config/kongctl.yaml"
    doc = load_kongctl(config.read_text(encoding="utf-8"))
    resource = "ai_gateways" if gateway == "ai-gateway-v2" else "control_planes"
    cert = doc[resource][0]["data_plane_certificates"][0]["cert"]
    assert cert["__tag__"] == "!file"
    cert_file = (config.parent / cert["value"]).resolve()
    assert cert_file == out / ".certs/cluster.crt"
    assert cert_file.read_text().startswith("-----BEGIN CERTIFICATE-----")

    # 公開証明書だけを登録し、対応する秘密鍵は Data Plane のマウント元に残す。
    compose = yaml.safe_load((out / "compose.yaml").read_text())
    dp = next(
        service
        for service in compose["services"].values()
        if "KONG_CLUSTER_CERT" in service.get("environment", {})
    )
    cert_dest = Path(dp["environment"]["KONG_CLUSTER_CERT"])
    assert f".certs:{cert_dest.parent}" in dp["volumes"]
    assert cert_dest.name == cert_file.name
    key_dest = Path(dp["environment"]["KONG_CLUSTER_CERT_KEY"])
    assert key_dest.parent == cert_dest.parent
    assert (cert_file.parent / key_dest.name).is_file()
    assert ".key" not in config.read_text()


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
    assert cert["cert"] == {"__tag__": "!file", "value": "../.certs/cluster.crt"}


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


def test_balancer_follows_the_semantic_flags_not_the_vectordb():
    # vectordb を置いただけで semantic 設定が出ると、フラグが何も効いていないのと同じ
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": False, "semantic_routing": False},
    )
    assert "balancer" not in kongctl_of(ctx)["ai_gateways"][0]["models"][0]["config"]


def test_semantic_routing_alone_enables_the_balancer():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        vectordb={"type": "pgvector"},
        ai={"providers": [AZURE_PROVIDER], "semantic_routing": True},
    )
    balancer = kongctl_of(ctx)["ai_gateways"][0]["models"][0]["config"]["balancer"]
    assert balancer["algorithm"] == "semantic"


def test_pgvector_vectordb_block_matches_the_deck_side():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        vectordb={"type": "pgvector"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
    )
    vectordb = kongctl_of(ctx)["ai_gateways"][0]["models"][0]["config"]["balancer"]["vectordb"]
    assert vectordb["database"] == ctx.vector.database
    assert vectordb["user"] == ctx.vector.user
    assert vectordb["password"] == ctx.vector.password


def test_redis_vectordb_block_has_no_database_fields():
    doc = kongctl_of(ctx_for(**{k: v for k, v in SEMANTIC.items() if k != "customer"}))
    vectordb = doc["ai_gateways"][0]["models"][0]["config"]["balancer"]["vectordb"]
    assert "database" not in vectordb
    assert "user" not in vectordb


def test_konnect_name_renames_the_control_plane_entity():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v1", konnect_name="bluesky",
                             ai={"providers": [AZURE_PROVIDER]}))
    cp = doc["control_planes"][0]
    assert cp["ref"] == cp["name"] == "bluesky-ai-gateway"
    # namespace は customer のまま。動かすと sync 済みのリソースが管理外に見える
    assert doc["_defaults"]["kongctl"]["namespace"] == "acme"


def test_konnect_name_renames_the_ai_gateway_entity():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", konnect_name="bluesky",
                             ai={"providers": [AZURE_PROVIDER]}))
    gateway = doc["ai_gateways"][0]
    assert gateway["ref"] == gateway["name"] == gateway["display_name"] == "bluesky-ai-gateway"
    assert gateway["models"][0]["ai_gateway"]["value"] == "bluesky-ai-gateway#id"
