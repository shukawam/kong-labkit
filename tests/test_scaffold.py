import tomllib

import pytest

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for


def test_all_configurations_emit_the_same_top_level_files():
    expected = {"compose.yaml", "mise.toml", "README.md", ".env", ".env.example", ".gitignore", "docs/.gitkeep"}
    for ctx in (
        ctx_for(),
        ctx_for(control_plane="self-managed"),
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}),
    ):
        assert expected <= set(render_all(ctx)), ctx.spec.gateway


def test_mise_is_valid_toml_with_expected_tasks():
    doc = tomllib.loads(render_all(ctx_for())["mise.toml"])
    tasks = {name.removeprefix("tasks.") for name in doc["tasks"]}
    assert tasks == {"up", "down", "reset", "certs", "setup", "sync", "diff", "logs", "smoke"}


def test_mise_loads_dotenv():
    doc = tomllib.loads(render_all(ctx_for())["mise.toml"])
    assert doc["env"]["mise"]["file"] == ".env"


def test_sync_uses_kongctl_for_konnect_ai_gateway_v2():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    doc = tomllib.loads(render_all(ctx)["mise.toml"])
    run = doc["tasks"]["sync"]["run"]
    assert "kongctl sync konnect -f config/kongctl.yaml" in run
    assert "--auto-approve" in run


def test_sync_uses_deck_for_self_managed():
    doc = tomllib.loads(render_all(ctx_for(control_plane="self-managed"))["mise.toml"])
    assert "deck gateway sync" in doc["tasks"]["sync"]["run"]
    assert "config/kong/kong.yaml" in doc["tasks"]["sync"]["run"]


def test_smoke_targets_the_allocated_proxy_port():
    doc = tomllib.loads(render_all(ctx_for())["mise.toml"])
    assert "localhost:8000/httpbin/status/200" in doc["tasks"]["smoke"]["run"]


def test_smoke_uses_a_token_when_the_route_is_protected():
    # idp 有効時は upstream が openid-connect で保護されるため、無認証の curl -f は必ず 401 で落ちる
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    run = tomllib.loads(render_all(ctx)["mise.toml"])["tasks"]["smoke"]["run"]
    assert ctx.idp.token_endpoint in run
    assert f"client_id={ctx.idp.public_client_id}" in run
    assert 'Authorization: Bearer $TOKEN' in run


def test_smoke_without_idp_stays_anonymous():
    run = tomllib.loads(render_all(ctx_for())["mise.toml"])["tasks"]["smoke"]["run"]
    assert "Bearer" not in run
    assert "curl -sS -f http://localhost:8000/httpbin/status/200" in run


def test_smoke_for_entra_id_only_checks_the_status_code():
    # 無人で direct access grants を通せないため、トークン取得を試みてはいけない
    run = tomllib.loads(render_all(ctx_for(idp={"type": "entra-id"}))["mise.toml"])["tasks"][
        "smoke"
    ]["run"]
    assert "Bearer" not in run
    assert "%{http_code}" in run


