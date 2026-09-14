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


def test_api_gateway_route_allows_plaintext_http():
    doc = kong_yaml_of(ctx_for())
    assert "http" in doc["services"][0]["routes"][0]["protocols"]


def test_api_gateway_without_upstream_has_no_services():
    doc = kong_yaml_of(ctx_for(upstream="none"))
    assert doc.get("services", []) == []


def test_openid_connect_plugin_uses_resolved_issuer():
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = kong_yaml_of(ctx)
    plugin = next(p for p in doc["services"][0]["plugins"] if p["name"] == "openid-connect")
    assert plugin["config"]["issuer"] == "http://keycloak:8080/realms/acme"
    assert plugin["config"]["client_id"] == ["acme-client"]
    # decK は ${VAR} を展開しないため、ローカル固定値は実値で埋める
    assert plugin["config"]["client_secret"] == ["local-dev-secret"]


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


def test_aigw_v1_chat_route_allows_plaintext_http():
    ctx = ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    doc = kong_yaml_of(ctx)
    assert "http" in doc["services"][0]["routes"][0]["protocols"]


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


def test_entra_id_openid_connect_uses_deck_env_references():
    # decK は ${VAR} を展開しない。展開されない値のまま sync すると認証が原理的に成立しない
    doc = kong_yaml_of(ctx_for(idp={"type": "entra-id"}))
    plugin = next(p for p in doc["services"][0]["plugins"] if p["name"] == "openid-connect")
    config = plugin["config"]
    assert config["issuer"] == (
        'https://login.microsoftonline.com/${{ env "DECK_AZURE_TENANT_ID" }}/v2.0'
    )
    assert config["client_id"] == ['${{ env "DECK_AZURE_CLIENT_ID" }}']
    assert config["client_secret"] == ['${{ env "DECK_AZURE_CLIENT_SECRET" }}']


def test_entra_id_never_references_the_keycloak_secret():
    body = render_all(ctx_for(idp={"type": "entra-id"}))["config/kong/kong.yaml"]
    assert "KEYCLOAK_CLIENT_SECRET" not in body


def test_aigw_v1_azure_auth_uses_deck_env_reference():
    ctx = ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    target = kong_yaml_of(ctx)["services"][0]["plugins"][0]["config"]["targets"][0]
    assert target["auth"]["header_name"] == "api-key"
    assert target["auth"]["header_value"] == '${{ env "DECK_AZURE_OPENAI_API_KEY" }}'


def test_aigw_v1_non_azure_providers_get_their_own_credentials():
    # LLM_API_KEY は .env のどこにも生成されないため、azure 以外は全て 401 になっていた
    anthropic = {"type": "anthropic", "models": [{"name": "claude-opus-5"}]}
    bedrock = {"type": "bedrock", "region": "us-east-1", "models": [{"name": "nova"}]}
    ctx = ctx_for(gateway="ai-gateway-v1", ai={"providers": [anthropic, bedrock]})
    targets = kong_yaml_of(ctx)["services"][0]["plugins"][0]["config"]["targets"]
    assert targets[0]["auth"] == {
        "header_name": "x-api-key",
        "header_value": '${{ env "DECK_ANTHROPIC_API_KEY" }}',
    }
    assert targets[1]["auth"] == {
        "aws_access_key_id": '${{ env "DECK_AWS_ACCESS_KEY_ID" }}',
        "aws_secret_access_key": '${{ env "DECK_AWS_SECRET_ACCESS_KEY" }}',
    }
    body = render_all(ctx)["config/kong/kong.yaml"]
    assert "LLM_API_KEY" not in body


def test_deck_env_references_have_a_matching_dotenv_entry():
    anthropic = {"type": "anthropic", "models": [{"name": "claude-opus-5"}]}
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        idp={"type": "entra-id"},
        ai={"providers": [anthropic]},
    )
    body = render_all(ctx)["config/kong/kong.yaml"]
    dotenv = render_all(ctx)[".env"]
    for alias, source in ctx.deck.env_aliases.items():
        assert f'env "{alias}"' in body, alias
        assert f"{source}=" in dotenv, source


