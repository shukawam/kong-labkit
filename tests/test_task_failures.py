import subprocess
import sys
import tomllib

import pytest

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for


def task_of(ctx, name):
    return tomllib.loads(render_all(ctx)["mise.toml"])["tasks"][name]["run"]


def stub(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize("control_plane", ["konnect", "self-managed"])
@pytest.mark.parametrize("task", ["setup", "sync", "diff"])
@pytest.mark.parametrize("credential", [None, ""])
def test_missing_api_key_stops_before_any_external_command(
    tmp_path, control_plane, task, credential
):
    ctx = ctx_for(
        gateway="ai-gateway-v1", control_plane=control_plane,
        ai={"providers": [AZURE_PROVIDER]},
    )
    calls = tmp_path / "calls"
    for name in ("kongctl", "deck", "docker", "mise"):
        stub(tmp_path, name, f"from pathlib import Path\nPath({str(calls)!r}).touch()\n")
    env = {"PATH": str(tmp_path), "KONNECT_PAT": "test-pat"}
    if credential is not None:
        env["AZURE_OPENAI_API_KEY"] = credential
    result = subprocess.run(
        ["/bin/bash", "-c", task_of(ctx, task)], cwd=tmp_path,
        env=env, text=True, capture_output=True,
    )
    assert result.returncode != 0
    assert "AZURE_OPENAI_API_KEY" in result.stderr
    assert not calls.exists(), "A remote or Docker command ran before the credential check"


@pytest.mark.parametrize("control_plane", ["konnect", "self-managed"])
@pytest.mark.parametrize("task", ["sync", "diff"])
def test_populated_api_key_is_forwarded_to_deck(tmp_path, control_plane, task):
    ctx = ctx_for(
        gateway="ai-gateway-v1", control_plane=control_plane,
        ai={"providers": [AZURE_PROVIDER]},
    )
    output = tmp_path / "forwarded-key"
    for name in ("kongctl", "deck"):
        stub(tmp_path, name,
             "import os\nfrom pathlib import Path\n"
             f"Path({str(output)!r}).write_text(os.environ['DECK_AZURE_OPENAI_API_KEY'])\n")
    # Shell metacharacters inside a credential remain literal data.
    credential = "test-key-'$value;$(false)"
    result = subprocess.run(
        ["/bin/bash", "-c", task_of(ctx, task)], cwd=tmp_path,
        env={"PATH": str(tmp_path), "KONNECT_PAT": "test-pat",
             "AZURE_OPENAI_API_KEY": credential},
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text() == credential


@pytest.mark.parametrize("status", ["200", "201", "301", "401", "403", "404", "429", "500", "502", "000"])
def test_ldap_chat_smoke_requires_authenticated_200(tmp_path, status):
    ctx = ctx_for(
        gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]},
        idp={"type": "ldap", "users": [{"name": "developer", "password": "password"}]},
    )
    stub(tmp_path, "curl", """
import os
import sys
args = sys.argv[1:]
auth = [arg for arg in args if arg.startswith('Authorization:')]
if auth:
    assert auth == ['Authorization: ldap ZGV2ZWxvcGVyOnBhc3N3b3Jk'], auth
    print(os.environ['TEST_UPSTREAM_STATUS'], end='')
    sys.exit(7 if os.environ['TEST_UPSTREAM_STATUS'] == '000' else 0)
print('401', end='')
""")
    result = subprocess.run(
        ["/bin/bash", "-c", task_of(ctx, "smoke")], cwd=tmp_path,
        env={"PATH": str(tmp_path), "TEST_UPSTREAM_STATUS": status},
        text=True, capture_output=True,
    )
    assert (result.returncode == 0) == (status == "200"), result.stderr
    assert ("smoke: OK" in result.stdout) == (status == "200")
    if status not in ("200", "000"):
        assert f"HTTP {status}" in result.stderr
