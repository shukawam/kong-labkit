import subprocess

import pytest
from click.testing import CliRunner

from generator.gen import (
    DirtyWorktreeError,
    TargetNotEmptyError,
    check_git_clean,
    main,
    write_files,
)

FILES = {"compose.yaml": "a\n", ".env": 'K="v"\n', "config/kong/kong.yaml": "b\n"}


def test_writes_into_empty_directory(tmp_path):
    result = write_files(tmp_path, FILES, force=False)
    assert (tmp_path / "compose.yaml").read_text() == "a\n"
    assert (tmp_path / "config/kong/kong.yaml").read_text() == "b\n"
    assert sorted(p.name for p in result.created) == [".env", "compose.yaml", "kong.yaml"]
    assert result.overwritten == []
    assert result.skipped == []


def test_refuses_non_empty_directory_without_force(tmp_path):
    (tmp_path / "compose.yaml").write_text("existing\n")
    with pytest.raises(TargetNotEmptyError) as e:
        write_files(tmp_path, FILES, force=False)
    assert "--force" in str(e.value)
    assert (tmp_path / "compose.yaml").read_text() == "existing\n"


def test_force_overwrites_but_protects_env(tmp_path):
    (tmp_path / "compose.yaml").write_text("existing\n")
    (tmp_path / ".env").write_text('K="secret-i-typed"\n')
    result = write_files(tmp_path, FILES, force=True)
    assert (tmp_path / "compose.yaml").read_text() == "a\n"
    assert (tmp_path / ".env").read_text() == 'K="secret-i-typed"\n'
    assert [p.name for p in result.skipped] == [".env"]
    assert "compose.yaml" in [p.name for p in result.overwritten]


def test_force_protects_certs_and_docs(tmp_path):
    (tmp_path / "compose.yaml").write_text("existing\n")
    (tmp_path / ".certs").mkdir()
    (tmp_path / ".certs/cluster.crt").write_text("registered-with-konnect\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/answer.md").write_text("顧客への回答\n")

    write_files(tmp_path, {**FILES, "docs/.gitkeep": ""}, force=True)

    assert (tmp_path / ".certs/cluster.crt").read_text() == "registered-with-konnect\n"
    assert (tmp_path / "docs/answer.md").read_text() == "顧客への回答\n"


def test_force_creates_missing_env_in_a_non_empty_directory(tmp_path):
    # .env は保護対象だが、まだ無いなら作る。保護はあくまで既存物を守るためのもの
    (tmp_path / "compose.yaml").write_text("existing\n")
    result = write_files(tmp_path, FILES, force=True)
    assert (tmp_path / ".env").read_text() == 'K="v"\n'
    assert [p.name for p in result.skipped] == []


def test_git_clean_passes_on_clean_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    check_git_clean(tmp_path)  # 例外が出なければ通過


def test_git_clean_raises_on_dirty_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("a\n")
    with pytest.raises(DirtyWorktreeError) as e:
        check_git_clean(tmp_path)
    assert "commit か stash" in str(e.value)


def test_git_clean_passes_when_not_a_repo(tmp_path):
    check_git_clean(tmp_path)


