from pathlib import Path

import pytest

from generator.context import build_context
from generator.render import render_all
from generator.schema import load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.yaml"))


def test_examples_exist():
    assert len(EXAMPLES) == 3, [e.name for e in EXAMPLES]


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.stem)
def test_output_matches_golden(example):
    golden_dir = ROOT / "tests" / "golden" / example.stem
    assert golden_dir.exists(), (
        f"{golden_dir} がありません。uv run python scripts/update_golden.py で作成してください。"
    )

    produced = render_all(build_context(load_spec(example)))
    expected = {
        str(p.relative_to(golden_dir)): p.read_text(encoding="utf-8")
        for p in golden_dir.rglob("*")
        if p.is_file()
    }

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
