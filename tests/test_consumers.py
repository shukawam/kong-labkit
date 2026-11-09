import tomllib

import pytest
from pydantic import ValidationError

from generator.render import render_all
from generator.schema import EnvSpec
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl

CONSUMERS = {
    "consumer_groups": [
        {"name": "developer-dep", "rate_limit": {"tokens": 500, "window": 60}},
        {"name": "researcher-dep"},
    ],
    "consumers": [
        {"name": "developer", "groups": ["developer-dep"], "keys": [{"name": "developer"}]},
        {
            "name": "researcher",
            "groups": ["researcher-dep"],
            "keys": [{"name": "researcher"}, {"name": "researcher-short", "ttl": 300}],
        },
    ],
}


def v2(**ai):
    return ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER], **CONSUMERS, **ai})


def spec_with(**ai):
    return EnvSpec.model_validate(
        {"customer": "acme", "gateway": "ai-gateway-v2", "ai": {"providers": [AZURE_PROVIDER], **CONSUMERS, **ai}}
    )


def gateway_of(ctx) -> dict:
    return load_kongctl(render_all(ctx)["config/kongctl.yaml"])["ai_gateways"][0]


def tasks_of(ctx) -> dict:
    return tomllib.loads(render_all(ctx)["mise.toml"])["tasks"]


# --- schema ---


@pytest.mark.parametrize("gateway", ["ai-gateway-v1", "api-gateway"])
def test_consumers_are_v2_only(gateway):
    with pytest.raises(ValidationError, match="ai-gateway-v2"):
        EnvSpec.model_validate({"customer": "acme", "gateway": gateway, "ai": {"providers": [AZURE_PROVIDER], **CONSUMERS}})


def test_consumer_needs_a_key():
    with pytest.raises(ValidationError, match="keys"):
        spec_with(consumers=[{"name": "developer"}])


def test_consumer_group_must_be_declared():
    with pytest.raises(ValidationError, match="unknown-dep"):
        spec_with(consumers=[{"name": "developer", "groups": ["unknown-dep"], "keys": [{"name": "developer"}]}])


def test_key_names_are_unique_across_consumers():
    # キー名が .env の変数名になるので、別の Consumer でも重ねられない
    with pytest.raises(ValidationError, match="shared"):
        spec_with(consumers=[
            {"name": "a", "keys": [{"name": "shared"}]},
            {"name": "b", "keys": [{"name": "shared"}]},
        ], consumer_groups=[])


def test_consumer_names_are_unique():
    with pytest.raises(ValidationError, match="developer"):
        spec_with(consumers=[
            {"name": "developer", "keys": [{"name": "k1"}]},
            {"name": "developer", "keys": [{"name": "k2"}]},
        ], consumer_groups=[])


@pytest.mark.parametrize("name", ["Developer", "dev_1", "-dev", "dev.1"])
def test_names_must_be_safe_refs(name):
    with pytest.raises(ValidationError):
        spec_with(consumers=[{"name": name, "keys": [{"name": "k"}]}], consumer_groups=[])


@pytest.mark.parametrize("ttl", [0, -1])
def test_ttl_must_be_positive(ttl):
    # Konnect では 0 が無期限なので、0 を書いて「すぐ切れる」と読み違えないよう弾く
    with pytest.raises(ValidationError, match="ttl"):
        spec_with(consumers=[{"name": "d", "keys": [{"name": "k", "ttl": ttl}]}], consumer_groups=[])


# --- kongctl ---


def test_key_auth_strategy_is_created_and_every_model_requires_it():
    gateway = gateway_of(v2())
    [strategy] = gateway["auth_strategies"]
    assert strategy["type"] == "key-auth"
    assert strategy["name"] == "key-auth"
    assert strategy["config"]["key_names"] == ["apikey"]
    for model in gateway["models"]:
        assert model["access"] == {"auth_strategies": ["key-auth"]}


def test_no_auth_without_consumers():
    gateway = gateway_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert "auth_strategies" not in gateway
    assert "consumers" not in gateway
    assert all("access" not in m for m in gateway["models"])


def test_consumer_credentials_read_the_key_from_env_as_a_secret():
    consumers = {c["name"]: c for c in gateway_of(v2())["consumers"]}
    assert set(consumers) == {"developer", "researcher"}
    developer = consumers["developer"]
    assert developer["type"] == "api-key"
    [credential] = developer["credentials"]
    assert credential["type"] == "api-key"
    assert credential["name"] == "developer"
    assert credential["api_key"] == {
        "__tag__": "!secret",
        "value": {"source": {"__tag__": "!env", "value": "AI_CONSUMER_KEY_DEVELOPER"}},
    }
    assert "ttl" not in credential


