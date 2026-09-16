from pathlib import Path

import pytest

from generator.context import build_context
from generator.render import render_all
from generator.schema import load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.yaml"))


# gateway x control_plane の全組み合わせ。ここが欠けると、その環境は golden で一度も生成されない
MINIMALS = {
    "aigw-v2-konnect-minimal",
    "aigw-v1-konnect-minimal",
    "aigw-v1-self-managed-minimal",
    "apigw-konnect-minimal",
    "apigw-self-managed-minimal",
}


def _gitignore_patterns(golden_dir: Path) -> list[str]:
    return (golden_dir / ".gitignore").read_text(encoding="utf-8").split()


def _is_ignored(relpath: str, patterns: list[str]) -> bool:
    """生成物の .gitignore が外すパスか。ここに入るファイルは clone 直後には存在しない。"""
    for pattern in patterns:
        if pattern.endswith("/"):
            if f"/{relpath}".find(f"/{pattern}") >= 0:
                return True
        elif relpath == pattern or relpath.endswith(f"/{pattern}"):
            return True
    return False


def _comparable(files: dict[str, str], patterns: list[str]) -> dict[str, str]:
    return {k: v for k, v in files.items() if not _is_ignored(k, patterns)}


def _golden_files(golden_dir: Path) -> dict[str, str]:
    return {
        str(p.relative_to(golden_dir)): p.read_text(encoding="utf-8")
        for p in golden_dir.rglob("*")
        if p.is_file()
    }


def test_every_environment_has_a_minimal():
    assert MINIMALS <= {e.stem for e in EXAMPLES}


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.stem)
def test_output_matches_golden(example):
    golden_dir = ROOT / "tests" / "golden" / example.stem
    assert golden_dir.exists(), (
        f"{golden_dir} がありません。uv run python scripts/update_golden.py で作成してください。"
    )

    patterns = _gitignore_patterns(golden_dir)
    produced = _comparable(render_all(build_context(load_spec(example))), patterns)
    expected = _comparable(_golden_files(golden_dir), patterns)

    assert set(produced) == set(expected)
    for relpath in sorted(produced):
        assert produced[relpath] == expected[relpath], (
            f"{example.stem}/{relpath} が変わりました。"
            "意図した変更なら uv run python scripts/update_golden.py で更新してください。"
        )


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.stem)
def test_golden_keeps_why_comments(example):
    golden_dir = ROOT / "tests" / "golden" / example.stem
    compose = (golden_dir / "compose.yaml").read_text(encoding="utf-8")
    # docker compose config は正規化するためコメントとアンカーの消失を検知できない
    assert "x-default: &default" in compose


def test_generated_secrets_stay_out_of_the_comparison():
    """golden の .env は生成物で git 管理外。新しいクローンには存在しない。"""
    patterns = _gitignore_patterns(ROOT / "tests" / "golden" / "apigw-konnect-minimal")
    assert _is_ignored(".env", patterns)
    assert _is_ignored("config/kong/certs/tls.crt", patterns)
    assert not _is_ignored(".env.example", patterns)
    assert not _is_ignored("compose.yaml", patterns)


def test_comparison_passes_on_a_fresh_clone(tmp_path):
    """git 管理外のファイルを落とした golden（＝clone 直後の状態）でも比較が通る。"""
    example = ROOT / "examples" / "apigw-konnect-minimal.yaml"
    golden_dir = ROOT / "tests" / "golden" / example.stem
    fresh = tmp_path / example.stem
    patterns = _gitignore_patterns(golden_dir)
    for path in golden_dir.rglob("*"):
        relpath = str(path.relative_to(golden_dir))
        if not path.is_file() or _is_ignored(relpath, patterns):
            continue
        (fresh / relpath).parent.mkdir(parents=True, exist_ok=True)
        (fresh / relpath).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    assert not (fresh / ".env").exists()
    assert _comparable(render_all(build_context(load_spec(example))), patterns) == _golden_files(fresh)
