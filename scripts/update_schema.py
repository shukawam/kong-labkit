"""schemas/env.schema.json を generator/schema.py から作り直す。エディタ補完のための成果物で、定義元は Pydantic 側。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generator.schema import EnvSpec  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "env.schema.json"


def build() -> str:
    schema = EnvSpec.model_json_schema()
    # Pydantic は方言を書かないが、宣言が無いとバリデータによって解釈が draft-07 に落ちる
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "kong-labkit env spec"
    # 組み合わせルール（ai-gateway-v2 x self-managed 等）は model_validator 側にしかない
    schema["description"] = (
        "キーと値の候補だけを表す。組み合わせの検証は generator/schema.py の "
        "model_validator が担当するので、ここを通っても生成が失敗することはある。"
    )
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    SCHEMA.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA.write_text(build(), encoding="utf-8")
    print(f"updated: {SCHEMA.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
