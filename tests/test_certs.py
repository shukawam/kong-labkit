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