def test_ttl_is_only_written_for_keys_that_expire():
    researcher = next(c for c in gateway_of(v2())["consumers"] if c["name"] == "researcher")
    ttls = {c["name"]: c.get("ttl") for c in researcher["credentials"]}
    assert ttls == {"researcher": None, "researcher-short": 300}


def test_api_key_tag_names_the_env_variable():
    text = render_all(v2())["config/kongctl.yaml"]
    assert "api_key: !secret {source: !env AI_CONSUMER_KEY_DEVELOPER}" in text
    assert "api_key: !secret {source: !env AI_CONSUMER_KEY_RESEARCHER_SHORT}" in text


def test_consumer_groups_list_their_members():
    groups = {g["name"]: g for g in gateway_of(v2())["consumer_groups"]}
    assert groups["developer-dep"]["consumers"] == ["developer"]
    assert groups["researcher-dep"]["consumers"] == ["researcher"]


def test_rate_limit_is_a_group_scoped_token_policy():
    gateway = gateway_of(v2())
    groups = {g["name"]: g for g in gateway["consumer_groups"]}
    assert groups["developer-dep"]["policies"] == ["ai-rate-limiting-advanced-developer-dep"]
    assert "policies" not in groups["researcher-dep"]

    policy = next(p for p in gateway["policies"] if p["name"] == "ai-rate-limiting-advanced-developer-dep")
    assert policy["type"] == "ai-rate-limiting-advanced"
    assert policy["global"] is False
    config = policy["config"]
    # cluster は hybrid では使えない
    assert config["strategy"] == "local"
    [rule] = config["policies"]
    assert rule["match"] == [
        {"type": "consumer_group", "key": "name", "values": ["developer-dep"], "partition_by": True}
    ]
    assert rule["limits"] == [{"limit": 500, "window_size": 60, "tokens_count_strategy": "total_tokens"}]


# --- env / mise / README ---


def test_each_key_gets_an_empty_env_slot():
    files = render_all(v2())
    for name in ("AI_CONSUMER_KEY_DEVELOPER", "AI_CONSUMER_KEY_RESEARCHER", "AI_CONSUMER_KEY_RESEARCHER_SHORT"):
        assert f'{name}=""' in files[".env"]
        assert f'{name}=""' in files[".env.example"]


def test_keys_task_fills_only_empty_keys_without_printing_them():
    run = tasks_of(v2())["keys"]["run"]
    assert "openssl rand" in run
    assert "AI_CONSUMER_KEY_RESEARCHER_SHORT" in run
    assert "echo \"$value" not in run and "echo $value" not in run


def test_keys_task_appends_lines_that_a_forced_regeneration_could_not_add(tmp_path):
    import subprocess

    run = tasks_of(v2())["keys"]["run"]
    env = tmp_path / ".env"
    env.write_text('KONNECT_PAT="pat"\nAI_CONSUMER_KEY_DEVELOPER="keep"\n', encoding="utf-8")
    subprocess.run(["bash", "-c", run], cwd=tmp_path, check=True, capture_output=True)
    lines = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
    assert lines["KONNECT_PAT"] == '"pat"'
    assert lines["AI_CONSUMER_KEY_DEVELOPER"] == '"keep"'
    assert len(lines["AI_CONSUMER_KEY_RESEARCHER"].strip('"')) == 48
    assert len(lines["AI_CONSUMER_KEY_RESEARCHER_SHORT"].strip('"')) == 48


def test_setup_generates_keys_before_sync():
    run = tasks_of(v2())["setup"]["run"]
    assert run.index("mise run keys") < run.index("kongctl sync")


@pytest.mark.parametrize("task", ["sync", "diff"])
def test_sync_refuses_to_run_with_an_empty_key(task):
    run = tasks_of(v2())[task]["run"]
    assert '${AI_CONSUMER_KEY_DEVELOPER:?' in run


def test_smoke_checks_401_then_uses_the_first_key():
    run = tasks_of(v2())["smoke"]["run"]
    assert '"401"' in run
    assert 'apikey: $AI_CONSUMER_KEY_DEVELOPER' in run


def test_readme_lists_consumers_and_how_to_see_them_in_konnect():
    readme = render_all(v2())["README.md"]
    assert "## AI Consumer と API キー" in readme
    assert "researcher-short" in readme
    assert "300 秒" in readme
    assert "Explorer" in readme
    assert "AI gateway consumer" in readme
