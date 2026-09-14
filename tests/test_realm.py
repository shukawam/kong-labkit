import json

from generator.render import render_all
from tests.conftest import ctx_for


def realm_of(ctx) -> dict:
    return json.loads(render_all(ctx)["config/keycloak/realm-export.json"])


def test_realm_is_emitted_only_for_keycloak():
    # upstream="none": デフォルトの api-gateway は httpbin を services に含めるが、
    # そのパーシャルは Task 10 が作る。ここでは realm-export.json の有無だけを見たい
    assert "config/keycloak/realm-export.json" in render_all(
        ctx_for(idp={"type": "keycloak", "realm": "acme"}, upstream="none")
    )
    assert "config/keycloak/realm-export.json" not in render_all(ctx_for(upstream="none"))
    assert "config/keycloak/realm-export.json" not in render_all(
        ctx_for(idp={"type": "entra-id"}, upstream="none")
    )


def test_realm_name_and_enabled():
    realm = realm_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}, upstream="none"))
    assert realm["realm"] == "acme"
    assert realm["enabled"] is True


def test_confidential_client_uses_env_secret():
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"}, upstream="none")
    realm = realm_of(ctx)
    confidential = next(c for c in realm["clients"] if c["clientId"] == "acme-client")
    assert confidential["publicClient"] is False
    assert confidential["secret"] == ctx.env_vars["KEYCLOAK_CLIENT_SECRET"]
    assert confidential["serviceAccountsEnabled"] is True
    assert confidential["standardFlowEnabled"] is True


def test_public_client_for_browser_flows():
    realm = realm_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}, upstream="none"))
    public = next(c for c in realm["clients"] if c["clientId"] == "acme-public")
    assert public["publicClient"] is True
    assert "http://localhost:8000/*" in public["redirectUris"]


def test_test_user_is_present_with_password():
    realm = realm_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}, upstream="none"))
    user = next(u for u in realm["users"] if u["username"] == "tester")
    assert user["enabled"] is True
    assert user["credentials"][0]["value"] == "tester"
    assert user["credentials"][0]["temporary"] is False


def test_client_scope_is_declared():
    realm = realm_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}, upstream="none"))
    assert "acme-scope" in [s["name"] for s in realm["clientScopes"]]
