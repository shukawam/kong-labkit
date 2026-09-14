import tomllib

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for


def setup_of(ctx) -> str:
    doc = tomllib.loads(render_all(ctx)["mise.toml"])
    return doc["tasks"]["setup"]["run"]


def index_of(body: str, needle: str) -> int:
    assert needle in body, f"{needle!r} が setup タスクにありません:\n{body}"
    return body.index(needle)


def test_setup_exists_for_every_control_plane():
    assert setup_of(ctx_for())
    assert setup_of(ctx_for(control_plane="self-managed"))


def test_setup_reuses_an_existing_cluster_certificate():
    ctx = ctx_for()
    body = setup_of(ctx)
    # 作り直すと Konnect への再登録と Data Plane の再起動が要る
    assert f"if [ ! -f {ctx.certs.crt_path} ]" in body
    assert index_of(body, "mise run certs") < index_of(body, "kongctl sync")


def test_konnect_setup_syncs_before_starting_the_data_plane():
    body = setup_of(ctx_for())
    assert index_of(body, "kongctl sync") < index_of(body, "docker compose up -d")


def test_konnect_setup_resolves_the_control_plane_id_between_sync_and_up():
    body = setup_of(ctx_for())
    resolve = index_of(body, "kongctl get konnect")
    assert index_of(body, "kongctl sync") < resolve < index_of(body, "docker compose up -d")


def test_konnect_setup_only_rewrites_the_control_plane_id_line():
    body = setup_of(ctx_for())
    assert "^CONTROL_PLANE_ID=" in body
    for key in ("KONNECT_PAT", "AZURE_OPENAI_API_KEY", "LLDAP_ADMIN_PASSWORD"):
        assert f"s|^{key}=" not in body
    # 行が無い .env を黙って書き換えない
    assert index_of(body, "grep -q '^CONTROL_PLANE_ID='") < index_of(body, "sed ")


def test_aigw_v2_resolves_the_id_from_the_ai_gateway_entity():
    body = setup_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert "kongctl get konnect ai-gateway acme-ai-gateway" in body
    assert "EP=.endpoints.configuration" in body


def test_v1_resolves_the_id_from_the_gateway_control_plane():
    body = setup_of(ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]}))
    assert "kongctl get konnect gateway control-planes acme-ai-gateway" in body
    assert "EP=.config.control_plane_endpoint" in body


def test_konnect_name_is_what_setup_looks_up():
    body = setup_of(ctx_for(konnect_name="bluesky"))
    assert "kongctl get konnect gateway control-planes bluesky-gateway" in body


def test_self_managed_setup_starts_the_control_plane_before_syncing():
    body = setup_of(ctx_for(control_plane="self-managed"))
    assert index_of(body, "docker compose up -d") < index_of(body, "deck gateway sync")
    # ローカル CP は Admin API が開くまで sync を受け付けない
    assert index_of(body, "localhost:8001") < index_of(body, "deck gateway sync")
    assert "kongctl get konnect" not in body
    assert "CONTROL_PLANE_ID" not in body


def test_setup_does_not_run_smoke_itself():
    body = setup_of(ctx_for())
    assert "curl" not in body.split("docker compose up -d")[1]
    assert "mise run smoke" in body