def test_cli_generates_full_environment(tmp_path):
    env_yaml = tmp_path / "acme.yaml"
    env_yaml.write_text(
        "customer: acme\ngateway: api-gateway\nidp:\n  type: keycloak\n  realm: acme\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = CliRunner().invoke(main, [str(env_yaml), "-o", str(out)])
    assert result.exit_code == 0, result.output
    for name in ("compose.yaml", "mise.toml", "README.md", ".env", "env.yaml"):
        assert (out / name).exists(), name
    assert (out / "config/keycloak/realm-export.json").exists()
    assert (out / "docs/.gitkeep").exists()


def test_cli_copies_input_yaml_verbatim(tmp_path):
    env_yaml = tmp_path / "acme.yaml"
    body = "customer: acme\ngateway: api-gateway\n"
    env_yaml.write_text(body, encoding="utf-8")
    out = tmp_path / "out"
    CliRunner().invoke(main, [str(env_yaml), "-o", str(out)])
    assert (out / "env.yaml").read_text(encoding="utf-8") == body


def test_cli_generates_konnect_certs(tmp_path):
    env_yaml = tmp_path / "acme.yaml"
    env_yaml.write_text("customer: acme\ngateway: ai-gateway-v2\n", encoding="utf-8")
    out = tmp_path / "out"
    CliRunner().invoke(main, [str(env_yaml), "-o", str(out)])
    assert (out / ".certs/cluster.crt").exists()
    assert (out / ".certs/cluster.key").exists()


def test_cli_generates_shared_certs_for_self_managed(tmp_path):
    env_yaml = tmp_path / "acme.yaml"
    env_yaml.write_text(
        "customer: acme\ngateway: api-gateway\ncontrol_plane: self-managed\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    CliRunner().invoke(main, [str(env_yaml), "-o", str(out)])
    crt = out / "config/kong/certs/tls.crt"
    assert crt.exists()
    assert not (out / ".certs").exists()

    subject = subprocess.run(
        ["openssl", "x509", "-in", str(crt), "-noout", "-subject"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # shared mTLS は CN をこの固定リテラルとしか照合しないため、CN が一致しないと CP-DP 間の接続が確立しない
    assert "CN=kong_clustering" in subject


def test_cli_reports_validation_error_without_traceback(tmp_path):
    env_yaml = tmp_path / "bad.yaml"
    env_yaml.write_text(
        "customer: acme\ngateway: ai-gateway-v2\ncontrol_plane: self-managed\n", encoding="utf-8"
    )
    result = CliRunner().invoke(main, [str(env_yaml), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "ai-gateway-v2 は Konnect 専用です" in result.output
    assert "Traceback" not in result.output


def test_cli_prints_warnings(tmp_path):
    env_yaml = tmp_path / "entra.yaml"
    env_yaml.write_text(
        "customer: acme\ngateway: api-gateway\nidp:\n  type: entra-id\n", encoding="utf-8"
    )
    result = CliRunner().invoke(main, [str(env_yaml), "-o", str(tmp_path / "out")])
    assert result.exit_code == 0
    assert "app registration" in result.output


def test_cli_lists_paths_not_counts(tmp_path):
    env_yaml = tmp_path / "acme.yaml"
    env_yaml.write_text("customer: acme\ngateway: api-gateway\n", encoding="utf-8")
    result = CliRunner().invoke(main, [str(env_yaml), "-o", str(tmp_path / "out")])
    assert "compose.yaml" in result.output
    assert "mise.toml" in result.output


def test_cli_force_into_missing_directory_does_not_traceback(tmp_path):
    # out がまだ無い状態で --force を付けても check_git_clean が FileNotFoundError で落ちてはいけない
    env_yaml = tmp_path / "acme.yaml"
    env_yaml.write_text("customer: acme\ngateway: api-gateway\n", encoding="utf-8")
    out = tmp_path / "does-not-exist-yet"
    result = CliRunner().invoke(main, [str(env_yaml), "-o", str(out), "--force"])
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert (out / "compose.yaml").exists()


def test_cli_refuses_non_empty_directory_without_force(tmp_path):
    env_yaml = tmp_path / "acme.yaml"
    env_yaml.write_text("customer: acme\ngateway: api-gateway\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / "compose.yaml").write_text("existing\n")
    result = CliRunner().invoke(main, [str(env_yaml), "-o", str(out)])
    assert result.exit_code == 1
    assert "--force" in result.output
    assert "Traceback" not in result.output


def test_cli_refuses_dirty_git_worktree_with_force(tmp_path):
    env_yaml = tmp_path / "acme.yaml"
    env_yaml.write_text("customer: acme\ngateway: api-gateway\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=out, check=True)
    (out / "compose.yaml").write_text("existing\n")  # コミットしない未追跡ファイル = dirty
    result = CliRunner().invoke(main, [str(env_yaml), "-o", str(out), "--force"])
    assert result.exit_code == 1
    assert "commit か stash" in result.output
    assert "Traceback" not in result.output
