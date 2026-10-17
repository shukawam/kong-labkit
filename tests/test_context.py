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
    assert build_context(spec_from(gateway="ai-gateway-v2")).kong.image == "kong/kong-ai-gateway:2.0.3"
    assert build_context(spec_from(gateway="ai-gateway-v1")).kong.image == "kong/kong-gateway:3.16"
    assert build_context(spec_from(gateway="api-gateway")).kong.image == "kong/kong-gateway:3.16"


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
    # client_secret は kong.yaml では使わなくなった。参照は client_secret_ref 側に出る
    assert idp.client_secret_ref == '${{ env "DECK_AZURE_CLIENT_SECRET" }}'
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
    otel = build_context(spec_from(observability={"backend": "none"})).otel
    assert otel.enabled is False
    assert otel.endpoint is None


def test_newrelic_points_kong_at_the_local_collector():
    """Kong は送信先しか知らない。New Relic への中継は Collector 側の仕事。"""
    otel = build_context(spec_from(observability={"backend": "newrelic"})).otel
    assert otel.enabled is True
    assert otel.endpoint == "http://otel-collector:4318"
    assert otel.grpc_endpoint == "http://otel-collector:4317"
    assert otel.nr_endpoint == "https://otlp.nr-data.net"


def test_newrelic_eu_region_switches_the_exporter_endpoint():
    otel = build_context(
        spec_from(observability={"backend": "newrelic", "newrelic": {"region": "eu"}})
    ).otel
    assert otel.nr_endpoint == "https://otlp.eu01.nr-data.net"


def test_newrelic_replaces_the_lgtm_service():
    services = build_context(spec_from(observability={"backend": "newrelic"})).services
    assert "otel-collector" in services
    assert "otel-lgtm" not in services


def test_newrelic_license_key_is_requested_in_env():
    env_vars = build_context(spec_from(observability={"backend": "newrelic"})).env_vars
    assert env_vars["NEW_RELIC_LICENSE_KEY"] == ""
    assert "NEW_RELIC_LICENSE_KEY" not in build_context(spec_from()).env_vars


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


def test_env_vars_v2_bedrock_needs_aws_keys():
    bedrock = {"type": "bedrock", "region": "ap-northeast-1", "models": [{"name": "claude-opus-5"}]}
    ctx = build_context(spec_from(gateway="ai-gateway-v2", ai={"providers": [bedrock]}))
    assert ctx.env_vars["AWS_ACCESS_KEY_ID"] == ""
    assert ctx.env_vars["AWS_SECRET_ACCESS_KEY"] == ""


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


def test_ldap_context_derives_dns_from_the_customer():
    ldap = build_context(spec_from(idp={"type": "ldap"})).idp.ldap
    assert ldap.base_dn == "dc=acme,dc=local"
    assert ldap.users_dn == "ou=people,dc=acme,dc=local"
    assert ldap.groups_dn == "ou=groups,dc=acme,dc=local"
    # lldap は ldap_user_dn に素の名前を受け取り、cn=<name>,ou=people,<base> を作る
    assert ldap.bind_dn == "cn=admin,ou=people,dc=acme,dc=local"
    assert ldap.host == "lldap"
    assert ldap.port == 3890
    assert ldap.attribute == "uid"


def test_ldap_idp_is_enabled_but_has_no_oidc_fields():
    idp = build_context(spec_from(idp={"type": "ldap"})).idp
    assert idp.enabled is True
    assert idp.auth_plugin == "ldap-auth-advanced"
    assert idp.issuer is None
    assert idp.client_id is None
    assert idp.token_endpoint is None


def test_oidc_idps_expose_the_openid_connect_plugin():
    assert build_context(spec_from(idp={"type": "keycloak", "realm": "acme"})).idp.auth_plugin == (
        "openid-connect"
    )
    assert build_context(spec_from(idp={"type": "entra-id"})).idp.auth_plugin == "openid-connect"
    assert build_context(spec_from()).idp.auth_plugin is None


DEV_AND_RESEARCHER = {
    "type": "ldap",
    "users": [
        {"name": "developer", "email": "developer@example.com", "groups": ["developer-dep"]},
        {
            "name": "researcher",
            "email": "researcher@example.com",
            "groups": ["developer-dep", "researcher-dep"],
        },
    ],
}


