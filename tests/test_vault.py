import yaml

from generator.render import render_all
from tests.conftest import ctx_for


def vault_ctx(**overrides):
    return ctx_for(idp={"type": "ldap"}, secrets={"backend": "vault"}, **overrides)


def compose_doc(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)["compose.yaml"])


def test_vault_services_are_absent_by_default():
    services = compose_doc(ctx_for())["services"]
    assert "vault" not in services
    assert "vault-bootstrap" not in services


def test_vault_runs_in_dev_mode_with_the_lab_token():
    vault = compose_doc(vault_ctx())["services"]["vault"]
    assert vault["environment"]["VAULT_DEV_ROOT_TOKEN_ID"] == "${VAULT_TOKEN:-local-dev-token}"
    assert vault["ports"] == ["8200:8200"]
    assert vault["healthcheck"]["test"] == ["CMD", "vault", "status"]


def test_bootstrap_puts_every_seed_into_the_kv():
    bootstrap = compose_doc(vault_ctx())["services"]["vault-bootstrap"]
    script = bootstrap["entrypoint"][-1]
    assert 'vault kv put -mount=secret kong/ldap ldap_password="local-dev-password"' in script
    assert bootstrap["restart"] == "no"
    assert bootstrap["depends_on"] == {"vault": {"condition": "service_healthy"}}


def test_bootstrap_receives_env_backed_secrets():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [{"type": "openai", "models": [{"name": "gpt-5-6"}]}]},
        secrets={"backend": "vault"},
    )
    bootstrap = compose_doc(ctx)["services"]["vault-bootstrap"]
    assert bootstrap["environment"]["OPENAI_API_KEY"] == "${OPENAI_API_KEY}"
    assert 'header_value="Bearer ${OPENAI_API_KEY}"' in bootstrap["entrypoint"][-1]


def test_data_plane_is_wired_to_the_vault():
    # ctx.services では選択したテンプレート名が "kong-dp" だが、Konnect DP のサービスキーは
    # 既存の compose 上では "gateway"（README や test_render_compose.py と同じ命名）
    env = compose_doc(vault_ctx())["services"]["gateway"]["environment"]
    assert env["KONG_VAULT_HCV_HOST"] == "vault"
    assert env["KONG_VAULT_HCV_MOUNT"] == "secret"
    # Kong の既定は v1。dev モードの Vault は KV v2 なので外すと全参照が壊れる
    assert env["KONG_VAULT_HCV_KV"] == "v2"
    assert env["KONG_VAULT_HCV_TOKEN"] == "${VAULT_TOKEN:-local-dev-token}"


def test_data_plane_waits_for_the_bootstrap():
    dp = compose_doc(vault_ctx())["services"]["gateway"]
    assert dp["depends_on"]["vault-bootstrap"] == {"condition": "service_completed_successfully"}


def test_no_vault_env_on_the_data_plane_by_default():
    env = compose_doc(ctx_for())["services"]["gateway"]["environment"]
    assert not [k for k in env if k.startswith("KONG_VAULT_")]