def test_semantic_cache_plugin_follows_the_flag_not_the_vectordb():
    without_flag = ctx_for(
        gateway="ai-gateway-v1",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": False},
    )
    doc = kong_yaml_of(without_flag)
    assert all(
        p["name"] != "ai-semantic-cache" for p in doc["services"][0].get("plugins", [])
    )


def test_semantic_cache_embeddings_auth_follows_the_provider():
    anthropic = {"type": "anthropic", "models": [{"name": "claude-opus-5"}]}
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
        ai={"providers": [anthropic], "semantic_cache": True},
    )
    plugin = next(
        p for p in kong_yaml_of(ctx)["services"][0]["plugins"] if p["name"] == "ai-semantic-cache"
    )
    embeddings = plugin["config"]["embeddings"]
    assert embeddings["model"]["provider"] == "anthropic"
    assert embeddings["auth"] == {
        "header_name": "x-api-key",
        "header_value": '${{ env "DECK_ANTHROPIC_API_KEY" }}',
    }


def test_semantic_cache_pgvector_block_carries_credentials():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        vectordb={"type": "pgvector"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
    )
    plugin = next(
        p for p in kong_yaml_of(ctx)["services"][0]["plugins"] if p["name"] == "ai-semantic-cache"
    )
    pgvector = plugin["config"]["vectordb"]["pgvector"]
    assert pgvector["database"] == ctx.vector.database
    assert pgvector["user"] == ctx.vector.user
    assert pgvector["password"] == ctx.vector.password


def test_ldap_auth_advanced_binds_as_the_lldap_admin():
    ctx = ctx_for(idp={"type": "ldap"})
    doc = kong_yaml_of(ctx)
    plugin = next(p for p in doc["services"][0]["plugins"] if p["name"] == "ldap-auth-advanced")
    config = plugin["config"]
    assert config["ldap_host"] == "lldap"
    assert config["ldap_port"] == 3890
    assert config["base_dn"] == "ou=people,dc=acme,dc=local"
    assert config["bind_dn"] == "cn=admin,ou=people,dc=acme,dc=local"
    assert config["ldap_password"] == "local-dev-password"
    assert config["attribute"] == "uid"


def test_ldap_auth_advanced_maps_groups_to_consumers():
    doc = kong_yaml_of(ctx_for(idp={"type": "ldap"}))
    config = next(
        p for p in doc["services"][0]["plugins"] if p["name"] == "ldap-auth-advanced"
    )["config"]
    assert config["consumer_by"] == ["username"]
    assert config["consumer_optional"] is True
    assert config["group_base_dn"] == "ou=groups,dc=acme,dc=local"
    assert config["group_member_attribute"] == "member"


def test_ldap_declares_a_consumer_per_user():
    doc = kong_yaml_of(ctx_for(idp={"type": "ldap"}))
    assert [c["username"] for c in doc["consumers"]] == ["tester"]
    doc = kong_yaml_of(
        ctx_for(idp={"type": "ldap", "users": [{"name": "developer"}, {"name": "researcher"}]})
    )
    assert [c["username"] for c in doc["consumers"]] == ["developer", "researcher"]


def test_non_ldap_configs_declare_no_consumers():
    assert "consumers" not in kong_yaml_of(ctx_for())
    assert "consumers" not in kong_yaml_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}))


def test_ldap_and_openid_connect_are_mutually_exclusive():
    ldap = kong_yaml_of(ctx_for(idp={"type": "ldap"}))["services"][0]["plugins"]
    assert all(p["name"] != "openid-connect" for p in ldap)
    keycloak = kong_yaml_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}))["services"][0]
    assert all(p["name"] != "ldap-auth-advanced" for p in keycloak["plugins"])


def test_ldap_protects_the_chat_route_on_aigw_v1():
    ctx = ctx_for(gateway="ai-gateway-v1", idp={"type": "ldap"}, ai={"providers": [AZURE_PROVIDER]})
    plugins = kong_yaml_of(ctx)["services"][0]["plugins"]
    assert any(p["name"] == "ldap-auth-advanced" for p in plugins)
    assert any(p["name"] == "ai-proxy-advanced" for p in plugins)
