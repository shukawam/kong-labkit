import json
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
customer: smoke
gateway: api-gateway
control_plane: self-managed
idp:
  type: keycloak
  realm: smoke
upstream: httpbin
"""


def _fetch_access_token(cwd: Path) -> str:
    # spec: idp 有効時は upstream を openid-connect で保護する仕様なので、匿名アクセスは 401 になるのが正しい。
    # httpbin への到達確認には direct access grants で取ったトークンを使う。
    result = subprocess.run(
        [
            "curl",
            "-sS",
            "-f",
            "-X",
            "POST",
            "http://localhost:8080/realms/smoke/protocol/openid-connect/token",
            "-d",
            "grant_type=password",
            "-d",
            "client_id=smoke-public",
            "-d",
            "username=tester",
            "-d",
            "password=tester",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["access_token"]


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


@pytest.fixture(scope="module")
def running_env(tmp_path_factory):
    work = tmp_path_factory.mktemp("smoke")
    env_yaml = work / "smoke.yaml"
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

    subprocess.run(["docker", "compose", "up", "-d"], cwd=out, check=True)
    try:
        _wait_for(
            ["curl", "-sS", "-f", "http://localhost:8100/status"], cwd=out
        )
        yield out
    finally:
        subprocess.run(["docker", "compose", "down", "-v"], cwd=out, check=False)


def test_data_plane_reports_healthy(running_env):
    result = subprocess.run(
        ["curl", "-sS", "-f", "http://localhost:8100/status"],
        cwd=running_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_deck_sync_applies_declarative_config(running_env):
    result = subprocess.run(
        [
            "deck",
            "gateway",
            "sync",
            "config/kong/kong.yaml",
            "--kong-addr",
            "http://localhost:8001",
        ],
        cwd=running_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_proxy_rejects_anonymous_request(running_env):
    # deck sync が先に走っている必要があるため、同一モジュール内の実行順に依存する。
    # sync 直後は DP への配布が伝播しきっておらず 404（ルート未配布）が一時的に返るため、401 になるまで待つ。
    deadline = time.time() + 60
    code = None
    while time.time() < deadline:
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "http://localhost:8000/httpbin/status/200",
            ],
            cwd=running_env,
            capture_output=True,
            text=True,
        )
        code = result.stdout
        if code == "401":
            return
        time.sleep(2)
    raise AssertionError(f"匿名アクセスが 401 になりませんでした（最後の応答コード: {code}）")


def test_proxy_returns_200_through_httpbin_with_valid_token(running_env):
    token = _fetch_access_token(running_env)
    _wait_for(
        [
            "curl",
            "-sS",
            "-f",
            "-o",
            "/dev/null",
            "-H",
            f"Authorization: Bearer {token}",
            "http://localhost:8000/httpbin/status/200",
        ],
        cwd=running_env,
        timeout=60,
    )


def test_keycloak_issues_a_token(running_env):
    result = subprocess.run(
        [
            "curl",
            "-sS",
            "-f",
            "-X",
            "POST",
            "http://localhost:8080/realms/smoke/protocol/openid-connect/token",
            "-d",
            "grant_type=password",
            "-d",
            "client_id=smoke-public",
            "-d",
            "username=tester",
            "-d",
            "password=tester",
        ],
        cwd=running_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "access_token" in result.stdout
