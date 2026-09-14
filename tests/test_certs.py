import shutil
import subprocess

import pytest

from generator.certs import generate_cluster_cert

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl がない")


def test_generates_crt_and_key(tmp_path):
    crt, key = generate_cluster_cert(tmp_path / ".certs", common_name="acme-gateway")
    assert crt.exists() and key.exists()
    assert crt.name == "cluster.crt"
    assert key.name == "cluster.key"


def test_certificate_has_expected_common_name(tmp_path):
    crt, _ = generate_cluster_cert(tmp_path / ".certs", common_name="acme-gateway")
    out = subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-noout", "-subject"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "acme-gateway" in out


def test_certificate_has_subject_alternative_name(tmp_path):
    crt, _ = generate_cluster_cert(tmp_path / ".certs", common_name="acme-gateway")
    out = subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-noout", "-ext", "subjectAltName"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "acme-gateway" in out


def test_key_is_not_password_protected(tmp_path):
    # Kong はパスフレーズ付きの鍵を読めないため -nodes が効いていることを確認する
    _, key = generate_cluster_cert(tmp_path / ".certs", common_name="acme-gateway")
    subprocess.run(
        ["openssl", "rsa", "-in", str(key), "-noout", "-check"],
        capture_output=True,
        check=True,
    )


def test_creates_parent_directory(tmp_path):
    target = tmp_path / "nested" / ".certs"
    crt, _ = generate_cluster_cert(target, common_name="acme-gateway")
    assert crt.parent == target


def test_basename_controls_filenames(tmp_path):
    crt, key = generate_cluster_cert(
        tmp_path / "certs", common_name="acme-cluster", basename="tls"
    )
    assert crt.name == "tls.crt"
    assert key.name == "tls.key"


def _mise_certs_script(**overrides) -> str:
    import tomllib

    from generator.render import render_all
    from tests.conftest import ctx_for

    doc = tomllib.loads(render_all(ctx_for(**overrides))["mise.toml"])
    return doc["tasks"]["certs"]["run"]


def _subject_and_san(crt) -> tuple[str, str]:
    def field(*args: str) -> str:
        return subprocess.run(
            ["openssl", "x509", "-in", str(crt), "-noout", *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    return field("-subject"), field("-ext", "subjectAltName")


@pytest.mark.parametrize(
    "overrides, basename",
    [
        ({"gateway": "ai-gateway-v2"}, "cluster"),
        ({"control_plane": "self-managed"}, "tls"),
    ],
    ids=["konnect", "self-managed"],
)
def test_mise_certs_task_produces_the_same_certificate_as_the_module(
    tmp_path, overrides, basename
):
    # mise run certs は生成される README の初手 1 行目。certs.py だけ直すと手順書どおりの操作で
    # 潰したはずのバグ（SAN 無し・CN 違い）が再現する
    from generator.context import build_context
    from generator.schema import EnvSpec

    ctx = build_context(
        EnvSpec.model_validate({"customer": "acme", "gateway": "api-gateway", **overrides})
    )

    by_task = tmp_path / "task"
    by_task.mkdir()
    subprocess.run(
        ["bash", "-e", "-c", _mise_certs_script(**overrides)],
        cwd=by_task,
        capture_output=True,
        check=True,
    )
    task_crt = by_task / ctx.certs.crt_path
    assert task_crt.exists()
    assert (by_task / ctx.certs.key_path).exists()
    assert task_crt.name == f"{basename}.crt"

    module_crt, _ = generate_cluster_cert(
        tmp_path / "module", common_name=ctx.certs.common_name, basename=ctx.certs.basename
    )

    assert _subject_and_san(task_crt) == _subject_and_san(module_crt)
    assert f"CN={ctx.certs.common_name}" in _subject_and_san(task_crt)[0]
    assert f"DNS:{ctx.certs.common_name}" in _subject_and_san(task_crt)[1]
