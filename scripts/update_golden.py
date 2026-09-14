"""tests/golden を examples から作り直す。テンプレート変更が意図どおりか diff で確認するために使う。"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generator.context import build_context  # noqa: E402
from generator.render import render_all  # noqa: E402
from generator.schema import load_spec  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"


def main() -> None:
    for example in sorted((ROOT / "examples").glob("*.yaml")):
        target = GOLDEN / example.stem
        if target.exists():
            shutil.rmtree(target)
        for relpath, body in render_all(build_context(load_spec(example))).items():
            path = target / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        print(f"updated: {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