def test_ldap_defaults_to_a_single_test_user():
    ldap = build_context(spec_from(idp={"type": "ldap"})).idp.ldap
    assert [u.name for u in ldap.users] == ["tester"]
    assert ldap.users[0].password == "tester-password"
    assert ldap.users[0].email == "tester@example.com"
    assert ldap.groups == ("acme-ai-users",)


def test_ldap_groups_are_derived_from_users_without_duplicates():
    ldap = build_context(spec_from(idp=DEV_AND_RESEARCHER)).idp.ldap
    assert [u.name for u in ldap.users] == ["developer", "researcher"]
    # 宣言順を保ったまま重複を潰す
    assert ldap.groups == ("developer-dep", "researcher-dep")
    assert ldap.users[1].groups == ("developer-dep", "researcher-dep")


def test_ldap_user_email_and_password_have_derivable_defaults():
    ldap = build_context(spec_from(idp={"type": "ldap", "users": [{"name": "developer"}]})).idp.ldap
    assert ldap.users[0].email == "developer@example.com"
    assert ldap.users[0].password == "developer-password"


def test_ldap_smoke_uses_the_first_user():
    idp = build_context(spec_from(idp=DEV_AND_RESEARCHER)).idp
    assert idp.test_username == "developer"
    assert idp.test_password == "developer-password"


def test_ldap_services_include_the_one_shot_bootstrap():
    ctx = build_context(spec_from(idp={"type": "ldap"}))
    assert ctx.services == ["kong-dp", "httpbin", "lldap", "lldap-bootstrap", "otel-lgtm"]


def test_env_vars_ldap_are_deterministic_local_values():
    ctx = build_context(spec_from(idp={"type": "ldap"}))
    # 乱数にするとゴールデンテストが毎回落ちるので固定値にしている
    assert ctx.env_vars["LLDAP_ADMIN_USERNAME"] == "admin"
    assert ctx.env_vars["LLDAP_ADMIN_PASSWORD"] == "local-dev-password"
    assert ctx.env_vars["LLDAP_JWT_SECRET"] == "local-dev-jwt-secret"
    assert all(value for key, value in ctx.env_vars.items() if key.startswith("LLDAP_"))
    assert "KEYCLOAK_ADMIN" not in ctx.env_vars


def test_ldap_bind_password_matches_the_dotenv_default():
    ctx = build_context(spec_from(idp={"type": "ldap"}))
    assert ctx.idp.ldap.bind_password == ctx.env_vars["LLDAP_ADMIN_PASSWORD"]


def test_konnect_name_replaces_only_the_control_plane_name():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v1",
            konnect_name="bluesky",
            idp={"type": "ldap"},
            ai={"providers": [AZURE_PROVIDER]},
        )
    )
    assert ctx.kong.cp_name == "bluesky-ai-gateway"
    # 出力先と顧客側の識別子は customer のまま
    assert ctx.namespace == "acme"
    assert ctx.idp.ldap.base_dn == "dc=acme,dc=local"
    assert ctx.idp.ldap.groups == ("acme-ai-users",)


def test_konnect_name_keeps_the_gateway_suffix():
    assert build_context(spec_from(konnect_name="bluesky")).kong.cp_name == "bluesky-gateway"
    assert build_context(
        spec_from(gateway="ai-gateway-v2", konnect_name="bluesky")
    ).kong.cp_name == "bluesky-ai-gateway"


def test_konnect_name_does_not_touch_the_keycloak_client_ids():
    idp = build_context(
        spec_from(konnect_name="bluesky", idp={"type": "keycloak", "realm": "acme"})
    ).idp
    assert idp.client_id == "acme-client"
    assert idp.public_client_id == "acme-public"


def test_certs_common_name_follows_the_control_plane_name():
    assert build_context(spec_from(konnect_name="bluesky")).certs.common_name == "bluesky-gateway"


OPENAI_PROVIDER = {"type": "openai", "models": [{"name": "gpt-5-6"}]}


def aigw_spec(**overrides):
    return spec_from(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]}, **overrides)


def test_provider_auth_uses_deck_env_by_default():
    auth = build_context(aigw_spec()).deck.provider_auth[0]
    assert auth["header_value"] == "'${{ env \"DECK_AZURE_OPENAI_API_KEY\" }}'"


def test_provider_auth_uses_a_vault_reference():
    ctx = build_context(aigw_spec(secrets={"backend": "vault"}))
    assert ctx.deck.provider_auth[0]["header_value"] == "'{vault://hcv/kong/ai-azure/header_value}'"
    # vault 構成では decK に渡す環境変数が要らなくなる
    assert ctx.deck.env_aliases == {}