def test_smoke_task_is_valid_shell():
    import subprocess

    for ctx in (
        ctx_for(),
        ctx_for(idp={"type": "keycloak", "realm": "acme"}),
        ctx_for(idp={"type": "entra-id"}),
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}),
    ):
        run = tomllib.loads(render_all(ctx)["mise.toml"])["tasks"]["smoke"]["run"]
        result = subprocess.run(["bash", "-n"], input=run, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


def test_konnect_tasks_use_kongctl_with_the_declared_region_and_certificate_boundary():
    for gateway in ("api-gateway", "ai-gateway-v1", "ai-gateway-v2"):
        ctx = ctx_for(gateway=gateway, region="eu", ai={"providers": [AZURE_PROVIDER]})
        doc = tomllib.loads(render_all(ctx)["mise.toml"])
        for task in ("sync", "diff"):
            run = doc["tasks"][task]["run"]
            assert f"kongctl {task} konnect -f config/kongctl.yaml" in run
            assert '--pat "$KONNECT_PAT"' in run
            assert "--base-url https://eu.api.konghq.com" in run
            assert "--base-dir ." in run
            assert "config/kong/kong.yaml" not in run


def test_deck_tasks_export_the_prefixed_environment_variables():
    ctx = ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    tasks = tomllib.loads(render_all(ctx)["mise.toml"])["tasks"]
    for task in ("sync", "diff"):
        assert tasks[task]["run"].startswith('DECK_AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" ')


def test_konnect_readme_bootstraps_with_existing_local_certificates_before_starting():
    for gateway in ("api-gateway", "ai-gateway-v1", "ai-gateway-v2"):
        files = render_all(ctx_for(gateway=gateway, ai={"providers": [AZURE_PROVIDER]}))
        body = files["README.md"]
        first_steps = body.split("## 初手")[1].split("## エンドポイント")[0]
        assert first_steps.index("mise run sync") < first_steps.index("mise run up")
        assert "mise run certs" not in first_steps
        assert "config/kongctl.yaml" in first_steps
        assert "ローカルで作成済み" in first_steps
        tools = body.split("必要なツールは ")[1].split(" です。")[0].split("、")
        assert "kongctl" in tools
        assert ("deck" in tools) == (gateway != "ai-gateway-v2")
        certs_task = tomllib.loads(files["mise.toml"])["tasks"]["certs"]["run"]
        assert "mise run sync" in certs_task


def test_konnect_environment_has_a_pat_slot_and_readme_says_where_to_get_it():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    files = render_all(ctx)
    assert 'KONNECT_PAT=""' in files[".env"]
    assert "KONNECT_PAT" in files["README.md"]
    assert "Personal Access Token" in files["README.md"]
    assert "$KONNECT_PAT" in tomllib.loads(files["mise.toml"])["tasks"]["sync"]["run"]


def test_certs_task_only_for_konnect():
    konnect = tomllib.loads(render_all(ctx_for())["mise.toml"])
    assert ".certs" in konnect["tasks"]["certs"]["run"]

    self_managed = tomllib.loads(render_all(ctx_for(control_plane="self-managed"))["mise.toml"])
    assert "config/kong/certs" in self_managed["tasks"]["certs"]["run"]


def test_certs_task_paths_come_from_the_context():
    for ctx in (ctx_for(), ctx_for(control_plane="self-managed")):
        run = tomllib.loads(render_all(ctx)["mise.toml"])["tasks"]["certs"]["run"]
        assert ctx.certs.crt_path in run
        assert ctx.certs.key_path in run
        assert f"/CN={ctx.certs.common_name}/" in run
        assert f"subjectAltName=DNS:{ctx.certs.common_name}" in run


def test_env_file_lists_every_required_variable():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    body = render_all(ctx)[".env"]
    for key in ctx.env_vars:
        assert f'{key}="' in body, key


def test_env_example_has_every_key_with_empty_value():
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    body = render_all(ctx)[".env.example"]
    for key in ctx.env_vars:
        assert f'{key}=""' in body, key


def test_env_keeps_known_defaults_but_blanks_secrets():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}, idp={"type": "keycloak", "realm": "acme"})
    body = render_all(ctx)[".env"]
    assert 'KEYCLOAK_ADMIN="admin"' in body
    assert 'AZURE_OPENAI_API_KEY=""' in body


def test_gitignore_protects_secrets_and_certs():
    body = render_all(ctx_for())[".gitignore"]
    assert ".env" in body
    assert ".certs/" in body


def test_readme_mentions_resolved_values():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        region="eu",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
        idp={"type": "keycloak", "realm": "acme"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
    )
    body = render_all(ctx)["README.md"]
    assert "acme-ai-gateway" in body
    assert "eu.cp.konghq.com" in body
    assert "mise run up" in body
    assert "http://localhost:3000" in body


def test_readme_troubleshooting_is_configuration_specific():
    konnect = render_all(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))["README.md"]
    assert "クラスタ証明書" in konnect
    assert "kong migrations bootstrap" not in konnect

    self_managed = render_all(ctx_for(control_plane="self-managed"))["README.md"]
    assert "kong migrations bootstrap" in self_managed

    keycloak = render_all(ctx_for(idp={"type": "keycloak", "realm": "acme"}))["README.md"]
    assert "KC_HOSTNAME" in keycloak


def test_readme_has_no_hard_wrapped_paragraphs():
    # 段落は 1 行で書く方針。表とリストとコードブロック以外に途中改行を作らない
    body = render_all(ctx_for())["README.md"]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.endswith("、") or stripped.endswith("し、"):
            raise AssertionError(f"段落が途中で改行されています: {line}")


@pytest.mark.parametrize(
    "overrides",
    [
        {"idp": {"type": "ldap"}, "cache": {"type": "redis"}},
        {"idp": {"type": "keycloak", "realm": "acme"}, "cache": {"type": "redis"}},
        {"cache": {"type": "redis"}},
    ],
    ids=["ldap", "keycloak", "none"],
)
def test_readme_endpoint_table_is_not_split_by_a_blank_line(overrides):
    # 条件分岐で行を足すとき、表の途中に空行を残すと以降の行が表から外れる
    body = render_all(ctx_for(**overrides))["README.md"]
    table = body.split("## エンドポイント", 1)[1].split("##", 1)[0]
    rows = [line for line in table.strip().splitlines() if line.strip()]
    assert len(rows) == len(table.strip().splitlines()), table
    assert all(row.startswith("|") for row in rows), table
