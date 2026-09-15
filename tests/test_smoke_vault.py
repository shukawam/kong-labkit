import base64
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from generator.gen import main

pytestmark = [
    pytest.mark.slow,
    pytest.mark.docker,
    pytest.mark.skipif(shutil.which("docker") is None, reason="docker がない"),
    pytest.mark.skipif(
        not os.environ.get("KONG_LICENSE_DATA"),
        reason="KONG_LICENSE_DATA が未設定（Kong Enterprise のライセンスが必要）",
    ),
]

ENV_YAML = """\
customer: vaultsmoke
gateway: api-gateway
control_plane: self-managed
idp:
  type: ldap
upstream: httpbin
secrets:
  backend: vault
"""

OVERRIDE = """\
services:
  kong-cp:
    ports: !override
      - 28001:8001
  kong-dp:
    ports: !override
      - 28000:8000
      - 28100:8100
  httpbin:
    ports: !override
      - 28081:80
  vault:
    ports: !override
      - 28200:8200
  lldap:
    # container_name も既定は "lldap" 固定で他プロジェクトの同名コンテナと衝突するため、
    # ポートと合わせて付け替える（アップストリームの実サービス名 "lldap" には依存しない）
    container_name: vaultsmoke-lldap
    ports: !override
      - 23890:3890
      - 27170:17170
  otel-lgtm:
    # 既定でも observability backend が otel-lgtm になり、container_name/ports が固定名で
    # 生成されるため同様に衝突する。ここでも作業環境固有の値へ付け替える
    container_name: vaultsmoke-otel-lgtm
    ports: !override
      - 23000:3000
      - 24317:4317
      - 24318:4318
"""


def _wait_for(cmd: list[str], cwd: Path, timeout: int = 240) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if result.returncode == 0:
            return
        last = result.stderr or result.stdout
        time.sleep(5)
    raise AssertionError(f"{' '.join(cmd)} が {timeout} 秒以内に成功しませんでした: {last}")


def _status(cwd: Path, credentials: str) -> str:
    token = base64.b64encode(credentials.encode()).decode()
    result = subprocess.run(
        [
            "curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
            "-H", f"Authorization: ldap {token}",
            "http://localhost:28000/httpbin/status/200",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture(scope="module")
def running_env(tmp_path_factory):
    work = tmp_path_factory.mktemp("vaultsmoke")
    env_yaml = work / "vaultsmoke.yaml"
    env_yaml.write_text(ENV_YAML, encoding="utf-8")
    out = work / "env"

    result = CliRunner().invoke(main, [str(env_yaml), "-o", str(out)])
    assert result.exit_code == 0, result.output

    dotenv = out / ".env"
    dotenv.write_text(
        dotenv.read_text(encoding="utf-8").replace(
            'KONG_LICENSE_DATA=""', f'KONG_LICENSE_DATA={os.environ["KONG_LICENSE_DATA"]!r}'
        ),
        encoding="utf-8",
    )
    (out / "compose.override.yaml").write_text(OVERRIDE, encoding="utf-8")

    try:
        # up 自体を try に含める: 他プロジェクトのコンテナと衝突して起動が途中で
        # 失敗しても、finally の down -v で孤児コンテナを必ず片付けるため
        subprocess.run(["docker", "compose", "up", "-d"], cwd=out, check=True)
        _wait_for(["curl", "-sS", "-f", "http://localhost:28100/status"], cwd=out)
        subprocess.run(
            ["deck", "gateway", "sync", "config/kong/kong.yaml", "--kong-addr", "http://localhost:28001"],
            cwd=out,
            check=True,
        )
        yield out
    finally:
        subprocess.run(["docker", "compose", "down", "-v"], cwd=out, check=False)


def test_kong_yaml_carries_no_plaintext_secret(running_env):
    body = (running_env / "config" / "kong" / "kong.yaml").read_text(encoding="utf-8")
    assert "{vault://hcv/kong/ldap/ldap_password}" in body
    assert "local-dev-password" not in body


def test_bootstrap_completed_successfully(running_env):
    result = subprocess.run(
        ["docker", "compose", "ps", "-a", "--format", "{{.Name}} {{.ExitCode}}"],
        cwd=running_env,
        capture_output=True,
        text=True,
    )
    assert "vault-bootstrap 0" in result.stdout, result.stdout


def test_ldap_auth_succeeds_through_the_vault_reference(running_env):
    """Kong は Vault から取った bind パスワードでしか LDAP に bind できない。"""
    deadline = time.time() + 120
    code = None
    while time.time() < deadline:
        code = _status(running_env, "tester:tester-password")
        if code == "200":
            return
        time.sleep(3)
    raise AssertionError(f"Vault 経由の LDAP 認証が 200 になりませんでした（最後の応答コード: {code}）")


def test_wrong_credentials_are_rejected(running_env):
    # 200 が「参照が解決できず素通ししている」偽陽性でないことを確かめる
    assert _status(running_env, "tester:wrong-password") == "401"