def test_vault_seeds_carry_the_env_var_to_inject():
    ctx = build_context(aigw_spec(secrets={"backend": "vault"}))
    seed = ctx.secrets.seeds[0]
    assert (seed.path, seed.key, seed.env_var) == ("ai-azure", "header_value", "AZURE_OPENAI_API_KEY")
    assert seed.shell_value == "${AZURE_OPENAI_API_KEY}"


def test_openai_bearer_prefix_moves_into_the_vault_value():
    """Kong は値の一部に埋めた参照を解決しない。Bearer ごと Vault に入れる必要がある。"""
    ctx = build_context(
        spec_from(gateway="ai-gateway-v1", ai={"providers": [OPENAI_PROVIDER]}, secrets={"backend": "vault"})
    )
    assert ctx.deck.provider_auth[0]["header_value"] == "'{vault://hcv/kong/ai-openai/header_value}'"
    assert ctx.secrets.seeds[0].shell_value == "Bearer ${OPENAI_API_KEY}"


def test_openai_bearer_prefix_stays_inline_for_the_env_backend():
    auth = build_context(
        spec_from(gateway="ai-gateway-v1", ai={"providers": [OPENAI_PROVIDER]})
    ).deck.provider_auth[0]
    assert auth["header_value"] == "'Bearer ${{ env \"DECK_OPENAI_API_KEY\" }}'"


def test_identifiers_stay_on_the_env_backend_even_with_vault():
    """client_id / tenant_id は秘密ではないので Vault に入れない。"""
    provider = {**AZURE_PROVIDER, "auth": "managed-identity"}
    # managed-identity だけでは秘密が無く vault 構成が成立しないので、IdP 側で 1 つ持たせる
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v1",
            ai={"providers": [provider]},
            idp={"type": "ldap"},
            secrets={"backend": "vault"},
        )
    )
    auth = ctx.deck.provider_auth[0]
    assert auth["azure_client_id"] == "'${{ env \"DECK_AZURE_CLIENT_ID\" }}'"
    assert [s.path for s in ctx.secrets.seeds] == ["ldap"]


def test_secrets_context_is_disabled_by_default():
    secrets = build_context(spec_from()).secrets
    assert secrets.enabled is False
    assert secrets.seeds == ()


def test_secrets_context_describes_the_vault():
    secrets = build_context(aigw_spec(secrets={"backend": "vault"})).secrets
    assert (secrets.enabled, secrets.host, secrets.mount, secrets.prefix) == (
        True,
        "vault",
        "secret",
        "kong",
    )
    assert secrets.env_sources == ("AZURE_OPENAI_API_KEY",)


def test_ldap_password_is_a_literal_by_default():
    idp = build_context(spec_from(idp={"type": "ldap"})).idp
    assert idp.ldap.bind_password_ref == idp.ldap.bind_password


def test_ldap_password_becomes_a_vault_reference():
    ctx = build_context(spec_from(idp={"type": "ldap"}, secrets={"backend": "vault"}))
    assert ctx.idp.ldap.bind_password_ref == "{vault://hcv/kong/ldap/ldap_password}"
    # lldap コンテナ自身は実値が要るので、こちらは平文のまま残る
    assert ctx.idp.ldap.bind_password == "local-dev-password"
    seed = next(s for s in ctx.secrets.seeds if s.path == "ldap")
    assert (seed.key, seed.literal) == ("ldap_password", "local-dev-password")


def test_keycloak_client_secret_becomes_a_vault_reference():
    ctx = build_context(
        spec_from(idp={"type": "keycloak", "realm": "acme"}, secrets={"backend": "vault"})
    )
    assert ctx.idp.client_secret_ref == "{vault://hcv/kong/oidc/client_secret}"
    # realm-export.json は Keycloak 側の設定なので実値が要る
    assert ctx.idp.client_secret == "local-dev-secret"


def test_entra_client_secret_becomes_a_vault_reference():
    ctx = build_context(spec_from(idp={"type": "entra-id"}, secrets={"backend": "vault"}))
    assert ctx.idp.client_secret_ref == "{vault://hcv/kong/oidc/client_secret}"
    # client_id は秘密ではないので decK の env 参照のまま
    assert ctx.idp.client_id == '${{ env "DECK_AZURE_CLIENT_ID" }}'
