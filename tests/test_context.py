import pytest

from generator.context import UnknownEmbeddingModelError, build_context
from generator.schema import EnvSpec


def spec_from(**overrides) -> EnvSpec:
    return EnvSpec.model_validate({"customer": "acme", "gateway": "api-gateway", **overrides})


AZURE_PROVIDER = {
    "type": "azure",
    "instance": "acme-foundry",
    "auth": "api-key",
    "models": [{"name": "gpt-5-6", "deployment_id": "gpt-5.6", "api_version": "2024-12-01-preview"}],
}


def test_kong_image_by_gateway():
    assert build_context(spec_from(gateway="ai-gateway-v2")).kong.image == "kong/kong-ai-gateway:2.0.1"
    assert build_context(spec_from(gateway="ai-gateway-v1")).kong.image == "kong/kong-gateway:3.14"
    assert build_context(spec_from(gateway="api-gateway")).kong.image == "kong/kong-gateway:3.14"


def test_konnect_domains_use_region():
    kong = build_context(spec_from(region="eu")).kong
    assert kong.konnect_domain == "eu.cp.konghq.com"
    assert kong.konnect_telemetry_domain == "eu.tp.konghq.com"
    assert kong.konnect_api_url == "https://eu.api.konghq.com"


def test_self_managed_has_no_konnect_domains():
    kong = build_context(spec_from(control_plane="self-managed")).kong
    assert kong.konnect_domain is None
    assert kong.konnect_telemetry_domain is None
    assert kong.konnect_api_url is None


def test_cp_name_differs_between_ai_and_api_gateway():
    assert build_context(spec_from(gateway="ai-gateway-v2")).kong.cp_name == "acme-ai-gateway"
    assert build_context(spec_from(gateway="api-gateway")).kong.cp_name == "acme-gateway"


def test_keycloak_issuer_is_resolved():
    idp = build_context(spec_from(idp={"type": "keycloak", "realm": "acme"})).idp
    assert idp.enabled is True
    assert idp.issuer == "http://keycloak:8080/realms/acme"
    assert idp.client_id == "acme-client"
    assert idp.service_name == "keycloak"


def test_entra_issuer_uses_deck_env_reference():
    # decK は ${VAR} を展開しない。${{ env "DECK_..." }} でなければただの文字列になる
    idp = build_context(spec_from(idp={"type": "entra-id"})).idp
    assert idp.issuer == (
        'https://login.microsoftonline.com/${{ env "DECK_AZURE_TENANT_ID" }}/v2.0'
    )
    assert idp.client_id == '${{ env "DECK_AZURE_CLIENT_ID" }}'
    assert idp.client_secret == '${{ env "DECK_AZURE_CLIENT_SECRET" }}'
    assert idp.service_name is None


def test_keycloak_client_secret_is_the_literal_value():
    idp = build_context(spec_from(idp={"type": "keycloak", "realm": "acme"})).idp
    assert idp.client_secret == "local-dev-secret"
    assert idp.public_client_id == "acme-public"
    assert idp.token_endpoint == (
        "http://localhost:8080/realms/acme/protocol/openid-connect/token"
    )


def test_deck_env_aliases_map_to_dotenv_names():
    ctx = build_context(spec_from(idp={"type": "entra-id"}))
    assert ctx.deck.env_aliases == {
        "DECK_AZURE_TENANT_ID": "AZURE_TENANT_ID",
        "DECK_AZURE_CLIENT_ID": "AZURE_CLIENT_ID",
        "DECK_AZURE_CLIENT_SECRET": "AZURE_CLIENT_SECRET",
    }
    for alias, source in ctx.deck.env_aliases.items():
        assert source in ctx.env_vars
        assert f'{alias}="${source}"' in ctx.deck.env_prefix


def test_ai_gateway_v2_needs_no_deck_aliases():
    ctx = build_context(
        spec_from(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    )
    assert ctx.deck.env_aliases == {}
    assert ctx.deck.env_prefix == ""


def test_deck_provider_auth_differs_per_provider():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v1",
            ai={
                "providers": [
                    AZURE_PROVIDER,
                    {"type": "anthropic", "models": [{"name": "claude-opus-5"}]},
                ]
            },
        )
    )
    azure, anthropic = ctx.deck.provider_auth
    assert azure["header_name"] == "api-key"
    assert azure["header_value"] == '\'${{ env "DECK_AZURE_OPENAI_API_KEY" }}\''
    assert anthropic["header_name"] == "x-api-key"
    assert anthropic["header_value"] == '\'${{ env "DECK_ANTHROPIC_API_KEY" }}\''


def test_certs_common_name_differs_by_control_plane():
    konnect = build_context(spec_from(gateway="ai-gateway-v2")).certs
    assert konnect.common_name == "acme-ai-gateway"
    assert konnect.crt_path == ".certs/cluster.crt"
    assert konnect.key_path == ".certs/cluster.key"

    self_managed = build_context(spec_from(control_plane="self-managed")).certs
    # shared mTLS は CN をこの固定リテラルとしか照合しない
    assert self_managed.common_name == "kong_clustering"
    assert self_managed.crt_path == "config/kong/certs/tls.crt"


def test_semantic_switches_are_independent():
    def semantic_of(**ai):
        return build_context(
            spec_from(
                gateway="ai-gateway-v2",
                vectordb={"type": "pgvector"},
                ai={"providers": [AZURE_PROVIDER], **ai},
            )
        ).semantic

    assert semantic_of() == semantic_of(semantic_cache=False, semantic_routing=False)
    assert semantic_of().enabled is False
    assert semantic_of(semantic_cache=True).cache is True
    assert semantic_of(semantic_cache=True).routing is False
    assert semantic_of(semantic_routing=True).routing is True
    assert semantic_of(semantic_routing=True).cache is False


