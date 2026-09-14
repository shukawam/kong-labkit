import itertools
import shutil
import subprocess

import pytest
import yaml
from pydantic import ValidationError

from generator.context import build_context
from generator.render import render_all
from generator.schema import EnvSpec
from tests.conftest import load_kongctl

AZURE_PROVIDER = {
    "type": "azure",
    "instance": "acme-foundry",
    "auth": "api-key",
    "models": [
        {"name": "gpt-5-6", "deployment_id": "gpt-5.6", "api_version": "2024-12-01-preview"}
    ],
}

GATEWAYS = ["ai-gateway-v2", "ai-gateway-v1", "api-gateway"]
CONTROL_PLANES = ["konnect", "self-managed"]
IDPS = [
    {"type": "none"},
    {"type": "keycloak", "realm": "acme"},
    {"type": "entra-id"},
    {"type": "ldap"},
]
CACHES = ["none", "redis", "redis-stack"]
VECTORDBS = ["none", "redis-stack", "pgvector"]


def _candidates():
    for gateway, cp, idp, cache, vectordb in itertools.product(
        GATEWAYS, CONTROL_PLANES, IDPS, CACHES, VECTORDBS
    ):
        data = {
            "customer": "acme",
            "gateway": gateway,
            "control_plane": cp,
            "idp": idp,
            "cache": {"type": cache},
            "vectordb": {"type": vectordb},
        }
        if gateway.startswith("ai-gateway"):
            data["ai"] = {
                "providers": [AZURE_PROVIDER],
                "semantic_cache": vectordb != "none",
            }
        yield data


def _valid_specs():
    specs = []
    for data in _candidates():
        try:
            specs.append(EnvSpec.model_validate(data))
        except ValidationError:
            continue  # スキーマが弾く組み合わせは対象外
    return specs


VALID_SPECS = _valid_specs()


def _spec_id(spec: EnvSpec) -> str:
    return "-".join(
        [
            spec.gateway.value,
            spec.control_plane.value,
            spec.idp.type.value,
            spec.cache.type.value,
            spec.vectordb.type.value,
        ]
    )


def test_matrix_is_not_trivially_small():
    # 組み合わせの生成条件を壊したときに気づくための番人
    assert len(VALID_SPECS) > 40, len(VALID_SPECS)


@pytest.mark.parametrize("spec", VALID_SPECS, ids=_spec_id)
def test_every_generated_yaml_parses(spec):
    files = render_all(build_context(spec))
    assert "config/kongctl.yaml" in files
    for relpath, body in files.items():
        if relpath.endswith((".yaml", ".yml")):
            if relpath == "config/kongctl.yaml":
                assert load_kongctl(body) is not None
                continue
            assert yaml.safe_load(body) is not None, relpath


@pytest.mark.docker
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker がない")
@pytest.mark.parametrize("spec", VALID_SPECS, ids=_spec_id)
def test_docker_compose_config_accepts_every_combination(spec, tmp_path):
    from generator.gen import write_files

    out = tmp_path / _spec_id(spec)
    out.mkdir(parents=True)
    write_files(out, render_all(build_context(spec)), force=False)

    result = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=out,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("spec", VALID_SPECS, ids=_spec_id)
def test_every_compose_service_name_matches_wiring(spec):
    doc = yaml.safe_load(render_all(build_context(spec))["compose.yaml"])
    services = set(doc["services"])
    ctx = build_context(spec)
    if ctx.cache.enabled:
        assert ctx.cache.host in services
    if ctx.vector.enabled and ctx.vector.type.value == "pgvector":
        assert ctx.vector.host in services
    if ctx.idp.service_name:
        assert ctx.idp.service_name in services
    if ctx.upstream.enabled:
        assert ctx.upstream.service_name in services


@pytest.mark.parametrize("spec", VALID_SPECS, ids=_spec_id)
def test_every_mise_toml_parses(spec):
    import tomllib

    doc = tomllib.loads(render_all(build_context(spec))["mise.toml"])
    assert {name.removeprefix("tasks.") for name in doc["tasks"]} == {
        "up", "down", "reset", "certs", "setup", "sync", "diff", "logs", "smoke"
    }


@pytest.mark.parametrize("spec", VALID_SPECS, ids=_spec_id)
def test_every_deck_env_reference_resolves(spec):
    # decK は DECK_ 付きの環境変数しか展開しない。参照先が .env に無ければ sync 時に空文字になる
    ctx = build_context(spec)
    files = render_all(ctx)
    body = files.get("config/kong/kong.yaml", "")
    for alias, source in ctx.deck.env_aliases.items():
        assert source in ctx.env_vars, source
        assert f'{alias}="${source}"' in ctx.deck.env_prefix, alias
    for line in body.splitlines():
        if '${{ env "' not in line:
            continue
        alias = line.split('${{ env "')[1].split('"')[0]
        assert alias in ctx.deck.env_aliases, alias
    assert "${AZURE" not in body
    assert "${KEYCLOAK" not in body
    assert "LLM_API_KEY" not in body