def test_pgvector_password_comes_from_the_context():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            vectordb={"type": "pgvector"},
            ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
        )
    )
    assert ctx.vector.password == "kong"
    assert ctx.vector.database == "vectors"
    assert ctx.vector.user == "kong"


def test_konnect_env_has_a_slot_for_the_pat():
    ctx = build_context(spec_from(gateway="ai-gateway-v2"))
    assert ctx.env_vars["KONNECT_PAT"] == ""
    assert "KONNECT_PAT" not in build_context(spec_from(control_plane="self-managed")).env_vars


def test_idp_none():
    idp = build_context(spec_from()).idp
    assert idp.enabled is False
    assert idp.issuer is None


def test_otel_endpoints():
    otel = build_context(spec_from()).otel
    assert otel.enabled is True
    assert otel.endpoint == "http://otel-lgtm:4318"
    assert otel.grpc_endpoint == "http://otel-lgtm:4317"


def test_otel_disabled():
    otel = build_context(spec_from(observability={"otel_lgtm": False})).otel
    assert otel.enabled is False
    assert otel.endpoint is None


def test_cache_image_by_type():
    assert build_context(spec_from(cache={"type": "redis"})).cache.image == "redis:8.0.2"
    assert build_context(spec_from(cache={"type": "redis-stack"})).cache.image == "redis/redis-stack:7.4.0-v3"
    assert build_context(spec_from()).cache.enabled is False


def test_vector_redis_stack_points_at_cache_service():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            cache={"type": "redis-stack"},
            vectordb={"type": "redis-stack"},
            ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
        )
    )
    assert ctx.vector.host == "redis"
    assert ctx.vector.port == 6379
    assert ctx.vector.dimensions == 3072
    assert ctx.vector.distance_metric == "cosine"


def test_vector_pgvector_uses_allocated_port():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            vectordb={"type": "pgvector"},
            ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
        )
    )
    assert ctx.vector.host == "pgvector"
    assert ctx.vector.port == 5432  # コンテナ内ポートなので退避の影響を受けない
    assert ctx.ports["vector_pg"] == 5432


def test_embedding_dimensions_for_small_model():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            cache={"type": "redis-stack"},
            vectordb={"type": "redis-stack"},
            ai={
                "providers": [AZURE_PROVIDER],
                "semantic_cache": True,
                "embedding_model": "text-embedding-3-small",
            },
        )
    )
    assert ctx.vector.dimensions == 1536


def test_unknown_embedding_model_is_rejected():
    with pytest.raises(UnknownEmbeddingModelError) as e:
        build_context(
            spec_from(
                gateway="ai-gateway-v2",
                cache={"type": "redis-stack"},
                vectordb={"type": "redis-stack"},
                ai={
                    "providers": [AZURE_PROVIDER],
                    "semantic_cache": True,
                    "embedding_model": "made-up-model",
                },
            )
        )
    assert "made-up-model" in str(e.value)
    assert "EMBEDDING_DIMENSIONS" in str(e.value)


def test_upstream_httpbin():
    up = build_context(spec_from()).upstream
    assert up.enabled is True
    assert up.url == "http://httpbin:80"


def test_services_order_konnect_ai_gateway_v2():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            cache={"type": "redis-stack"},
            vectordb={"type": "redis-stack"},
            idp={"type": "keycloak", "realm": "acme"},
            ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
        )
    )
    assert ctx.services == ["kong-aigw-v2", "redis", "keycloak", "otel-lgtm"]


def test_services_order_self_managed_api_gateway():
    ctx = build_context(
        spec_from(
            control_plane="self-managed",
            vectordb={"type": "pgvector"},
            idp={"type": "keycloak", "realm": "acme"},
        )
    )
    assert ctx.services == ["kong-self-managed", "httpbin", "pgvector", "keycloak", "otel-lgtm"]


def test_services_for_ai_gateway_v1_use_konnect_dp():
    ctx = build_context(spec_from(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]}))
    assert ctx.services == ["kong-dp", "otel-lgtm"]


def test_env_vars_konnect_azure_api_key():
    ctx = build_context(spec_from(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert ctx.env_vars["CONTROL_PLANE_ID"] == ""
    assert ctx.env_vars["AZURE_OPENAI_API_KEY"] == ""


def test_env_vars_self_managed_needs_license():
    ctx = build_context(spec_from(control_plane="self-managed"))
    assert ctx.env_vars["KONG_LICENSE_DATA"] == ""
    assert "CONTROL_PLANE_ID" not in ctx.env_vars


def test_env_vars_keycloak_has_deterministic_secret():
    ctx = build_context(spec_from(idp={"type": "keycloak", "realm": "acme"}))
    assert ctx.env_vars["KEYCLOAK_ADMIN"] == "admin"
    assert ctx.env_vars["KEYCLOAK_CLIENT_SECRET"] == "local-dev-secret"


def test_env_vars_entra_are_blank():
    ctx = build_context(spec_from(idp={"type": "entra-id"}))
    assert ctx.env_vars["AZURE_TENANT_ID"] == ""
    assert ctx.env_vars["AZURE_CLIENT_ID"] == ""
    assert ctx.env_vars["AZURE_CLIENT_SECRET"] == ""


def test_namespace_is_customer():
    assert build_context(spec_from()).namespace == "acme"
