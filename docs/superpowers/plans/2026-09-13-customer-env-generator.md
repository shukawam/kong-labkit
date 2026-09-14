# 顧客デリバリー用 再現環境ジェネレータ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `env.yaml` で構成を宣言すると、そのまま `mise run up` が通る Docker Compose ベースの Kong 検証環境一式を生成する CLI を作る。

**Architecture:** pydantic で入力 YAML を検証し（不正な組み合わせは生成前に停止）、`context.py` でコンポーネント間の配線値とポート割り当てを 1 つのコンテキストに解決し、Jinja2 のパーシャルを合成して compose とコンフィグを出力する。テンプレートは YAML の形を持つだけで、値を決める判断は全て Python 側にある。

**Tech Stack:** Python 3.12+ / uv（PEP 723 single-file script）/ pydantic v2 / Jinja2 / PyYAML / click / pytest / Docker Compose / mise / kongctl / decK

**Spec:** `docs/superpowers/specs/2026-09-13-customer-env-generator-design.md`

## Global Constraints

- 作業ディレクトリは `~/customer/kong-labkit`。spec と本計画をコミットした時点で git リポジトリとして初期化済み。`--force` の保護設計が git のクリーン判定に依存しているため、この前提は崩さないこと。
- v1 のスコープは `target: compose` のみ。`kubernetes` / `cloud-run` / `aca` はスキーマに存在させた上で unsupported エラーを返す。
- Python は 3.12 以上（`StrEnum` を使う）。
- `gen.py` は PEP 723 の inline script metadata を持ち、`uv run generator/gen.py` で依存解決から実行まで完結する。
- Jinja2 の Environment は必ず `undefined=StrictUndefined`、`trim_blocks=True`、`lstrip_blocks=True`、`keep_trailing_newline=True` で構築する。未定義変数が空文字になると、`KONG_CLUSTER_CONTROL_PLANE: .us.cp.konghq.com:443` のような一見正しい形の壊れた設定が黙って生成される。
- サービスパーシャルはインデント 0 で書き、骨格側で `{% filter indent(width=2, first=True) %}` で吸収する。`first=True` を省くと 1 行目だけインデントされず compose が壊れる。
- 常に保護するパス（`--force` でも上書きしない）は `.env` / `.certs/` / `docs/` の 3 つ。
- 固定イメージタグ: `kong/kong-ai-gateway:2.0.1` / `kong/kong-gateway:3.14` / `redis:8.0.2` / `redis/redis-stack:7.4.0-v3` / `pgvector/pgvector:pg17` / `postgres:17` / `quay.io/keycloak/keycloak:26.6.1` / `grafana/otel-lgtm:0.25.0` / `kennethreitz/httpbin`
- エラーメッセージは日本語で、「何がダメか」ではなく「どう直すか」を書く。文面はテストで固定する。
- コメントは why が非自明な箇所にのみ書く。項目の説明や公式ドキュメントの引用は書かない。
- Markdown を書くときは 1 段落 1 行（hard wrap しない）。

---

## File Structure

```
~/customer/kong-labkit/
├── .gitignore
├── README.md                        # ジェネレータ自身の使い方とオプション一覧
├── pyproject.toml                   # pytest 設定と dev 依存のみ
├── generator/
│   ├── gen.py                       # CLI。引数処理、ファイル書き出し、--force 判定
│   ├── schema.py                    # pydantic モデルと検証ルール 4 件
│   ├── context.py                   # EnvSpec → Ctx。配線解決とポート割り当て
│   ├── certs.py                     # openssl でクラスタ証明書
│   ├── render.py                    # Jinja2 Environment と render_all
│   └── templates/
│       ├── compose.yaml.j2
│       ├── services/
│       │   ├── kong-aigw-v2.yaml.j2
│       │   ├── kong-dp.yaml.j2
│       │   ├── kong-self-managed.yaml.j2
│       │   ├── keycloak.yaml.j2
│       │   ├── redis.yaml.j2
│       │   ├── pgvector.yaml.j2
│       │   ├── otel-lgtm.yaml.j2
│       │   └── httpbin.yaml.j2
│       ├── config/
│       │   ├── kongctl.yaml.j2
│       │   ├── kong.yaml.j2
│       │   └── realm-export.json.j2
│       ├── mise.toml.j2
│       ├── README.md.j2
│       ├── env.j2
│       └── gitignore.j2
├── examples/
│   ├── aigw-v2-konnect-redis-stack.yaml
│   ├── apigw-self-managed-keycloak-pgvector.yaml
│   └── aigw-v1-konnect-minimal.yaml
└── tests/
    ├── conftest.py
    ├── test_schema.py               # L1
    ├── test_ports.py               # L1
    ├── test_context.py             # L1
    ├── test_certs.py               # L1
    ├── test_render_compose.py      # L2 の構造 assert
    ├── test_matrix.py              # L2 の総当たり + docker compose config
    ├── test_golden.py              # L2 のゴールデン 3 構成
    ├── test_writer.py              # --force と保護対象
    ├── test_smoke_apigw.py         # L3 自動スモーク
    └── golden/                     # ゴールデンの期待出力
```

`render.py` は spec のファイル一覧に無い追加モジュールである。spec は `gen.py` を「引数処理、出力、`--force` 判定のみ」と定めているため、Jinja2 の Environment 構築と描画の責務を置く場所が必要になる。この 1 ファイルのみ spec からの逸脱として明示する。

---

## Task 1: プロジェクト基盤と入力スキーマ

**Files:**
- Create: `generator/schema.py`
- Create: `generator/__init__.py`
- Create: `tests/test_schema.py`
- Create: `pyproject.toml`
- Create: `.gitignore`（ジェネレータ側。生成物の `.gitignore` とは別物）

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces: `schema.load_spec(path: Path) -> EnvSpec`、`schema.EnvSpec`、列挙型 `Target` / `Gateway` / `ControlPlane` / `IdpType` / `CacheType` / `VectorDbType` / `UpstreamType` / `ProviderType` / `AuthType`、モデル `Provider` / `Model` / `AiSpec` / `IdpSpec` / `CacheSpec` / `VectorDbSpec` / `ObservabilitySpec`、`EnvSpec.warnings() -> list[str]`

- [ ] **Step 1: git リポジトリを初期化し、骨組みのファイルを置く**

```bash
cd ~/customer/kong-labkit
mkdir -p generator/templates/services generator/templates/config examples tests/golden
touch generator/__init__.py
cat > .gitignore <<'EOF'
__pycache__/
.pytest_cache/
.venv/
*.pyc
EOF
cat > pyproject.toml <<'EOF'
[project]
name = "customer-env-generator"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.9", "jinja2>=3.1", "pyyaml>=6.0", "click>=8.1"]

[dependency-groups]
dev = ["pytest>=8.3"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
markers = [
    "docker: docker コマンドを必要とするテスト",
    "slow: コンテナを起動するテスト",
]
EOF
uv sync
```

- [ ] **Step 2: 検証ルールの失敗テストを書く**

`tests/test_schema.py`:

```python
import pytest
import yaml
from pydantic import ValidationError

from generator.schema import ControlPlane, EnvSpec, Gateway, UpstreamType, load_spec

MINIMAL = {
    "customer": "acme",
    "gateway": "api-gateway",
}


def spec_from(**overrides) -> EnvSpec:
    data = {**MINIMAL, **overrides}
    return EnvSpec.model_validate(data)


def test_minimal_spec_defaults():
    spec = spec_from()
    assert spec.control_plane is ControlPlane.KONNECT
    assert spec.region == "us"
    assert spec.observability.otel_lgtm is True
    # api-gateway は上流が必要なので httpbin が既定で入る
    assert spec.upstream is UpstreamType.HTTPBIN


def test_ai_gateway_defaults_to_no_upstream():
    spec = spec_from(gateway="ai-gateway-v2")
    assert spec.upstream is UpstreamType.NONE


def test_rule1_aigw_v2_rejects_self_managed():
    with pytest.raises(ValidationError) as e:
        spec_from(gateway="ai-gateway-v2", control_plane="self-managed")
    assert "ai-gateway-v2 は Konnect 専用です" in str(e.value)
    assert "control_plane: konnect にするか" in str(e.value)


def test_rule2_semantic_cache_requires_vectordb():
    with pytest.raises(ValidationError) as e:
        spec_from(
            gateway="ai-gateway-v2",
            ai={"semantic_cache": True},
            vectordb={"type": "none"},
        )
    assert "vectordb.type を redis-stack か pgvector にしてください" in str(e.value)


def test_rule2_semantic_routing_requires_vectordb():
    with pytest.raises(ValidationError) as e:
        spec_from(
            gateway="ai-gateway-v2",
            ai={"semantic_routing": True},
            vectordb={"type": "none"},
        )
    assert "vectordb.type を redis-stack か pgvector にしてください" in str(e.value)


@pytest.mark.parametrize("cache_type", ["redis", "none"])
def test_rule3_redis_stack_vectordb_requires_redis_stack_cache(cache_type):
    with pytest.raises(ValidationError) as e:
        spec_from(
            gateway="ai-gateway-v2",
            cache={"type": cache_type},
            vectordb={"type": "redis-stack"},
        )
    assert "cache.type: redis-stack が必要です" in str(e.value)


def test_rule3_allows_redis_stack_for_both_roles():
    spec = spec_from(
        gateway="ai-gateway-v2",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
    )
    assert spec.warnings() == []


def test_rule4_entra_id_warns_but_succeeds():
    spec = spec_from(idp={"type": "entra-id"})
    warnings = spec.warnings()
    assert len(warnings) == 1
    assert "app registration" in warnings[0]


def test_keycloak_without_realm_is_rejected():
    with pytest.raises(ValidationError) as e:
        spec_from(idp={"type": "keycloak"})
    assert "idp.realm を指定してください" in str(e.value)


@pytest.mark.parametrize("target", ["kubernetes", "cloud-run", "aca"])
def test_unsupported_targets_are_rejected(target):
    with pytest.raises(ValidationError) as e:
        spec_from(target=target)
    assert "現在は compose のみ対応しています" in str(e.value)


def test_load_spec_reads_yaml(tmp_path):
    path = tmp_path / "env.yaml"
    path.write_text(yaml.safe_dump(MINIMAL), encoding="utf-8")
    spec = load_spec(path)
    assert spec.customer == "acme"
    assert spec.gateway is Gateway.API_GATEWAY
```

- [ ] **Step 3: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_schema.py -v`
Expected: 全件 FAIL。`ModuleNotFoundError: No module named 'generator.schema'`

- [ ] **Step 4: schema.py を実装する**

`generator/schema.py`:

```python
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Target(StrEnum):
    COMPOSE = "compose"
    KUBERNETES = "kubernetes"
    CLOUD_RUN = "cloud-run"
    ACA = "aca"


class Gateway(StrEnum):
    AI_GATEWAY_V2 = "ai-gateway-v2"
    AI_GATEWAY_V1 = "ai-gateway-v1"
    API_GATEWAY = "api-gateway"


class ControlPlane(StrEnum):
    KONNECT = "konnect"
    SELF_MANAGED = "self-managed"


class IdpType(StrEnum):
    KEYCLOAK = "keycloak"
    ENTRA_ID = "entra-id"
    NONE = "none"


class CacheType(StrEnum):
    REDIS = "redis"
    REDIS_STACK = "redis-stack"
    NONE = "none"


class VectorDbType(StrEnum):
    REDIS_STACK = "redis-stack"
    PGVECTOR = "pgvector"
    NONE = "none"


class UpstreamType(StrEnum):
    HTTPBIN = "httpbin"
    NONE = "none"


class ProviderType(StrEnum):
    AZURE = "azure"
    BEDROCK = "bedrock"
    VERTEX = "vertex"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class AuthType(StrEnum):
    API_KEY = "api-key"
    MANAGED_IDENTITY = "managed-identity"


class Strict(BaseModel):
    # 未知のキーを黙って捨てると、綴り間違いが「設定したのに効かない」として表れる
    model_config = ConfigDict(extra="forbid")


class Model(Strict):
    name: str
    deployment_id: str | None = None
    api_version: str | None = None


class Provider(Strict):
    type: ProviderType
    instance: str | None = None
    region: str | None = None
    auth: AuthType = AuthType.API_KEY
    models: list[Model] = Field(default_factory=list)


class AiSpec(Strict):
    providers: list[Provider] = Field(default_factory=list)
    semantic_cache: bool = False
    semantic_routing: bool = False
    embedding_model: str = "text-embedding-3-large"


class IdpSpec(Strict):
    type: IdpType = IdpType.NONE
    realm: str | None = None


class CacheSpec(Strict):
    type: CacheType = CacheType.NONE


class VectorDbSpec(Strict):
    type: VectorDbType = VectorDbType.NONE


class ObservabilitySpec(Strict):
    otel_lgtm: bool = True


class EnvSpec(Strict):
    customer: str
    target: Target = Target.COMPOSE
    gateway: Gateway
    control_plane: ControlPlane = ControlPlane.KONNECT
    region: str = "us"
    ai: AiSpec = Field(default_factory=AiSpec)
    idp: IdpSpec = Field(default_factory=IdpSpec)
    cache: CacheSpec = Field(default_factory=CacheSpec)
    vectordb: VectorDbSpec = Field(default_factory=VectorDbSpec)
    observability: ObservabilitySpec = Field(default_factory=ObservabilitySpec)
    upstream: UpstreamType | None = None

    @property
    def is_ai(self) -> bool:
        return self.gateway in (Gateway.AI_GATEWAY_V2, Gateway.AI_GATEWAY_V1)

    @model_validator(mode="after")
    def _resolve_upstream(self):
        if self.upstream is None:
            default = (
                UpstreamType.HTTPBIN
                if self.gateway is Gateway.API_GATEWAY
                else UpstreamType.NONE
            )
            object.__setattr__(self, "upstream", default)
        return self

    @model_validator(mode="after")
    def _check_target(self):
        if self.target is not Target.COMPOSE:
            raise ValueError(
                f"target: {self.target} は v1 では未対応です。"
                "現在は compose のみ対応しています。"
            )
        return self

    @model_validator(mode="after")
    def _check_rule1_aigw_v2_is_konnect_only(self):
        if (
            self.gateway is Gateway.AI_GATEWAY_V2
            and self.control_plane is ControlPlane.SELF_MANAGED
        ):
            raise ValueError(
                "ai-gateway-v2 は Konnect 専用です"
                "（データプレーンが KONG_KONNECT_MODE: on を前提にしているため、"
                "自前の Control Plane に接続する構成が存在しません）。"
                "control_plane: konnect にするか、gateway: ai-gateway-v1 にしてください。"
            )
        return self

    @model_validator(mode="after")
    def _check_rule2_semantic_needs_vectordb(self):
        semantic = self.ai.semantic_cache or self.ai.semantic_routing
        if semantic and self.vectordb.type is VectorDbType.NONE:
            raise ValueError(
                "ai.semantic_cache / ai.semantic_routing を有効にする場合は、"
                "vectordb.type を redis-stack か pgvector にしてください"
                "（semantic balancer は vectordb ブロックが必須で、"
                "未設定のまま sync すると kongctl が失敗します）。"
            )
        return self

    @model_validator(mode="after")
    def _check_rule3_redis_stack_vectordb(self):
        if (
            self.vectordb.type is VectorDbType.REDIS_STACK
            and self.cache.type is not CacheType.REDIS_STACK
        ):
            raise ValueError(
                "vectordb.type: redis-stack には cache.type: redis-stack が必要です"
                "（素の redis には検索モジュールが無く、none ではコンテナ自体が存在しません）。"
                "cache.type を redis-stack にするか、vectordb.type を pgvector にしてください。"
            )
        return self

    @model_validator(mode="after")
    def _check_keycloak_realm(self):
        if self.idp.type is IdpType.KEYCLOAK and not self.idp.realm:
            raise ValueError(
                "idp.type: keycloak のときは idp.realm を指定してください"
                "（realm 名が issuer URL と realm-export.json の両方に入ります）。"
            )
        return self

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.idp.type is IdpType.ENTRA_ID:
            out.append(
                "idp.type: entra-id のため .env の AZURE_TENANT_ID / AZURE_CLIENT_ID / "
                "AZURE_CLIENT_SECRET は空欄で生成します。"
                "Entra テナント側の app registration は手作業で行い、値を .env に転記してください。"
            )
        return out


def load_spec(path: Path) -> EnvSpec:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return EnvSpec.model_validate(data)
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_schema.py -v`
Expected: 全件 PASS

- [ ] **Step 6: コミット**

```bash
git add .gitignore pyproject.toml uv.lock generator/__init__.py generator/schema.py tests/test_schema.py
git commit -m "feat(schema): env.yaml のスキーマと検証ルール 4 件

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: ポート割り当て

**Files:**
- Create: `generator/context.py`（ポート割り当て部分のみ。配線解決は Task 3）
- Create: `tests/test_ports.py`

**Interfaces:**
- Consumes: `schema.EnvSpec`、`schema.ControlPlane`、`schema.VectorDbType`、`schema.CacheType`、`schema.IdpType`
- Produces: `context.allocate_ports(spec: EnvSpec) -> dict[str, int]`、`context.PortConflictError`。キー名は `proxy` / `status` / `admin` / `manager` / `grafana` / `otlp_grpc` / `otlp_http` / `keycloak` / `cache` / `vector_pg` / `kong_pg` / `httpbin`

- [ ] **Step 1: 失敗テストを書く**

`tests/test_ports.py`:

```python
import pytest

from generator.context import PortConflictError, allocate_ports
from generator.schema import EnvSpec


def spec_from(**overrides) -> EnvSpec:
    return EnvSpec.model_validate({"customer": "acme", "gateway": "api-gateway", **overrides})


def test_konnect_minimal_ports():
    ports = allocate_ports(spec_from(upstream="none"))
    assert ports == {
        "proxy": 8000,
        "status": 8100,
        "grafana": 3000,
        "otlp_grpc": 4317,
        "otlp_http": 4318,
    }


def test_self_managed_adds_admin_and_manager_and_kong_pg():
    ports = allocate_ports(spec_from(control_plane="self-managed", upstream="none"))
    assert ports["admin"] == 8001
    assert ports["manager"] == 8002
    assert ports["kong_pg"] == 5432


def test_pgvector_takes_5432_when_kong_pg_absent():
    ports = allocate_ports(spec_from(vectordb={"type": "pgvector"}, upstream="none"))
    assert ports["vector_pg"] == 5432
    assert "kong_pg" not in ports


def test_pgvector_moves_to_5433_when_kong_pg_present():
    ports = allocate_ports(
        spec_from(
            control_plane="self-managed",
            vectordb={"type": "pgvector"},
            upstream="none",
        )
    )
    assert ports["kong_pg"] == 5432
    assert ports["vector_pg"] == 5433


def test_keycloak_and_cache_and_httpbin():
    ports = allocate_ports(
        spec_from(
            idp={"type": "keycloak", "realm": "acme"},
            cache={"type": "redis"},
        )
    )
    assert ports["keycloak"] == 8080
    assert ports["cache"] == 6379
    assert ports["httpbin"] == 8081


def test_otel_disabled_drops_observability_ports():
    ports = allocate_ports(spec_from(observability={"otel_lgtm": False}, upstream="none"))
    assert "grafana" not in ports
    assert "otlp_http" not in ports


def test_no_duplicate_ports_across_full_configuration():
    ports = allocate_ports(
        spec_from(
            control_plane="self-managed",
            idp={"type": "keycloak", "realm": "acme"},
            cache={"type": "redis-stack"},
            vectordb={"type": "pgvector"},
        )
    )
    assert len(set(ports.values())) == len(ports), ports


def test_conflict_raises_with_actionable_message(monkeypatch):
    import generator.context as ctxmod

    # 2 つのコンポーネントに同じ固定ポートを割り当てた状態を作る
    monkeypatch.setitem(ctxmod.PREFERRED_PORTS, "keycloak", 8000)
    with pytest.raises(PortConflictError) as e:
        allocate_ports(spec_from(idp={"type": "keycloak", "realm": "acme"}, upstream="none"))
    assert "8000" in str(e.value)
    assert "proxy" in str(e.value)
    assert "keycloak" in str(e.value)
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_ports.py -v`
Expected: 全件 FAIL。`ImportError: cannot import name 'PortConflictError' from 'generator.context'`

- [ ] **Step 3: context.py のポート割り当てを実装する**

`generator/context.py`（この Task ではこの内容のみ。Task 3 で追記する）:

```python
from generator.schema import (
    CacheType,
    ControlPlane,
    EnvSpec,
    IdpType,
    UpstreamType,
    VectorDbType,
)


class PortConflictError(Exception):
    pass


PREFERRED_PORTS: dict[str, int] = {
    "proxy": 8000,
    "status": 8100,
    "admin": 8001,
    "manager": 8002,
    "grafana": 3000,
    "otlp_grpc": 4317,
    "otlp_http": 4318,
    "keycloak": 8080,
    "httpbin": 8081,
    "cache": 6379,
    "kong_pg": 5432,
    "vector_pg": 5432,
}

# kong_pg より後に置くことで、両方ある構成では vector_pg がずれる
_ALLOCATION_ORDER = [
    "proxy",
    "status",
    "admin",
    "manager",
    "grafana",
    "otlp_grpc",
    "otlp_http",
    "keycloak",
    "httpbin",
    "cache",
    "kong_pg",
    "vector_pg",
]


def _required_keys(spec: EnvSpec) -> set[str]:
    keys = {"proxy", "status"}
    if spec.control_plane is ControlPlane.SELF_MANAGED:
        keys |= {"admin", "manager", "kong_pg"}
    if spec.observability.otel_lgtm:
        keys |= {"grafana", "otlp_grpc", "otlp_http"}
    if spec.idp.type is IdpType.KEYCLOAK:
        keys.add("keycloak")
    if spec.upstream is UpstreamType.HTTPBIN:
        keys.add("httpbin")
    if spec.cache.type is not CacheType.NONE:
        keys.add("cache")
    if spec.vectordb.type is VectorDbType.PGVECTOR:
        keys.add("vector_pg")
    return keys


def allocate_ports(spec: EnvSpec) -> dict[str, int]:
    required = _required_keys(spec)
    assigned: dict[str, int] = {}
    owner_of: dict[int, str] = {}

    for key in _ALLOCATION_ORDER:
        if key not in required:
            continue
        port = PREFERRED_PORTS[key]
        if port in owner_of:
            # Kong のメタストアと pgvector が両方 5432 を希望するのは想定内なので退避させる
            if key == "vector_pg":
                port += 1
            if port in owner_of:
                raise PortConflictError(
                    f"ポート {port} を {owner_of[port]} と {key} が同時に要求しています。"
                    f"generator/context.py の PREFERRED_PORTS で {key} の値を変更してください。"
                )
        assigned[key] = port
        owner_of[port] = key

    return assigned
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_ports.py -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add generator/context.py tests/test_ports.py
git commit -m "feat(context): ポート割り当てと衝突検出

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 配線解決（build_context）

**Files:**
- Modify: `generator/context.py`（Task 2 の内容に追記）
- Create: `tests/test_context.py`

**Interfaces:**
- Consumes: `context.allocate_ports`、`schema.EnvSpec` と全列挙型
- Produces: `context.build_context(spec: EnvSpec) -> Ctx`、dataclass `Ctx` / `KongCtx` / `IdpCtx` / `OtelCtx` / `CacheCtx` / `VectorCtx` / `UpstreamCtx`、`context.EMBEDDING_DIMENSIONS`、`context.UnknownEmbeddingModelError`。`Ctx` の属性は `spec` / `kong` / `idp` / `otel` / `cache` / `vector` / `upstream` / `ports` / `services` / `env_vars` / `namespace`

- [ ] **Step 1: 失敗テストを書く**

`tests/test_context.py`:

```python
import pytest

from generator.context import UnknownEmbeddingModelError, build_context
from generator.schema import EnvSpec


def spec_from(**overrides) -> EnvSpec:
    return EnvSpec.model_validate({"customer": "acme", "gateway": "api-gateway", **overrides})


AZURE_PROVIDER = {
    "type": "azure",
    "instance": "acme-foundry",
    "auth": "api-key",
    "models": [{"name": "gpt-5-6", "deployment_id": "gpt-5.6", "api_version": "2024-12-01-preview"}],
}


def test_kong_image_by_gateway():
    assert build_context(spec_from(gateway="ai-gateway-v2")).kong.image == "kong/kong-ai-gateway:2.0.1"
    assert build_context(spec_from(gateway="ai-gateway-v1")).kong.image == "kong/kong-gateway:3.14"
    assert build_context(spec_from(gateway="api-gateway")).kong.image == "kong/kong-gateway:3.14"


def test_konnect_domains_use_region():
    kong = build_context(spec_from(region="eu")).kong
    assert kong.konnect_domain == "eu.cp.konghq.com"
    assert kong.konnect_telemetry_domain == "eu.tp.konghq.com"


def test_self_managed_has_no_konnect_domains():
    kong = build_context(spec_from(control_plane="self-managed")).kong
    assert kong.konnect_domain is None
    assert kong.konnect_telemetry_domain is None


def test_cp_name_differs_between_ai_and_api_gateway():
    assert build_context(spec_from(gateway="ai-gateway-v2")).kong.cp_name == "acme-ai-gateway"
    assert build_context(spec_from(gateway="api-gateway")).kong.cp_name == "acme-gateway"


def test_keycloak_issuer_is_resolved():
    idp = build_context(spec_from(idp={"type": "keycloak", "realm": "acme"})).idp
    assert idp.enabled is True
    assert idp.issuer == "http://keycloak:8080/realms/acme"
    assert idp.client_id == "acme-client"
    assert idp.service_name == "keycloak"


def test_entra_issuer_uses_env_placeholder():
    idp = build_context(spec_from(idp={"type": "entra-id"})).idp
    assert idp.issuer == "https://login.microsoftonline.com/${AZURE_TENANT_ID}/v2.0"
    assert idp.service_name is None


def test_idp_none():
    idp = build_context(spec_from()).idp
    assert idp.enabled is False
    assert idp.issuer is None


def test_otel_endpoints():
    otel = build_context(spec_from()).otel
    assert otel.enabled is True
    assert otel.endpoint == "http://otel-lgtm:4318"
    assert otel.grpc_endpoint == "http://otel-lgtm:4317"


def test_otel_disabled():
    otel = build_context(spec_from(observability={"otel_lgtm": False})).otel
    assert otel.enabled is False
    assert otel.endpoint is None


def test_cache_image_by_type():
    assert build_context(spec_from(cache={"type": "redis"})).cache.image == "redis:8.0.2"
    assert build_context(spec_from(cache={"type": "redis-stack"})).cache.image == "redis/redis-stack:7.4.0-v3"
    assert build_context(spec_from()).cache.enabled is False


def test_vector_redis_stack_points_at_cache_service():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            cache={"type": "redis-stack"},
            vectordb={"type": "redis-stack"},
            ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
        )
    )
    assert ctx.vector.host == "redis"
    assert ctx.vector.port == 6379
    assert ctx.vector.dimensions == 3072
    assert ctx.vector.distance_metric == "cosine"


def test_vector_pgvector_uses_allocated_port():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            vectordb={"type": "pgvector"},
            ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
        )
    )
    assert ctx.vector.host == "pgvector"
    assert ctx.vector.port == 5432  # コンテナ内ポートなので退避の影響を受けない
    assert ctx.ports["vector_pg"] == 5432


def test_embedding_dimensions_for_small_model():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            cache={"type": "redis-stack"},
            vectordb={"type": "redis-stack"},
            ai={
                "providers": [AZURE_PROVIDER],
                "semantic_cache": True,
                "embedding_model": "text-embedding-3-small",
            },
        )
    )
    assert ctx.vector.dimensions == 1536


def test_unknown_embedding_model_is_rejected():
    with pytest.raises(UnknownEmbeddingModelError) as e:
        build_context(
            spec_from(
                gateway="ai-gateway-v2",
                cache={"type": "redis-stack"},
                vectordb={"type": "redis-stack"},
                ai={
                    "providers": [AZURE_PROVIDER],
                    "semantic_cache": True,
                    "embedding_model": "made-up-model",
                },
            )
        )
    assert "made-up-model" in str(e.value)
    assert "EMBEDDING_DIMENSIONS" in str(e.value)


def test_upstream_httpbin():
    up = build_context(spec_from()).upstream
    assert up.enabled is True
    assert up.url == "http://httpbin:80"


def test_services_order_konnect_ai_gateway_v2():
    ctx = build_context(
        spec_from(
            gateway="ai-gateway-v2",
            cache={"type": "redis-stack"},
            vectordb={"type": "redis-stack"},
            idp={"type": "keycloak", "realm": "acme"},
            ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
        )
    )
    assert ctx.services == ["kong-aigw-v2", "redis", "keycloak", "otel-lgtm"]


def test_services_order_self_managed_api_gateway():
    ctx = build_context(
        spec_from(
            control_plane="self-managed",
            vectordb={"type": "pgvector"},
            idp={"type": "keycloak", "realm": "acme"},
        )
    )
    assert ctx.services == ["kong-self-managed", "httpbin", "pgvector", "keycloak", "otel-lgtm"]


def test_services_for_ai_gateway_v1_use_konnect_dp():
    ctx = build_context(spec_from(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]}))
    assert ctx.services == ["kong-dp", "otel-lgtm"]


def test_env_vars_konnect_azure_api_key():
    ctx = build_context(spec_from(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert ctx.env_vars["CONTROL_PLANE_ID"] == ""
    assert ctx.env_vars["AZURE_OPENAI_API_KEY"] == ""


def test_env_vars_self_managed_needs_license():
    ctx = build_context(spec_from(control_plane="self-managed"))
    assert ctx.env_vars["KONG_LICENSE_DATA"] == ""
    assert "CONTROL_PLANE_ID" not in ctx.env_vars


def test_env_vars_keycloak_has_deterministic_secret():
    ctx = build_context(spec_from(idp={"type": "keycloak", "realm": "acme"}))
    assert ctx.env_vars["KEYCLOAK_ADMIN"] == "admin"
    assert ctx.env_vars["KEYCLOAK_CLIENT_SECRET"] == "local-dev-secret"


def test_env_vars_entra_are_blank():
    ctx = build_context(spec_from(idp={"type": "entra-id"}))
    assert ctx.env_vars["AZURE_TENANT_ID"] == ""
    assert ctx.env_vars["AZURE_CLIENT_ID"] == ""
    assert ctx.env_vars["AZURE_CLIENT_SECRET"] == ""


def test_namespace_is_customer():
    assert build_context(spec_from()).namespace == "acme"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_context.py -v`
Expected: 全件 FAIL。`ImportError: cannot import name 'build_context' from 'generator.context'`

- [ ] **Step 3: context.py に配線解決を追記する**

`generator/context.py` の先頭 import 行を差し替え、`allocate_ports` の後ろに以下を追記する。

import 行（ファイル先頭）:

```python
from dataclasses import dataclass

from generator.schema import (
    AuthType,
    CacheType,
    ControlPlane,
    EnvSpec,
    Gateway,
    IdpType,
    ProviderType,
    UpstreamType,
    VectorDbType,
)
```

`allocate_ports` の後ろに追記:

```python
class UnknownEmbeddingModelError(Exception):
    pass


KONG_IMAGES = {
    Gateway.AI_GATEWAY_V2: "kong/kong-ai-gateway:2.0.1",
    Gateway.AI_GATEWAY_V1: "kong/kong-gateway:3.14",
    Gateway.API_GATEWAY: "kong/kong-gateway:3.14",
}

CACHE_IMAGES = {
    CacheType.REDIS: "redis:8.0.2",
    CacheType.REDIS_STACK: "redis/redis-stack:7.4.0-v3",
}

EMBEDDING_DIMENSIONS = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
    "amazon.titan-embed-text-v2:0": 1024,
}

# Konnect DP の識別に使うラベル。手元の macOS からの接続であることが Konnect 上で見分けられる
DP_LABELS = "type:docker-macOsArmOS"


@dataclass(frozen=True)
class KongCtx:
    image: str
    cp_name: str
    konnect_domain: str | None
    konnect_telemetry_domain: str | None
    dp_labels: str


@dataclass(frozen=True)
class IdpCtx:
    type: IdpType
    enabled: bool
    realm: str | None
    issuer: str | None
    client_id: str | None
    service_name: str | None


@dataclass(frozen=True)
class OtelCtx:
    enabled: bool
    endpoint: str | None
    grpc_endpoint: str | None


@dataclass(frozen=True)
class CacheCtx:
    enabled: bool
    type: CacheType
    image: str | None
    host: str | None
    port: int


@dataclass(frozen=True)
class VectorCtx:
    enabled: bool
    type: VectorDbType
    host: str | None
    port: int
    dimensions: int
    distance_metric: str
    threshold: float
    database: str
    user: str


@dataclass(frozen=True)
class UpstreamCtx:
    enabled: bool
    service_name: str | None
    url: str | None


@dataclass(frozen=True)
class Ctx:
    spec: EnvSpec
    kong: KongCtx
    idp: IdpCtx
    otel: OtelCtx
    cache: CacheCtx
    vector: VectorCtx
    upstream: UpstreamCtx
    ports: dict[str, int]
    services: list[str]
    env_vars: dict[str, str]
    namespace: str


def _build_kong(spec: EnvSpec) -> KongCtx:
    konnect = spec.control_plane is ControlPlane.KONNECT
    suffix = "ai-gateway" if spec.is_ai else "gateway"
    return KongCtx(
        image=KONG_IMAGES[spec.gateway],
        cp_name=f"{spec.customer}-{suffix}",
        konnect_domain=f"{spec.region}.cp.konghq.com" if konnect else None,
        konnect_telemetry_domain=f"{spec.region}.tp.konghq.com" if konnect else None,
        dp_labels=DP_LABELS,
    )


def _build_idp(spec: EnvSpec) -> IdpCtx:
    if spec.idp.type is IdpType.KEYCLOAK:
        return IdpCtx(
            type=IdpType.KEYCLOAK,
            enabled=True,
            realm=spec.idp.realm,
            issuer=f"http://keycloak:8080/realms/{spec.idp.realm}",
            client_id=f"{spec.customer}-client",
            service_name="keycloak",
        )
    if spec.idp.type is IdpType.ENTRA_ID:
        # テナント ID は生成時に判明しないため compose の変数参照のまま Kong に渡す
        return IdpCtx(
            type=IdpType.ENTRA_ID,
            enabled=True,
            realm=None,
            issuer="https://login.microsoftonline.com/${AZURE_TENANT_ID}/v2.0",
            client_id="${AZURE_CLIENT_ID}",
            service_name=None,
        )
    return IdpCtx(
        type=IdpType.NONE, enabled=False, realm=None, issuer=None, client_id=None, service_name=None
    )


def _build_otel(spec: EnvSpec) -> OtelCtx:
    if not spec.observability.otel_lgtm:
        return OtelCtx(enabled=False, endpoint=None, grpc_endpoint=None)
    return OtelCtx(
        enabled=True,
        endpoint="http://otel-lgtm:4318",
        grpc_endpoint="http://otel-lgtm:4317",
    )


def _build_cache(spec: EnvSpec) -> CacheCtx:
    if spec.cache.type is CacheType.NONE:
        return CacheCtx(enabled=False, type=CacheType.NONE, image=None, host=None, port=6379)
    return CacheCtx(
        enabled=True,
        type=spec.cache.type,
        image=CACHE_IMAGES[spec.cache.type],
        host="redis",
        port=6379,
    )


def _build_vector(spec: EnvSpec) -> VectorCtx:
    if spec.vectordb.type is VectorDbType.NONE:
        return VectorCtx(
            enabled=False,
            type=VectorDbType.NONE,
            host=None,
            port=0,
            dimensions=0,
            distance_metric="cosine",
            threshold=0.7,
            database="",
            user="",
        )

    model = spec.ai.embedding_model
    if model not in EMBEDDING_DIMENSIONS:
        raise UnknownEmbeddingModelError(
            f"埋め込みモデル {model} の次元数が分かりません。"
            "generator/context.py の EMBEDDING_DIMENSIONS にモデル名と次元数を追加してください。"
        )

    if spec.vectordb.type is VectorDbType.REDIS_STACK:
        host, port, database, user = "redis", 6379, "", ""
    else:
        host, port, database, user = "pgvector", 5432, "vectors", "kong"

    return VectorCtx(
        enabled=True,
        type=spec.vectordb.type,
        host=host,
        # コンテナ内のポート。ホスト側の退避（5433）は ports 側だけの話
        port=port,
        dimensions=EMBEDDING_DIMENSIONS[model],
        distance_metric="cosine",
        threshold=0.7,
        database=database,
        user=user,
    )


def _build_upstream(spec: EnvSpec) -> UpstreamCtx:
    if spec.upstream is UpstreamType.HTTPBIN:
        return UpstreamCtx(enabled=True, service_name="httpbin", url="http://httpbin:80")
    return UpstreamCtx(enabled=False, service_name=None, url=None)


def _build_services(spec: EnvSpec) -> list[str]:
    services: list[str] = []
    if spec.gateway is Gateway.AI_GATEWAY_V2:
        services.append("kong-aigw-v2")
    elif spec.control_plane is ControlPlane.SELF_MANAGED:
        services.append("kong-self-managed")
    else:
        services.append("kong-dp")

    if spec.upstream is UpstreamType.HTTPBIN:
        services.append("httpbin")
    if spec.cache.type is not CacheType.NONE:
        services.append("redis")
    if spec.vectordb.type is VectorDbType.PGVECTOR:
        services.append("pgvector")
    if spec.idp.type is IdpType.KEYCLOAK:
        services.append("keycloak")
    if spec.observability.otel_lgtm:
        services.append("otel-lgtm")
    return services


_PROVIDER_ENV = {
    ProviderType.OPENAI: ["OPENAI_API_KEY"],
    ProviderType.ANTHROPIC: ["ANTHROPIC_API_KEY"],
    ProviderType.VERTEX: ["GOOGLE_APPLICATION_CREDENTIALS"],
    ProviderType.BEDROCK: ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
}


def _build_env_vars(spec: EnvSpec) -> dict[str, str]:
    env: dict[str, str] = {}

    if spec.control_plane is ControlPlane.KONNECT:
        env["CONTROL_PLANE_ID"] = ""
    else:
        env["KONG_LICENSE_DATA"] = ""

    for provider in spec.ai.providers:
        if provider.type is ProviderType.AZURE:
            if provider.auth is AuthType.API_KEY:
                env["AZURE_OPENAI_API_KEY"] = ""
            else:
                env["AZURE_TENANT_ID"] = ""
                env["AZURE_CLIENT_ID"] = ""
        else:
            for key in _PROVIDER_ENV.get(provider.type, []):
                env[key] = ""

    if spec.idp.type is IdpType.KEYCLOAK:
        env["KEYCLOAK_ADMIN"] = "admin"
        env["KEYCLOAK_ADMIN_PASSWORD"] = "admin"
        # ローカル検証用の固定値。乱数にするとゴールデンテストが毎回落ちる
        env["KEYCLOAK_CLIENT_SECRET"] = "local-dev-secret"
    elif spec.idp.type is IdpType.ENTRA_ID:
        env["AZURE_TENANT_ID"] = ""
        env["AZURE_CLIENT_ID"] = ""
        env["AZURE_CLIENT_SECRET"] = ""

    return env


def build_context(spec: EnvSpec) -> Ctx:
    return Ctx(
        spec=spec,
        kong=_build_kong(spec),
        idp=_build_idp(spec),
        otel=_build_otel(spec),
        cache=_build_cache(spec),
        vector=_build_vector(spec),
        upstream=_build_upstream(spec),
        ports=allocate_ports(spec),
        services=_build_services(spec),
        env_vars=_build_env_vars(spec),
        namespace=spec.customer,
    )
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_context.py -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add generator/context.py tests/test_context.py
git commit -m "feat(context): コンポーネント間の配線解決

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: クラスタ証明書の生成

**Files:**
- Create: `generator/certs.py`
- Create: `tests/test_certs.py`

**Interfaces:**
- Consumes: なし
- Produces: `certs.generate_cluster_cert(out_dir: Path, common_name: str, days: int = 1095) -> tuple[Path, Path]`（`(crt, key)` を返す）、`certs.OpensslError`

- [ ] **Step 1: 失敗テストを書く**

`tests/test_certs.py`:

```python
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
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_certs.py -v`
Expected: 全件 FAIL。`ModuleNotFoundError: No module named 'generator.certs'`

- [ ] **Step 3: certs.py を実装する**

`generator/certs.py`:

```python
import subprocess
from pathlib import Path


class OpensslError(Exception):
    pass


def generate_cluster_cert(
    out_dir: Path, common_name: str, days: int = 1095
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    crt = out_dir / "cluster.crt"
    key = out_dir / "cluster.key"

    cmd = [
        "openssl",
        "req",
        "-new",
        "-x509",
        # Kong はパスフレーズ付きの鍵を読めないので -nodes は必須
        "-nodes",
        "-newkey",
        "rsa:2048",
        "-subj",
        f"/CN={common_name}/C=JP",
        "-keyout",
        str(key),
        "-out",
        str(crt),
        "-days",
        str(days),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise OpensslError(
            f"クラスタ証明書の生成に失敗しました（openssl の終了コード {result.returncode}）。\n"
            f"{result.stderr.strip()}"
        )

    key.chmod(0o600)
    return crt, key
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_certs.py -v`
Expected: 全件 PASS

- [ ] **Step 5: コミット**

```bash
git add generator/certs.py tests/test_certs.py
git commit -m "feat(certs): Konnect 用クラスタ証明書の生成

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 描画エンジンと compose 骨格

**Files:**
- Create: `generator/render.py`
- Create: `generator/templates/compose.yaml.j2`
- Create: `generator/templates/services/otel-lgtm.yaml.j2`
- Create: `tests/conftest.py`
- Create: `tests/test_render_compose.py`

**Interfaces:**
- Consumes: `context.build_context`、`context.Ctx`
- Produces: `render.build_env() -> jinja2.Environment`、`render.render_all(ctx: Ctx) -> dict[str, str]`（相対パス → 中身）、`render.TEMPLATE_DIR`。テスト用フィクスチャ `tests/conftest.py` の `ctx_for(**overrides) -> Ctx`

このタスクでは `render_all` は `compose.yaml` のみを返す。以降のタスクで返すファイルが増えていく。

- [ ] **Step 1: 共通フィクスチャと失敗テストを書く**

`tests/conftest.py`:

```python
import dataclasses

import pytest
import yaml

from generator.context import Ctx, build_context
from generator.schema import EnvSpec

AZURE_PROVIDER = {
    "type": "azure",
    "instance": "acme-foundry",
    "auth": "api-key",
    "models": [
        {"name": "gpt-5-6", "deployment_id": "gpt-5.6", "api_version": "2024-12-01-preview"}
    ],
}


def ctx_for(**overrides) -> Ctx:
    data = {"customer": "acme", "gateway": "api-gateway", **overrides}
    return build_context(EnvSpec.model_validate(data))


def only_services(ctx: Ctx, *names: str) -> Ctx:
    """まだ書いていないパーシャルを外して骨格だけ確認するためのヘルパ。"""
    return dataclasses.replace(ctx, services=list(names))


@pytest.fixture
def compose_of():
    def _compose_of(ctx: Ctx) -> dict:
        from generator.render import render_all

        return yaml.safe_load(render_all(ctx)["compose.yaml"])

    return _compose_of
```

`tests/test_render_compose.py`:

```python
import pytest
import yaml
from jinja2 import UndefinedError

from generator.render import build_env, render_all
from tests.conftest import ctx_for, only_services


def test_env_uses_strict_undefined():
    env = build_env()
    template = env.from_string("{{ missing_variable }}")
    with pytest.raises(UndefinedError):
        template.render()


def test_env_trims_blocks():
    env = build_env()
    rendered = env.from_string("a\n{% if true %}\nb\n{% endif %}\nc\n").render()
    assert rendered == "a\nb\nc\n"


def test_compose_is_valid_yaml_with_anchor_resolved(compose_of):
    doc = compose_of(only_services(ctx_for(), "otel-lgtm"))
    assert doc["services"]["otel-lgtm"]["networks"] == ["kong-network"]
    assert doc["services"]["otel-lgtm"]["restart"] == "on-failure"


def test_compose_defines_network(compose_of):
    doc = compose_of(only_services(ctx_for(), "otel-lgtm"))
    assert "kong-network" in doc["networks"]


def test_kong_env_anchor_only_for_self_managed():
    raw = render_all(only_services(ctx_for(), "otel-lgtm"))["compose.yaml"]
    assert "x-kong-env" not in raw

    raw_sm = render_all(
        only_services(ctx_for(control_plane="self-managed"), "otel-lgtm")
    )["compose.yaml"]
    assert "x-kong-env: &kong-env" in raw_sm
    assert "KONG_PG_HOST: database" in raw_sm


def test_otel_ports_come_from_allocation(compose_of):
    doc = compose_of(only_services(ctx_for(), "otel-lgtm"))
    assert doc["services"]["otel-lgtm"]["ports"] == ["3000:3000", "4317:4317", "4318:4318"]


def test_partial_renders_at_indent_zero_and_is_indented_by_skeleton():
    raw = render_all(only_services(ctx_for(), "otel-lgtm"))["compose.yaml"]
    # first=True を省くと 1 行目だけインデントされず compose が壊れる
    assert "\n  otel-lgtm:\n" in raw
    assert "\notel-lgtm:\n" not in raw


def test_services_partial_is_standalone_parseable():
    env = build_env()
    ctx = ctx_for()
    raw = env.get_template("services/otel-lgtm.yaml.j2").render(
        ctx=ctx, spec=ctx.spec, ports=ctx.ports, otel=ctx.otel
    )
    # パーシャルはインデント 0 なので単体でも YAML として読める
    assert "otel-lgtm" in yaml.safe_load(raw)
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_render_compose.py -v`
Expected: 全件 FAIL。`ModuleNotFoundError: No module named 'generator.render'`

- [ ] **Step 3: render.py を実装する**

`generator/render.py`:

```python
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from generator.context import Ctx

TEMPLATE_DIR = Path(__file__).parent / "templates"


def build_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        # 未定義変数が空文字になると、一見正しい形の壊れた設定が黙って生成される
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def template_vars(ctx: Ctx) -> dict:
    return {
        "ctx": ctx,
        "spec": ctx.spec,
        "kong": ctx.kong,
        "idp": ctx.idp,
        "otel": ctx.otel,
        "cache": ctx.cache,
        "vector": ctx.vector,
        "upstream": ctx.upstream,
        "ports": ctx.ports,
        "services": ctx.services,
        "env_vars": ctx.env_vars,
        "namespace": ctx.namespace,
    }


def render_all(ctx: Ctx) -> dict[str, str]:
    env = build_env()
    variables = template_vars(ctx)
    return {
        "compose.yaml": env.get_template("compose.yaml.j2").render(**variables),
    }
```

- [ ] **Step 4: compose 骨格と otel-lgtm パーシャルを書く**

`generator/templates/compose.yaml.j2`:

```jinja
x-default: &default
  networks:
    - kong-network
  restart: on-failure
{% if spec.control_plane.value == 'self-managed' %}

x-kong-env: &kong-env
  KONG_DATABASE: postgres
  KONG_PG_HOST: database
  KONG_PG_PASSWORD: kong
  KONG_PASSWORD: kong
{% endif %}

networks:
  kong-network:

services:
{% for service in services %}
{% filter indent(width=2, first=True) %}
{% include 'services/' ~ service ~ '.yaml.j2' %}
{% endfilter %}
{% endfor %}
```

`generator/templates/services/otel-lgtm.yaml.j2`:

```jinja
otel-lgtm:
  <<: *default
  container_name: otel-lgtm
  image: grafana/otel-lgtm:0.25.0
  ports:
    - {{ ports.grafana }}:3000
    - {{ ports.otlp_grpc }}:4317
    - {{ ports.otlp_http }}:4318
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_render_compose.py -v`
Expected: 全件 PASS

`test_partial_renders_at_indent_zero_and_is_indented_by_skeleton` が落ちる場合は `{% filter indent(width=2, first=True) %}` の `first=True` が抜けている。

- [ ] **Step 6: コミット**

```bash
git add generator/render.py generator/templates/compose.yaml.j2 generator/templates/services/otel-lgtm.yaml.j2 tests/conftest.py tests/test_render_compose.py
git commit -m "feat(render): Jinja2 環境と compose 骨格、otel-lgtm

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: キャッシュとベクトル DB のサービス

**Files:**
- Create: `generator/templates/services/redis.yaml.j2`
- Create: `generator/templates/services/pgvector.yaml.j2`
- Modify: `tests/test_render_compose.py`（テストを追記）

**Interfaces:**
- Consumes: `render.render_all`、`context.CacheCtx`、`context.VectorCtx`、`ports`
- Produces: compose に `redis` および `pgvector` サービスを出すパーシャル。サービス名は配線値と一致させる（`cache.host == "redis"`、`vector.host == "pgvector"`）

- [ ] **Step 1: 失敗テストを `tests/test_render_compose.py` の末尾に追記する**

```python
def test_plain_redis_image_and_healthcheck(compose_of):
    doc = compose_of(only_services(ctx_for(cache={"type": "redis"}), "redis"))
    svc = doc["services"]["redis"]
    assert svc["image"] == "redis:8.0.2"
    assert svc["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert svc["ports"] == ["6379:6379"]


def test_redis_stack_image(compose_of):
    doc = compose_of(only_services(ctx_for(cache={"type": "redis-stack"}), "redis"))
    assert doc["services"]["redis"]["image"] == "redis/redis-stack:7.4.0-v3"


def test_redis_service_name_matches_wiring():
    ctx = ctx_for(cache={"type": "redis-stack"})
    # kongctl.yaml が vectordb.host として書く値と compose のサービス名がずれると接続できない
    assert ctx.cache.host == "redis"
    assert "redis" in render_all(only_services(ctx, "redis"))["compose.yaml"]


def test_pgvector_service(compose_of):
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        vectordb={"type": "pgvector"},
        ai={"semantic_cache": True},
    )
    doc = compose_of(only_services(ctx, "pgvector"))
    svc = doc["services"]["pgvector"]
    assert svc["image"] == "pgvector/pgvector:pg17"
    assert svc["environment"]["POSTGRES_DB"] == "vectors"
    assert svc["environment"]["POSTGRES_USER"] == "kong"
    assert svc["ports"] == ["5432:5432"]
    assert svc["healthcheck"]["test"] == ["CMD-SHELL", "pg_isready -U kong -d vectors"]


def test_pgvector_host_port_shifts_when_kong_metastore_present(compose_of):
    ctx = ctx_for(
        control_plane="self-managed",
        vectordb={"type": "pgvector"},
    )
    doc = compose_of(only_services(ctx, "pgvector"))
    # ホスト側は 5433 に退避するが、コンテナ内は 5432 のまま
    assert doc["services"]["pgvector"]["ports"] == ["5433:5432"]


def test_pgvector_enables_extension_on_init(compose_of):
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        vectordb={"type": "pgvector"},
        ai={"semantic_cache": True},
    )
    doc = compose_of(only_services(ctx, "pgvector"))
    # pgvector イメージでも CREATE EXTENSION は自分で実行する必要がある
    assert "/docker-entrypoint-initdb.d/" in doc["services"]["pgvector"]["volumes"][0]
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_render_compose.py -v -k "redis or pgvector"`
Expected: FAIL。`jinja2.exceptions.TemplateNotFound: services/redis.yaml.j2`

- [ ] **Step 3: パーシャルを書く**

`generator/templates/services/redis.yaml.j2`:

```jinja
redis:
  <<: *default
  container_name: redis
  image: {{ cache.image }}
  ports:
    - {{ ports.cache }}:6379
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 10s
    timeout: 5s
    retries: 5
```

`generator/templates/services/pgvector.yaml.j2`:

```jinja
pgvector:
  <<: *default
  container_name: pgvector
  image: pgvector/pgvector:pg17
  environment:
    POSTGRES_DB: {{ vector.database }}
    POSTGRES_USER: {{ vector.user }}
    POSTGRES_PASSWORD: kong
  ports:
    - {{ ports.vector_pg }}:5432
  volumes:
    - ./config/pgvector/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U {{ vector.user }} -d {{ vector.database }}"]
    interval: 10s
    timeout: 5s
    retries: 5
```

- [ ] **Step 4: pgvector の初期化 SQL を render_all に追加する**

`generator/render.py` の `render_all` を差し替える:

```python
def render_all(ctx: Ctx) -> dict[str, str]:
    env = build_env()
    variables = template_vars(ctx)
    files = {
        "compose.yaml": env.get_template("compose.yaml.j2").render(**variables),
    }
    if ctx.vector.enabled and ctx.vector.type.value == "pgvector":
        files["config/pgvector/init.sql"] = "CREATE EXTENSION IF NOT EXISTS vector;\n"
    return files
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_render_compose.py -v`
Expected: 全件 PASS

- [ ] **Step 6: コミット**

```bash
git add generator/templates/services/redis.yaml.j2 generator/templates/services/pgvector.yaml.j2 generator/render.py tests/test_render_compose.py
git commit -m "feat(templates): redis と pgvector のサービス

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Kong サービス 3 種

**Files:**
- Create: `generator/templates/services/kong-aigw-v2.yaml.j2`
- Create: `generator/templates/services/kong-dp.yaml.j2`
- Create: `generator/templates/services/kong-self-managed.yaml.j2`
- Modify: `generator/certs.py`（`basename` パラメータを追加）
- Modify: `tests/test_certs.py`（テストを追記）
- Modify: `tests/test_render_compose.py`（テストを追記）

**Interfaces:**
- Consumes: `context.KongCtx`、`context.OtelCtx`、`ports`
- Produces: 3 つのパーシャル。`kong-self-managed` は `database` / `kong-bootstrap` / `kong-cp` / `kong-dp` の 4 サービスを 1 ファイルで出す。`certs.generate_cluster_cert(out_dir, common_name, days=1095, basename="cluster")` に `basename` を追加

**この Task で必ず守ること:** Kong の環境変数のうち値が `on` / `off` になるものは、テンプレート側で必ずクォートする。YAML 1.1 を実装する PyYAML は裸の `off` を `False` と解釈し、YAML 1.2 の docker compose は文字列 `"off"` のまま扱うため、クォートしないとテストの読み取り結果と実際にコンテナへ渡る値がずれる。Kong は `database` に `"false"` を受け付けないので、この差は実際に起動失敗を招く。

- [ ] **Step 1: certs.py の basename 追加の失敗テストを書く**

`tests/test_certs.py` の末尾に追記:

```python
def test_basename_controls_filenames(tmp_path):
    crt, key = generate_cluster_cert(
        tmp_path / "certs", common_name="acme-cluster", basename="tls"
    )
    assert crt.name == "tls.crt"
    assert key.name == "tls.key"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_certs.py::test_basename_controls_filenames -v`
Expected: FAIL。`TypeError: generate_cluster_cert() got an unexpected keyword argument 'basename'`

- [ ] **Step 3: certs.py に basename を追加する**

`generator/certs.py` のシグネチャと 2 行を差し替える:

```python
def generate_cluster_cert(
    out_dir: Path, common_name: str, days: int = 1095, basename: str = "cluster"
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    crt = out_dir / f"{basename}.crt"
    key = out_dir / f"{basename}.key"
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_certs.py -v`
Expected: 全件 PASS

- [ ] **Step 5: Kong サービスの失敗テストを `tests/test_render_compose.py` の末尾に追記する**

```python
def test_aigw_v2_konnect_wiring(compose_of):
    ctx = ctx_for(gateway="ai-gateway-v2", region="eu")
    doc = compose_of(only_services(ctx, "kong-aigw-v2"))
    env = doc["services"]["kong"]["environment"]
    assert env["KONG_CLUSTER_CONTROL_PLANE"] == "${CONTROL_PLANE_ID:-}.eu.cp.konghq.com:443"
    assert env["KONG_CLUSTER_TELEMETRY_ENDPOINT"] == "${CONTROL_PLANE_ID:-}.eu.tp.konghq.com:443"
    assert env["KONG_CLUSTER_MTLS"] == "pki"
    assert doc["services"]["kong"]["volumes"] == [".certs:/etc/kong/cluster-certs"]


def test_on_off_values_are_quoted_strings(compose_of):
    # 裸の off は PyYAML が False にする。Kong は "false" を受け付けないので必ず文字列で渡す
    doc = compose_of(only_services(ctx_for(gateway="ai-gateway-v2"), "kong-aigw-v2"))
    env = doc["services"]["kong"]["environment"]
    assert env["KONG_DATABASE"] == "off"
    assert env["KONG_VITALS"] == "off"
    assert env["KONG_KONNECT_MODE"] == "on"


def test_aigw_v2_uses_expressions_router(compose_of):
    doc = compose_of(only_services(ctx_for(gateway="ai-gateway-v2"), "kong-aigw-v2"))
    assert doc["services"]["kong"]["environment"]["KONG_ROUTER_FLAVOR"] == "expressions"


def test_tracing_only_when_otel_enabled(compose_of):
    on = compose_of(only_services(ctx_for(gateway="ai-gateway-v2"), "kong-aigw-v2"))
    assert on["services"]["kong"]["environment"]["KONG_TRACING_INSTRUMENTATIONS"] == "all"

    off = compose_of(
        only_services(
            ctx_for(gateway="ai-gateway-v2", observability={"otel_lgtm": False}),
            "kong-aigw-v2",
        )
    )
    assert "KONG_TRACING_INSTRUMENTATIONS" not in off["services"]["kong"]["environment"]


def test_konnect_dp_for_api_gateway(compose_of):
    doc = compose_of(only_services(ctx_for(), "kong-dp"))
    svc = doc["services"]["gateway"]
    assert svc["image"] == "kong/kong-gateway:3.14"
    assert svc["environment"]["KONG_ROLE"] == "data_plane"
    assert svc["environment"]["KONG_KONNECT_MODE"] == "on"
    assert svc["ports"] == ["8000:8000", "8100:8100"]


def test_self_managed_emits_four_services(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    for name in ("database", "kong-bootstrap", "kong-cp", "kong-dp"):
        assert name in doc["services"], name


def test_self_managed_bootstrap_ordering(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    # bootstrap 完了前に CP が起動すると migrations 未適用で落ちる
    assert doc["services"]["kong-bootstrap"]["depends_on"]["database"]["condition"] == "service_healthy"
    assert (
        doc["services"]["kong-cp"]["depends_on"]["kong-bootstrap"]["condition"]
        == "service_completed_successfully"
    )


def test_self_managed_uses_shared_mtls_and_mounted_certs(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    assert doc["services"]["kong-dp"]["environment"]["KONG_CLUSTER_MTLS"] == "shared"
    assert doc["services"]["kong-cp"]["volumes"] == ["./config/kong/certs:/etc/kong/certs"]


def test_self_managed_admin_and_manager_ports(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    assert doc["services"]["kong-cp"]["ports"] == ["8001:8001", "8002:8002"]


def test_self_managed_dp_trusts_forwarded_ip(compose_of):
    doc = compose_of(only_services(ctx_for(control_plane="self-managed"), "kong-self-managed"))
    env = doc["services"]["kong-dp"]["environment"]
    assert env["KONG_TRUSTED_IPS"] == "0.0.0.0/0"
    assert env["KONG_REAL_IP_HEADER"] == "X-Forwarded-For"
    assert env["KONG_REAL_IP_RECURSIVE"] == "on"
```

- [ ] **Step 6: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_render_compose.py -v -k "aigw or dp or self_managed or quoted"`
Expected: FAIL。`jinja2.exceptions.TemplateNotFound: services/kong-aigw-v2.yaml.j2`

- [ ] **Step 7: 3 つのパーシャルを書く**

`generator/templates/services/kong-aigw-v2.yaml.j2`:

```jinja
kong:
  <<: *default
  container_name: kong
  image: {{ kong.image }}
  ports:
    - {{ ports.proxy }}:8000
    - {{ ports.status }}:8100
  environment:
    KONG_ROLE: data_plane
    KONG_DATABASE: "off"
    KONG_VITALS: "off"
    KONG_CLUSTER_MTLS: pki
    KONG_CLUSTER_CONTROL_PLANE: ${CONTROL_PLANE_ID:-}.{{ kong.konnect_domain }}:443
    KONG_CLUSTER_SERVER_NAME: ${CONTROL_PLANE_ID:-}.{{ kong.konnect_domain }}
    KONG_CLUSTER_TELEMETRY_ENDPOINT: ${CONTROL_PLANE_ID:-}.{{ kong.konnect_telemetry_domain }}:443
    KONG_CLUSTER_TELEMETRY_SERVER_NAME: ${CONTROL_PLANE_ID:-}.{{ kong.konnect_telemetry_domain }}
    KONG_CLUSTER_CERT: /etc/kong/cluster-certs/cluster.crt
    KONG_CLUSTER_CERT_KEY: /etc/kong/cluster-certs/cluster.key
    KONG_LUA_SSL_TRUSTED_CERTIFICATE: system
    KONG_KONNECT_MODE: "on"
    KONG_CLUSTER_DP_LABELS: {{ kong.dp_labels }}
    KONG_ROUTER_FLAVOR: expressions
    KONG_STATUS_LISTEN: 0.0.0.0:8100
    KONG_LOG_LEVEL: debug
    KONG_PROXY_ACCESS_LOG: /dev/stdout
    KONG_PROXY_ERROR_LOG: /dev/stderr
{% if otel.enabled %}
    KONG_TRACING_INSTRUMENTATIONS: all
    KONG_TRACING_SAMPLING_RATE: "1.0"
{% endif %}
  volumes:
    - .certs:/etc/kong/cluster-certs
  healthcheck:
    test: ["CMD", "kong", "health"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 60s
```

`generator/templates/services/kong-dp.yaml.j2`:

```jinja
gateway:
  <<: *default
  container_name: gateway
  image: {{ kong.image }}
  ports:
    - {{ ports.proxy }}:8000
    - {{ ports.status }}:8100
  environment:
    KONG_ROLE: data_plane
    KONG_DATABASE: "off"
    KONG_VITALS: "off"
    KONG_CLUSTER_MTLS: pki
    KONG_CLUSTER_CONTROL_PLANE: ${CONTROL_PLANE_ID:-}.{{ kong.konnect_domain }}:443
    KONG_CLUSTER_SERVER_NAME: ${CONTROL_PLANE_ID:-}.{{ kong.konnect_domain }}
    KONG_CLUSTER_TELEMETRY_ENDPOINT: ${CONTROL_PLANE_ID:-}.{{ kong.konnect_telemetry_domain }}:443
    KONG_CLUSTER_TELEMETRY_SERVER_NAME: ${CONTROL_PLANE_ID:-}.{{ kong.konnect_telemetry_domain }}
    KONG_CLUSTER_CERT: /etc/kong/cluster-certs/cluster.crt
    KONG_CLUSTER_CERT_KEY: /etc/kong/cluster-certs/cluster.key
    KONG_LUA_SSL_TRUSTED_CERTIFICATE: system
    KONG_KONNECT_MODE: "on"
    KONG_CLUSTER_DP_LABELS: {{ kong.dp_labels }}
    KONG_ROUTER_FLAVOR: expressions
    KONG_STATUS_LISTEN: 0.0.0.0:8100
    KONG_LOG_LEVEL: debug
    KONG_PROXY_ACCESS_LOG: /dev/stdout
    KONG_PROXY_ERROR_LOG: /dev/stderr
{% if otel.enabled %}
    KONG_TRACING_INSTRUMENTATIONS: all
    KONG_TRACING_SAMPLING_RATE: "1.0"
{% endif %}
  volumes:
    - .certs:/etc/kong/cluster-certs
  healthcheck:
    test: ["CMD", "kong", "health"]
    interval: 30s
    timeout: 10s
    retries: 3
    start_period: 60s
```

`generator/templates/services/kong-self-managed.yaml.j2`:

```jinja
database:
  <<: *default
  container_name: database
  image: postgres:17
  environment:
    POSTGRES_USER: kong
    POSTGRES_DB: kong
    POSTGRES_PASSWORD: kong
  ports:
    - {{ ports.kong_pg }}:5432
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U kong"]
    interval: 10s
    timeout: 10s
    retries: 5

kong-bootstrap:
  <<: *default
  container_name: kong-bootstrap
  image: {{ kong.image }}
  restart: "no"
  depends_on:
    database:
      condition: service_healthy
  command: kong migrations bootstrap
  environment:
    <<: *kong-env
    KONG_LICENSE_DATA: ${KONG_LICENSE_DATA:-}

kong-cp:
  <<: *default
  container_name: kong-cp
  image: {{ kong.image }}
  depends_on:
    kong-bootstrap:
      condition: service_completed_successfully
  ports:
    - {{ ports.admin }}:8001
    - {{ ports.manager }}:8002
  environment:
    <<: *kong-env
    KONG_ROLE: control_plane
    KONG_ADMIN_LISTEN: 0.0.0.0:8001
    KONG_ADMIN_GUI_LISTEN: 0.0.0.0:8002
    KONG_CLUSTER_LISTEN: 0.0.0.0:8005
    KONG_TELEMETRY_LISTEN: 0.0.0.0:8006
    KONG_ADMIN_GUI_HOST: http://localhost:{{ ports.manager }}
    KONG_CLUSTER_CERT: /etc/kong/certs/tls.crt
    KONG_CLUSTER_CERT_KEY: /etc/kong/certs/tls.key
    KONG_UNTRUSTED_LUA: "on"
    KONG_LICENSE_DATA: ${KONG_LICENSE_DATA:-}
{% if otel.enabled %}
    KONG_TRACING_INSTRUMENTATIONS: all
    KONG_TRACING_SAMPLING_RATE: "1.0"
{% endif %}
  volumes:
    - ./config/kong/certs:/etc/kong/certs

kong-dp:
  <<: *default
  container_name: kong-dp
  image: {{ kong.image }}
  depends_on:
    kong-bootstrap:
      condition: service_completed_successfully
  ports:
    - {{ ports.proxy }}:8000
    - {{ ports.status }}:8100
  environment:
    KONG_ROLE: data_plane
    KONG_DATABASE: "off"
    KONG_CLUSTER_MTLS: shared
    KONG_CLUSTER_CONTROL_PLANE: kong-cp:8005
    KONG_CLUSTER_SERVER_NAME: kong-cluster
    KONG_CLUSTER_TELEMETRY_ENDPOINT: kong-cp:8006
    KONG_CLUSTER_TELEMETRY_SERVER_NAME: kong-cp
    KONG_CLUSTER_CERT: /etc/kong/certs/tls.crt
    KONG_CLUSTER_CERT_KEY: /etc/kong/certs/tls.key
    KONG_STATUS_LISTEN: 0.0.0.0:8100
    KONG_UNTRUSTED_LUA: "on"
    KONG_LOG_LEVEL: debug
    KONG_LICENSE_DATA: ${KONG_LICENSE_DATA:-}
    KONG_TRUSTED_IPS: 0.0.0.0/0
    KONG_REAL_IP_HEADER: X-Forwarded-For
    KONG_REAL_IP_RECURSIVE: "on"
    KONG_PROXY_ACCESS_LOG: /dev/stdout
    KONG_PROXY_ERROR_LOG: /dev/stderr
{% if otel.enabled %}
    KONG_TRACING_INSTRUMENTATIONS: all
    KONG_TRACING_SAMPLING_RATE: "1.0"
{% endif %}
  volumes:
    - ./config/kong/certs:/etc/kong/certs
```

- [ ] **Step 8: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_render_compose.py tests/test_certs.py -v`
Expected: 全件 PASS

- [ ] **Step 9: コミット**

```bash
git add generator/templates/services/kong-aigw-v2.yaml.j2 generator/templates/services/kong-dp.yaml.j2 generator/templates/services/kong-self-managed.yaml.j2 generator/certs.py tests/test_certs.py tests/test_render_compose.py
git commit -m "feat(templates): Kong サービス 3 種と on/off のクォート

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Keycloak と realm-export.json

**Files:**
- Create: `generator/templates/services/keycloak.yaml.j2`
- Create: `generator/templates/config/realm-export.json.j2`
- Modify: `generator/render.py`（realm-export.json を出力に追加）
- Modify: `tests/test_render_compose.py`（テストを追記）
- Create: `tests/test_realm.py`

**Interfaces:**
- Consumes: `context.IdpCtx`、`ctx.env_vars["KEYCLOAK_CLIENT_SECRET"]`
- Produces: compose の `keycloak` サービス、`render_all` の戻り値に `config/keycloak/realm-export.json` を追加（`idp.type` が keycloak のときのみ）

- [ ] **Step 1: 失敗テストを書く**

`tests/test_render_compose.py` の末尾に追記:

```python
def test_keycloak_service(compose_of):
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = compose_of(only_services(ctx, "keycloak"))
    svc = doc["services"]["keycloak"]
    assert svc["image"] == "quay.io/keycloak/keycloak:26.6.1"
    assert svc["command"] == ["start-dev", "--import-realm"]
    assert svc["ports"] == ["8080:8080"]


def test_keycloak_hostname_matches_issuer(compose_of):
    # KC_HOSTNAME とブラウザから見える URL がずれると issuer 検証が落ちる
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = compose_of(only_services(ctx, "keycloak"))
    assert doc["services"]["keycloak"]["environment"]["KC_HOSTNAME"] == "http://localhost:8080"
    assert ctx.idp.issuer == "http://keycloak:8080/realms/acme"


def test_keycloak_mounts_realm_export(compose_of):
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = compose_of(only_services(ctx, "keycloak"))
    assert doc["services"]["keycloak"]["volumes"] == [
        "./config/keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro"
    ]
```

`tests/test_realm.py`:

```python
import json

from generator.render import render_all
from tests.conftest import ctx_for


def realm_of(ctx) -> dict:
    return json.loads(render_all(ctx)["config/keycloak/realm-export.json"])


def test_realm_is_emitted_only_for_keycloak():
    assert "config/keycloak/realm-export.json" in render_all(
        ctx_for(idp={"type": "keycloak", "realm": "acme"})
    )
    assert "config/keycloak/realm-export.json" not in render_all(ctx_for())
    assert "config/keycloak/realm-export.json" not in render_all(
        ctx_for(idp={"type": "entra-id"})
    )


def test_realm_name_and_enabled():
    realm = realm_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}))
    assert realm["realm"] == "acme"
    assert realm["enabled"] is True


def test_confidential_client_uses_env_secret():
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    realm = realm_of(ctx)
    confidential = next(c for c in realm["clients"] if c["clientId"] == "acme-client")
    assert confidential["publicClient"] is False
    assert confidential["secret"] == ctx.env_vars["KEYCLOAK_CLIENT_SECRET"]
    assert confidential["serviceAccountsEnabled"] is True
    assert confidential["standardFlowEnabled"] is True


def test_public_client_for_browser_flows():
    realm = realm_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}))
    public = next(c for c in realm["clients"] if c["clientId"] == "acme-public")
    assert public["publicClient"] is True
    assert "http://localhost:8000/*" in public["redirectUris"]


def test_test_user_is_present_with_password():
    realm = realm_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}))
    user = next(u for u in realm["users"] if u["username"] == "tester")
    assert user["enabled"] is True
    assert user["credentials"][0]["value"] == "tester"
    assert user["credentials"][0]["temporary"] is False


def test_client_scope_is_declared():
    realm = realm_of(ctx_for(idp={"type": "keycloak", "realm": "acme"}))
    assert "acme-scope" in [s["name"] for s in realm["clientScopes"]]
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_realm.py -v`
Expected: 全件 FAIL。`KeyError: 'config/keycloak/realm-export.json'`

- [ ] **Step 3: Keycloak パーシャルを書く**

`generator/templates/services/keycloak.yaml.j2`:

```jinja
keycloak:
  <<: *default
  container_name: keycloak
  image: quay.io/keycloak/keycloak:26.6.1
  command: ["start-dev", "--import-realm"]
  environment:
    KC_BOOTSTRAP_ADMIN_USERNAME: ${KEYCLOAK_ADMIN:-admin}
    KC_BOOTSTRAP_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD:-admin}
    KC_HOSTNAME: http://localhost:{{ ports.keycloak }}
  ports:
    - {{ ports.keycloak }}:8080
  volumes:
    - ./config/keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro
```

- [ ] **Step 4: realm-export のテンプレートを書く**

`generator/templates/config/realm-export.json.j2`:

```jinja
{
  "realm": "{{ idp.realm }}",
  "enabled": true,
  "sslRequired": "none",
  "registrationAllowed": false,
  "clientScopes": [
    {
      "name": "{{ spec.customer }}-scope",
      "protocol": "openid-connect",
      "attributes": {
        "include.in.token.scope": "true",
        "display.on.consent.screen": "false"
      }
    }
  ],
  "clients": [
    {
      "clientId": "{{ idp.client_id }}",
      "enabled": true,
      "protocol": "openid-connect",
      "publicClient": false,
      "secret": "{{ env_vars['KEYCLOAK_CLIENT_SECRET'] }}",
      "standardFlowEnabled": true,
      "directAccessGrantsEnabled": true,
      "serviceAccountsEnabled": true,
      "redirectUris": ["http://localhost:{{ ports.proxy }}/*"],
      "webOrigins": ["*"],
      "defaultClientScopes": ["openid", "profile", "email", "{{ spec.customer }}-scope"]
    },
    {
      "clientId": "{{ spec.customer }}-public",
      "enabled": true,
      "protocol": "openid-connect",
      "publicClient": true,
      "standardFlowEnabled": true,
      "directAccessGrantsEnabled": true,
      "redirectUris": ["http://localhost:{{ ports.proxy }}/*"],
      "webOrigins": ["*"],
      "defaultClientScopes": ["openid", "profile", "email", "{{ spec.customer }}-scope"]
    }
  ],
  "users": [
    {
      "username": "tester",
      "enabled": true,
      "emailVerified": true,
      "email": "tester@example.com",
      "firstName": "Test",
      "lastName": "User",
      "credentials": [
        {
          "type": "password",
          "value": "tester",
          "temporary": false
        }
      ],
      "realmRoles": ["default-roles-{{ idp.realm }}"]
    }
  ]
}
```

- [ ] **Step 5: render.py に realm-export の出力を追加する**

`generator/render.py` の `render_all` を差し替える:

```python
def render_all(ctx: Ctx) -> dict[str, str]:
    env = build_env()
    variables = template_vars(ctx)
    files = {
        "compose.yaml": env.get_template("compose.yaml.j2").render(**variables),
    }
    if ctx.vector.enabled and ctx.vector.type.value == "pgvector":
        files["config/pgvector/init.sql"] = "CREATE EXTENSION IF NOT EXISTS vector;\n"
    if ctx.idp.type.value == "keycloak":
        files["config/keycloak/realm-export.json"] = env.get_template(
            "config/realm-export.json.j2"
        ).render(**variables)
    return files
```

- [ ] **Step 6: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_realm.py tests/test_render_compose.py -v`
Expected: 全件 PASS

- [ ] **Step 7: コミット**

```bash
git add generator/templates/services/keycloak.yaml.j2 generator/templates/config/realm-export.json.j2 generator/render.py tests/test_realm.py tests/test_render_compose.py
git commit -m "feat(templates): Keycloak サービスと realm-export

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: AI Gateway v2 のエンティティ定義（kongctl.yaml）

**Files:**
- Create: `generator/templates/config/kongctl.yaml.j2`
- Modify: `generator/render.py`
- Modify: `tests/conftest.py`（kongctl のカスタムタグを読むローダを追加）
- Create: `tests/test_kongctl.py`

**Interfaces:**
- Consumes: `context.Ctx`、`spec.ai.providers`、`context.VectorCtx`
- Produces: `render_all` の戻り値に `config/kongctl.yaml` を追加（`gateway` が `ai-gateway-v2` のときのみ）。テストヘルパ `tests/conftest.py` の `load_kongctl(text) -> dict`

**注意:** kongctl の YAML は `!file` / `!env` / `!ref` / `!secret` というカスタムタグを使う。PyYAML の `safe_load` はこれらを知らず `could not determine a constructor for the tag` で落ちるため、テストでは専用のローダを用意する。

- [ ] **Step 1: conftest.py にカスタムタグ対応のローダを追加する**

`tests/conftest.py` の末尾に追記:

```python
class KongctlLoader(yaml.SafeLoader):
    """kongctl のカスタムタグを値として保持したまま読むためのローダ。"""


def _keep_tag(loader, node):
    return {"__tag__": node.tag, "value": loader.construct_scalar(node)}


for _tag in ("!file", "!env", "!ref", "!secret"):
    KongctlLoader.add_constructor(_tag, _keep_tag)


def load_kongctl(text: str) -> dict:
    return yaml.load(text, Loader=KongctlLoader)
```

- [ ] **Step 2: 失敗テストを書く**

`tests/test_kongctl.py`:

```python
import pytest

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for, load_kongctl

SEMANTIC = {
    "customer": "acme",
    "gateway": "ai-gateway-v2",
    "cache": {"type": "redis-stack"},
    "vectordb": {"type": "redis-stack"},
    "ai": {"providers": [AZURE_PROVIDER], "semantic_cache": True},
}


def kongctl_of(ctx) -> dict:
    return load_kongctl(render_all(ctx)["config/kongctl.yaml"])


def test_emitted_only_for_ai_gateway_v2():
    assert "config/kongctl.yaml" in render_all(
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    )
    assert "config/kongctl.yaml" not in render_all(ctx_for())
    assert "config/kongctl.yaml" not in render_all(
        ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    )


def test_namespace_is_customer():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert doc["_defaults"]["kongctl"]["namespace"] == "acme"


def test_ai_gateway_name_matches_cp_name():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    doc = kongctl_of(ctx)
    gw = doc["ai_gateways"][0]
    assert gw["name"] == ctx.kong.cp_name == "acme-ai-gateway"


def test_azure_provider_uses_api_key_from_env():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    provider = doc["ai_gateways"][0]["model_providers"][0]
    assert provider["type"] == "azure"
    assert provider["config"]["instance"] == "acme-foundry"
    header = provider["config"]["auth"]["headers"][0]
    assert header["name"] == "api-key"
    assert header["value"] == {"__tag__": "!env", "value": "AZURE_OPENAI_API_KEY"}


def test_azure_managed_identity_provider():
    provider_spec = {**AZURE_PROVIDER, "auth": "managed-identity"}
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [provider_spec]}))
    auth = doc["ai_gateways"][0]["model_providers"][0]["config"]["auth"]
    assert auth["type"] == "azure"
    assert auth["use_managed_identity"] is True


def test_model_route_path_is_derived_from_model_name():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    model = doc["ai_gateways"][0]["models"][0]
    assert model["name"] == "gpt-5-6"
    assert model["config"]["route"]["paths"] == ["/v1/gpt-5-6"]
    assert model["config"]["route"]["strip_path"] is True
    assert model["formats"] == [{"type": "openai"}]
    assert model["capabilities"] == ["generate"]


def test_model_target_carries_deployment_id_and_api_version():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    target = doc["ai_gateways"][0]["models"][0]["targets"][0]
    assert target["provider"] == "azure"
    assert target["config"]["deployment_id"] == "gpt-5.6"
    assert target["config"]["api_version"] == "2024-12-01-preview"


def test_no_balancer_without_semantic_features():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    assert "balancer" not in doc["ai_gateways"][0]["models"][0]["config"]


def test_semantic_balancer_wires_vectordb():
    doc = kongctl_of(ctx_for(**{k: v for k, v in SEMANTIC.items() if k != "customer"}))
    balancer = doc["ai_gateways"][0]["models"][0]["config"]["balancer"]
    assert balancer["algorithm"] == "semantic"
    assert balancer["embeddings"]["name"] == "text-embedding-3-large"
    vectordb = balancer["vectordb"]
    assert vectordb["type"] == "redis"
    assert vectordb["host"] == "redis"
    assert vectordb["port"] == 6379
    assert vectordb["dimensions"] == 3072
    assert vectordb["distance_metric"] == "cosine"


def test_semantic_balancer_with_pgvector():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        vectordb={"type": "pgvector"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
    )
    vectordb = kongctl_of(ctx)["ai_gateways"][0]["models"][0]["config"]["balancer"]["vectordb"]
    assert vectordb["type"] == "pgvector"
    assert vectordb["host"] == "pgvector"
    assert vectordb["port"] == 5432


def test_data_plane_certificate_references_generated_file():
    doc = kongctl_of(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))
    cert = doc["ai_gateways"][0]["data_plane_certificates"][0]
    assert cert["cert"] == {"__tag__": "!file", "value": ".certs/cluster.crt"}


def test_multiple_providers_emit_multiple_model_providers():
    bedrock = {
        "type": "bedrock",
        "region": "us-east-1",
        "models": [{"name": "claude-opus-5"}],
    }
    doc = kongctl_of(
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER, bedrock]})
    )
    gw = doc["ai_gateways"][0]
    assert [p["type"] for p in gw["model_providers"]] == ["azure", "bedrock"]
    assert [m["name"] for m in gw["models"]] == ["gpt-5-6", "claude-opus-5"]
```

- [ ] **Step 3: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_kongctl.py -v`
Expected: 全件 FAIL。`KeyError: 'config/kongctl.yaml'`

- [ ] **Step 4: kongctl.yaml.j2 を書く**

`generator/templates/config/kongctl.yaml.j2`:

```jinja
_defaults:
  kongctl:
    namespace: {{ namespace }}

ai_gateways:
  - ref: {{ kong.cp_name }}
    name: {{ kong.cp_name }}
    display_name: {{ kong.cp_name }}
    deployment_type: hybrid

    model_providers:
{% for provider in spec.ai.providers %}
      - ref: {{ provider.type.value }}
        ai_gateway: !ref {{ kong.cp_name }}#id
        name: {{ provider.type.value }}
        type: {{ provider.type.value }}
        display_name: {{ provider.type.value }}
        config:
{% if provider.instance %}
          instance: {{ provider.instance }}
{% endif %}
{% if provider.region %}
          region: {{ provider.region }}
{% endif %}
{% if provider.type.value == 'azure' and provider.auth.value == 'managed-identity' %}
          auth:
            type: azure
            use_managed_identity: true
            client_id: !env AZURE_CLIENT_ID
            tenant_id: !env AZURE_TENANT_ID
{% elif provider.type.value == 'azure' %}
          auth:
            type: basic
            headers:
              - name: api-key
                value: !env AZURE_OPENAI_API_KEY
{% elif provider.type.value == 'openai' %}
          auth:
            type: bearer
            token: !env OPENAI_API_KEY
{% elif provider.type.value == 'anthropic' %}
          auth:
            type: basic
            headers:
              - name: x-api-key
                value: !env ANTHROPIC_API_KEY
{% else %}
          auth:
            type: {{ provider.type.value }}
{% endif %}
{% endfor %}

    models:
{% for provider in spec.ai.providers %}
{% for model in provider.models %}
      - ref: {{ model.name }}
        ai_gateway: !ref {{ kong.cp_name }}#id
        name: {{ model.name }}
        display_name: {{ model.name }}
        type: model
        enabled: true
        capabilities:
          - generate
        formats:
          - type: openai
        targets:
          - name: {{ model.name }}
            provider: {{ provider.type.value }}
            weight: 100
            config:
              type: {{ provider.type.value }}
{% if model.deployment_id %}
              deployment_id: {{ model.deployment_id }}
{% endif %}
{% if model.api_version %}
              api_version: {{ model.api_version }}
{% endif %}
        config:
          model:
            name_header: true
          response_streaming: allow
{% if vector.enabled %}
          balancer:
            algorithm: semantic
            embeddings:
              name: {{ spec.ai.embedding_model }}
              provider: {{ provider.type.value }}
              allow_auth_override: false
              config:
                type: {{ provider.type.value }}
            vectordb:
              type: {{ 'redis' if vector.type.value == 'redis-stack' else 'pgvector' }}
              host: {{ vector.host }}
              port: {{ vector.port }}
              dimensions: {{ vector.dimensions }}
              threshold: {{ vector.threshold }}
              distance_metric: {{ vector.distance_metric }}
{% endif %}
          route:
            paths:
              - /v1/{{ model.name }}
            protocols:
              - http
              - https
            strip_path: true
{% endfor %}
{% endfor %}

    data_plane_certificates:
      - ref: {{ kong.cp_name }}-cert
        ai_gateway: !ref {{ kong.cp_name }}#id
        title: Cluster Certificate
        description: Cluster certificate for {{ kong.cp_name }}
        cert: !file .certs/cluster.crt
```

- [ ] **Step 5: render.py に kongctl.yaml の出力を追加する**

`generator/render.py` の `render_all` の `files` 構築部分に追記する（`return files` の直前）:

```python
    if ctx.spec.gateway.value == "ai-gateway-v2":
        files["config/kongctl.yaml"] = env.get_template("config/kongctl.yaml.j2").render(
            **variables
        )
```

- [ ] **Step 6: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_kongctl.py -v`
Expected: 全件 PASS

- [ ] **Step 7: コミット**

```bash
git add generator/templates/config/kongctl.yaml.j2 generator/render.py tests/conftest.py tests/test_kongctl.py
git commit -m "feat(templates): AI Gateway v2 のエンティティ定義

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: decK 宣言的設定と httpbin

**Files:**
- Create: `generator/templates/config/kong.yaml.j2`
- Create: `generator/templates/services/httpbin.yaml.j2`
- Modify: `generator/render.py`
- Modify: `tests/test_render_compose.py`
- Create: `tests/test_kong_yaml.py`

**Interfaces:**
- Consumes: `context.Ctx`、`context.UpstreamCtx`、`context.IdpCtx`、`context.OtelCtx`
- Produces: compose の `httpbin` サービス、`render_all` の戻り値に `config/kong/kong.yaml` を追加（`gateway` が `ai-gateway-v1` または `api-gateway` のとき）

- [ ] **Step 1: 失敗テストを書く**

`tests/test_render_compose.py` の末尾に追記:

```python
def test_httpbin_service(compose_of):
    doc = compose_of(only_services(ctx_for(), "httpbin"))
    svc = doc["services"]["httpbin"]
    assert svc["image"] == "kennethreitz/httpbin"
    assert svc["ports"] == ["8081:80"]
```

`tests/test_kong_yaml.py`:

```python
import yaml

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for


def kong_yaml_of(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)["config/kong/kong.yaml"])


def test_emitted_for_api_gateway_and_aigw_v1_only():
    assert "config/kong/kong.yaml" in render_all(ctx_for())
    assert "config/kong/kong.yaml" in render_all(
        ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    )
    assert "config/kong/kong.yaml" not in render_all(
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    )


def test_format_version():
    assert kong_yaml_of(ctx_for())["_format_version"] == "3.0"


def test_api_gateway_routes_to_httpbin():
    doc = kong_yaml_of(ctx_for())
    service = doc["services"][0]
    assert service["name"] == "httpbin"
    assert service["url"] == "http://httpbin:80"
    assert service["routes"][0]["paths"] == ["/httpbin"]
    assert service["routes"][0]["strip_path"] is True


def test_api_gateway_without_upstream_has_no_services():
    doc = kong_yaml_of(ctx_for(upstream="none"))
    assert doc.get("services", []) == []


def test_openid_connect_plugin_uses_resolved_issuer():
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    doc = kong_yaml_of(ctx)
    plugin = next(p for p in doc["services"][0]["plugins"] if p["name"] == "openid-connect")
    assert plugin["config"]["issuer"] == "http://keycloak:8080/realms/acme"
    assert plugin["config"]["client_id"] == ["acme-client"]
    assert plugin["config"]["client_secret"] == ["${KEYCLOAK_CLIENT_SECRET}"]


def test_no_openid_connect_when_idp_none():
    doc = kong_yaml_of(ctx_for())
    assert all(p["name"] != "openid-connect" for p in doc["services"][0].get("plugins", []))


def test_opentelemetry_plugin_is_global_when_otel_enabled():
    doc = kong_yaml_of(ctx_for())
    plugin = next(p for p in doc["plugins"] if p["name"] == "opentelemetry")
    assert plugin["config"]["traces_endpoint"] == "http://otel-lgtm:4318/v1/traces"
    assert plugin["config"]["logs_endpoint"] == "http://otel-lgtm:4318/v1/logs"


def test_no_global_plugins_when_otel_disabled():
    doc = kong_yaml_of(ctx_for(observability={"otel_lgtm": False}))
    assert doc.get("plugins", []) == []


def test_aigw_v1_uses_ai_proxy_advanced():
    ctx = ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    doc = kong_yaml_of(ctx)
    service = doc["services"][0]
    assert service["name"] == "llm"
    plugin = next(p for p in service["plugins"] if p["name"] == "ai-proxy-advanced")
    target = plugin["config"]["targets"][0]
    assert target["model"]["provider"] == "azure"
    assert target["model"]["name"] == "gpt-5-6"
    assert target["model"]["options"]["azure_deployment_id"] == "gpt-5.6"
    assert target["route_type"] == "llm/v1/chat"


def test_aigw_v1_semantic_cache_plugin_points_at_vectordb():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
    )
    doc = kong_yaml_of(ctx)
    plugin = next(
        p for p in doc["services"][0]["plugins"] if p["name"] == "ai-semantic-cache"
    )
    assert plugin["config"]["vectordb"]["strategy"] == "redis"
    assert plugin["config"]["vectordb"]["redis"]["host"] == "redis"
    assert plugin["config"]["vectordb"]["dimensions"] == 3072


def test_aigw_v1_without_semantic_cache_has_no_cache_plugin():
    ctx = ctx_for(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]})
    doc = kong_yaml_of(ctx)
    assert all(
        p["name"] != "ai-semantic-cache" for p in doc["services"][0].get("plugins", [])
    )
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_kong_yaml.py -v`
Expected: 全件 FAIL。`KeyError: 'config/kong/kong.yaml'`

- [ ] **Step 3: httpbin パーシャルを書く**

`generator/templates/services/httpbin.yaml.j2`:

```jinja
httpbin:
  <<: *default
  container_name: httpbin
  image: kennethreitz/httpbin
  ports:
    - {{ ports.httpbin }}:80
```

- [ ] **Step 4: kong.yaml.j2 を書く**

`generator/templates/config/kong.yaml.j2`:

```jinja
_format_version: "3.0"

services:
{% if spec.gateway.value == 'ai-gateway-v1' %}
  - name: llm
    # ai-proxy-advanced が上流を差し替えるが、service には URL が必須なので捨て値を置く
    url: http://localhost:32000
    routes:
      - name: chat
        paths:
          - /v1/chat/completions
        methods:
          - POST
        strip_path: false
    plugins:
      - name: ai-proxy-advanced
        config:
          targets:
{% for provider in spec.ai.providers %}
{% for model in provider.models %}
            - route_type: llm/v1/chat
              auth:
{% if provider.type.value == 'azure' %}
                header_name: api-key
                header_value: ${AZURE_OPENAI_API_KEY}
{% else %}
                header_name: Authorization
                header_value: Bearer ${LLM_API_KEY}
{% endif %}
              model:
                provider: {{ provider.type.value }}
                name: {{ model.name }}
                options:
{% if provider.instance %}
                  azure_instance: {{ provider.instance }}
{% endif %}
{% if model.deployment_id %}
                  azure_deployment_id: {{ model.deployment_id }}
{% endif %}
{% if model.api_version %}
                  azure_api_version: "{{ model.api_version }}"
{% endif %}
{% endfor %}
{% endfor %}
{% if vector.enabled %}
      - name: ai-semantic-cache
        config:
          embeddings:
            auth:
              header_name: api-key
              header_value: ${AZURE_OPENAI_API_KEY}
            model:
              provider: openai
              name: {{ spec.ai.embedding_model }}
          vectordb:
            strategy: {{ 'redis' if vector.type.value == 'redis-stack' else 'pgvector' }}
            dimensions: {{ vector.dimensions }}
            distance_metric: {{ vector.distance_metric }}
            threshold: {{ vector.threshold }}
{% if vector.type.value == 'redis-stack' %}
            redis:
              host: {{ vector.host }}
              port: {{ vector.port }}
{% else %}
            pgvector:
              host: {{ vector.host }}
              port: {{ vector.port }}
              database: {{ vector.database }}
              user: {{ vector.user }}
{% endif %}
{% endif %}
{% if idp.enabled %}
      - name: openid-connect
        config:
          issuer: {{ idp.issuer }}
          client_id:
            - {{ idp.client_id }}
          client_secret:
            - ${KEYCLOAK_CLIENT_SECRET}
          auth_methods:
            - bearer
            - client_credentials
{% endif %}
{% elif upstream.enabled %}
  - name: {{ upstream.service_name }}
    url: {{ upstream.url }}
    routes:
      - name: {{ upstream.service_name }}
        paths:
          - /{{ upstream.service_name }}
        strip_path: true
{% if idp.enabled %}
    plugins:
      - name: openid-connect
        config:
          issuer: {{ idp.issuer }}
          client_id:
            - {{ idp.client_id }}
          client_secret:
            - ${KEYCLOAK_CLIENT_SECRET}
          auth_methods:
            - bearer
            - client_credentials
{% endif %}
{% endif %}

plugins:
{% if otel.enabled %}
  - name: opentelemetry
    config:
      traces_endpoint: {{ otel.endpoint }}/v1/traces
      logs_endpoint: {{ otel.endpoint }}/v1/logs
      resource_attributes:
        service.name: {{ kong.cp_name }}
{% endif %}
```

空の `services:` / `plugins:` は YAML 上 `null` になり、`doc.get(..., [])` を使ったテストが落ちる。上のテンプレートの `services:` 行と `plugins:` 行は、それぞれ次のように出し分ける。

```jinja
{% if spec.gateway.value == 'ai-gateway-v1' or upstream.enabled %}
services:
{% else %}
services: []
{% endif %}
```

```jinja
{% if otel.enabled %}
plugins:
  - name: opentelemetry
    config:
      traces_endpoint: {{ otel.endpoint }}/v1/traces
      logs_endpoint: {{ otel.endpoint }}/v1/logs
      resource_attributes:
        service.name: {{ kong.cp_name }}
{% else %}
plugins: []
{% endif %}
```

- [ ] **Step 5: render.py に kong.yaml の出力を追加する**

`generator/render.py` の `render_all` の `return files` の直前に追記:

```python
    if ctx.spec.gateway.value in ("ai-gateway-v1", "api-gateway"):
        files["config/kong/kong.yaml"] = env.get_template("config/kong.yaml.j2").render(
            **variables
        )
```

- [ ] **Step 6: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_kong_yaml.py tests/test_render_compose.py -v`
Expected: 全件 PASS

- [ ] **Step 7: コミット**

```bash
git add generator/templates/config/kong.yaml.j2 generator/templates/services/httpbin.yaml.j2 generator/render.py tests/test_kong_yaml.py tests/test_render_compose.py
git commit -m "feat(templates): decK 宣言的設定と httpbin

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: mise タスク、README、.env、.gitignore

**Files:**
- Create: `generator/templates/mise.toml.j2`
- Create: `generator/templates/README.md.j2`
- Create: `generator/templates/env.j2`
- Create: `generator/templates/gitignore.j2`
- Modify: `generator/render.py`
- Create: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: `context.Ctx` 全体
- Produces: `render_all` の戻り値に `mise.toml` / `README.md` / `.env` / `.env.example` / `.gitignore` / `docs/.gitkeep` を追加（全構成で必ず出す）

- [ ] **Step 1: 失敗テストを書く**

`tests/test_scaffold.py`:

```python
import tomllib

from generator.render import render_all
from tests.conftest import AZURE_PROVIDER, ctx_for


def test_all_configurations_emit_the_same_top_level_files():
    expected = {"compose.yaml", "mise.toml", "README.md", ".env", ".env.example", ".gitignore", "docs/.gitkeep"}
    for ctx in (
        ctx_for(),
        ctx_for(control_plane="self-managed"),
        ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}),
    ):
        assert expected <= set(render_all(ctx)), ctx.spec.gateway


def test_mise_is_valid_toml_with_expected_tasks():
    doc = tomllib.loads(render_all(ctx_for())["mise.toml"])
    tasks = {name.removeprefix("tasks.") for name in doc["tasks"]}
    assert tasks == {"up", "down", "reset", "certs", "sync", "diff", "logs", "smoke"}


def test_mise_loads_dotenv():
    doc = tomllib.loads(render_all(ctx_for())["mise.toml"])
    assert doc["env"]["mise"]["file"] == ".env"


def test_sync_uses_kongctl_for_konnect_ai_gateway_v2():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    doc = tomllib.loads(render_all(ctx)["mise.toml"])
    assert "kongctl sync konnect -f config/kongctl.yaml --auto-approve" in doc["tasks"]["sync"]["run"]


def test_sync_uses_deck_for_self_managed():
    doc = tomllib.loads(render_all(ctx_for(control_plane="self-managed"))["mise.toml"])
    assert "deck gateway sync" in doc["tasks"]["sync"]["run"]
    assert "config/kong/kong.yaml" in doc["tasks"]["sync"]["run"]


def test_smoke_targets_the_allocated_proxy_port():
    doc = tomllib.loads(render_all(ctx_for())["mise.toml"])
    assert "localhost:8000/httpbin/status/200" in doc["tasks"]["smoke"]["run"]


def test_certs_task_only_for_konnect():
    konnect = tomllib.loads(render_all(ctx_for())["mise.toml"])
    assert ".certs" in konnect["tasks"]["certs"]["run"]

    self_managed = tomllib.loads(render_all(ctx_for(control_plane="self-managed"))["mise.toml"])
    assert "config/kong/certs" in self_managed["tasks"]["certs"]["run"]


def test_env_file_lists_every_required_variable():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    body = render_all(ctx)[".env"]
    for key in ctx.env_vars:
        assert f'{key}="' in body, key


def test_env_example_has_every_key_with_empty_value():
    ctx = ctx_for(idp={"type": "keycloak", "realm": "acme"})
    body = render_all(ctx)[".env.example"]
    for key in ctx.env_vars:
        assert f'{key}=""' in body, key


def test_env_keeps_known_defaults_but_blanks_secrets():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}, idp={"type": "keycloak", "realm": "acme"})
    body = render_all(ctx)[".env"]
    assert 'KEYCLOAK_ADMIN="admin"' in body
    assert 'AZURE_OPENAI_API_KEY=""' in body


def test_gitignore_protects_secrets_and_certs():
    body = render_all(ctx_for())[".gitignore"]
    assert ".env" in body
    assert ".certs/" in body


def test_readme_mentions_resolved_values():
    ctx = ctx_for(
        gateway="ai-gateway-v2",
        region="eu",
        cache={"type": "redis-stack"},
        vectordb={"type": "redis-stack"},
        idp={"type": "keycloak", "realm": "acme"},
        ai={"providers": [AZURE_PROVIDER], "semantic_cache": True},
    )
    body = render_all(ctx)["README.md"]
    assert "acme-ai-gateway" in body
    assert "eu.cp.konghq.com" in body
    assert "mise run up" in body
    assert "http://localhost:3000" in body


def test_readme_troubleshooting_is_configuration_specific():
    konnect = render_all(ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]}))["README.md"]
    assert "クラスタ証明書" in konnect
    assert "kong migrations bootstrap" not in konnect

    self_managed = render_all(ctx_for(control_plane="self-managed"))["README.md"]
    assert "kong migrations bootstrap" in self_managed

    keycloak = render_all(ctx_for(idp={"type": "keycloak", "realm": "acme"}))["README.md"]
    assert "KC_HOSTNAME" in keycloak


def test_readme_has_no_hard_wrapped_paragraphs():
    # 段落は 1 行で書く方針。表とリストとコードブロック以外に途中改行を作らない
    body = render_all(ctx_for())["README.md"]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.endswith("、") or stripped.endswith("し、"):
            raise AssertionError(f"段落が途中で改行されています: {line}")
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_scaffold.py -v`
Expected: 全件 FAIL。`KeyError: 'mise.toml'`

- [ ] **Step 3: mise.toml.j2 を書く**

`generator/templates/mise.toml.j2`:

```jinja
[tasks.up]
description = "環境を起動する"
run = "docker compose up -d"

[tasks.down]
description = "環境を停止する"
run = "docker compose down"

[tasks.reset]
description = "ボリュームごと作り直す"
run = """
docker compose down -v
docker compose up -d
"""

[tasks.certs]
description = "クラスタ証明書を再生成する"
{% if spec.control_plane.value == 'konnect' %}
run = """
mkdir -p .certs
openssl req -new -x509 -nodes -newkey rsa:2048 \
  -subj "/CN={{ kong.cp_name }}/C=JP" \
  -keyout .certs/cluster.key -out .certs/cluster.crt -days 1095
chmod 600 .certs/cluster.key
echo "生成しました。Konnect の Control Plane に .certs/cluster.crt を登録してください。"
"""
{% else %}
run = """
mkdir -p config/kong/certs
openssl req -new -x509 -nodes -newkey rsa:2048 \
  -subj "/CN=kong-cluster/C=JP" \
  -keyout config/kong/certs/tls.key -out config/kong/certs/tls.crt -days 1095
chmod 600 config/kong/certs/tls.key
"""
{% endif %}

[tasks.sync]
description = "宣言的設定を適用する"
{% if spec.gateway.value == 'ai-gateway-v2' %}
run = "kongctl sync konnect -f config/kongctl.yaml --auto-approve"
{% elif spec.control_plane.value == 'konnect' %}
run = "kongctl sync konnect -f config/kong/kong.yaml --auto-approve"
{% else %}
run = "deck gateway sync config/kong/kong.yaml --kong-addr http://localhost:{{ ports.admin }}"
{% endif %}

[tasks.diff]
description = "宣言的設定の差分を見る"
{% if spec.gateway.value == 'ai-gateway-v2' %}
run = "kongctl diff konnect -f config/kongctl.yaml"
{% elif spec.control_plane.value == 'konnect' %}
run = "kongctl diff konnect -f config/kong/kong.yaml"
{% else %}
run = "deck gateway diff config/kong/kong.yaml --kong-addr http://localhost:{{ ports.admin }}"
{% endif %}

[tasks.logs]
description = "ログを追う。サービス名を引数で渡せる"
run = "docker compose logs -f {{ '{{arg(name=\"service\", default=\"\")}}' }}"

[tasks.smoke]
description = "主要経路を叩いて疎通を確認する"
run = """
set -e
{% if spec.gateway.value == 'ai-gateway-v2' %}
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:{{ ports.status }}/status
{% for provider in spec.ai.providers %}
{% for model in provider.models %}
curl -sS -f -X POST http://localhost:{{ ports.proxy }}/v1/{{ model.name }}/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"ping"}]}' > /dev/null
{% endfor %}
{% endfor %}
{% elif upstream.enabled %}
curl -sS -f http://localhost:{{ ports.proxy }}/httpbin/status/200 > /dev/null
{% else %}
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:{{ ports.status }}/status
{% endif %}
echo "smoke: OK"
"""

[env]
mise.file = ".env"
```

- [ ] **Step 4: env.j2 と gitignore.j2 を書く**

`generator/templates/env.j2`:

```jinja
{% for key, value in env_vars.items() %}
{{ key }}="{{ '' if blank else value }}"
{% endfor %}
```

`generator/templates/gitignore.j2`:

```jinja
.env
.certs/
config/kong/certs/
.venv/
__pycache__/
```

- [ ] **Step 5: README.md.j2 を書く**

`generator/templates/README.md.j2`:

```jinja
# {{ spec.customer }} 検証環境

`env.yaml` から生成された環境です。構成を変えるときは `env.yaml` を編集して再生成してください。

## 構成

| 項目 | 値 |
|---|---|
| Gateway | {{ spec.gateway.value }} |
| Control Plane | {{ spec.control_plane.value }}{% if spec.control_plane.value == 'konnect' %}（{{ kong.cp_name }} / {{ kong.konnect_domain }}）{% endif %} |
| IdP | {{ spec.idp.type.value }}{% if idp.realm %}（realm: {{ idp.realm }}）{% endif %} |
| キャッシュ | {{ spec.cache.type.value }} |
| ベクトル DB | {{ spec.vectordb.type.value }} |
| Observability | {{ 'otel-lgtm' if otel.enabled else 'なし' }} |

## 前提

必要なツールは docker、mise{% if spec.gateway.value == 'ai-gateway-v2' or spec.control_plane.value == 'konnect' %}、kongctl{% else %}、deck{% endif %} です。

`.env` の次の項目を埋めてから起動してください。

{% for key, value in env_vars.items() %}
{% if not value %}
- `{{ key }}`
{% endif %}
{% endfor %}

## 初手

{% if spec.control_plane.value == 'konnect' %}
```bash
mise run certs                      # .certs/cluster.{crt,key} を生成
# Konnect で {{ kong.cp_name }} を作成し、.certs/cluster.crt を登録する
# 発行された Control Plane ID を .env の CONTROL_PLANE_ID に書く
mise run up
mise run sync
mise run smoke
```
{% else %}
```bash
mise run certs                      # config/kong/certs/tls.{crt,key} を生成
# .env の KONG_LICENSE_DATA に Kong Enterprise のライセンス JSON を 1 行で入れる
mise run up
mise run sync
mise run smoke
```
{% endif %}

## エンドポイント

| 用途 | URL |
|---|---|
| Proxy | http://localhost:{{ ports.proxy }} |
| Status API | http://localhost:{{ ports.status }}/status |
{% if 'admin' in ports %}
| Admin API | http://localhost:{{ ports.admin }} |
| Kong Manager | http://localhost:{{ ports.manager }} |
{% endif %}
{% if otel.enabled %}
| Grafana | http://localhost:{{ ports.grafana }} |
{% endif %}
{% if idp.service_name %}
| Keycloak | http://localhost:{{ ports.keycloak }} |
{% endif %}
{% if 'cache' in ports %}
| Redis | localhost:{{ ports.cache }} |
{% endif %}
{% if 'vector_pg' in ports %}
| pgvector | localhost:{{ ports.vector_pg }} |
{% endif %}
{% if upstream.enabled %}
| httpbin（直接） | http://localhost:{{ ports.httpbin }} |
{% endif %}

## トラブルシュート

{% if spec.control_plane.value == 'konnect' %}
**データプレーンが Konnect に繋がらない。** `.certs/cluster.crt` を Konnect の Control Plane に登録し忘れている場合がほとんどです。証明書を再生成したときは登録もやり直す必要があります。

**`CONTROL_PLANE_ID` が空のまま起動した。** `KONG_CLUSTER_CONTROL_PLANE` が `.{{ kong.konnect_domain }}:443` という形になり、名前解決に失敗します。`.env` を埋めて `mise run reset` してください。
{% else %}
**CP が起動直後に落ちる。** `kong migrations bootstrap` が終わる前に CP が起動するとマイグレーション未適用で落ちます。`depends_on` の `service_completed_successfully` で順序は保証していますが、`docker compose up` を個別サービス指定で叩いたときは順序が崩れます。

**ライセンスエラーが出る。** `.env` の `KONG_LICENSE_DATA` に Kong Enterprise のライセンス JSON を 1 行で入れてください。改行が入っていると読めません。
{% endif %}
{% if idp.service_name %}

**トークン検証が issuer 不一致で落ちる。** `KC_HOSTNAME` は `http://localhost:{{ ports.keycloak }}` ですが、Kong がコンテナ内から見る issuer は `{{ idp.issuer }}` です。ブラウザから取得したトークンを Kong に渡すときに issuer がずれるため、テストは `direct access grants`（パスワードグラント）でコンテナ内の URL から取得するのが確実です。
{% endif %}
{% if vector.enabled and vector.type.value == 'redis-stack' %}

**ベクトル検索が動かない。** `redis:8` ではなく `redis/redis-stack` を使っているか確認してください。素の Redis には検索モジュールが入っていません。
{% endif %}
```

- [ ] **Step 6: render.py に足りない出力を追加する**

`generator/render.py` の `render_all` の `files` 初期化を差し替える:

```python
    files = {
        "compose.yaml": env.get_template("compose.yaml.j2").render(**variables),
        "mise.toml": env.get_template("mise.toml.j2").render(**variables),
        "README.md": env.get_template("README.md.j2").render(**variables),
        ".env": env.get_template("env.j2").render(blank=False, **variables),
        ".env.example": env.get_template("env.j2").render(blank=True, **variables),
        ".gitignore": env.get_template("gitignore.j2").render(**variables),
        "docs/.gitkeep": "",
    }
```

- [ ] **Step 7: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_scaffold.py -v`
Expected: 全件 PASS

- [ ] **Step 8: コミット**

```bash
git add generator/templates/mise.toml.j2 generator/templates/README.md.j2 generator/templates/env.j2 generator/templates/gitignore.j2 generator/render.py tests/test_scaffold.py
git commit -m "feat(templates): mise タスク、README、.env、.gitignore

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: CLI と --force 保護

**Files:**
- Create: `generator/gen.py`
- Create: `tests/test_writer.py`

**Interfaces:**
- Consumes: `schema.load_spec`、`context.build_context`、`render.render_all`、`certs.generate_cluster_cert`
- Produces: `gen.main`（click コマンド）、`gen.WriteResult`（`created` / `overwritten` / `skipped` の 3 リスト）、`gen.write_files(out: Path, files: dict[str, str], force: bool) -> WriteResult`、`gen.check_git_clean(out: Path) -> None`、`gen.DirtyWorktreeError`、`gen.TargetNotEmptyError`、`gen.PROTECTED_PATHS`

- [ ] **Step 1: 失敗テストを書く**

`tests/test_writer.py`:

```python
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
    assert (out / "config/kong/certs/tls.crt").exists()
    assert not (out / ".certs").exists()


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
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_writer.py -v`
Expected: 全件 FAIL。`ModuleNotFoundError: No module named 'generator.gen'`

- [ ] **Step 3: gen.py を実装する**

`generator/gen.py`:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.9", "jinja2>=3.1", "pyyaml>=6.0", "click>=8.1"]
# ///
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import click
from pydantic import ValidationError

from generator.certs import generate_cluster_cert
from generator.context import build_context
from generator.render import render_all
from generator.schema import load_spec

# --force でも決して上書きしない。API キー、Konnect 登録済みの証明書、顧客向け成果物
PROTECTED_PATHS = (".env", ".certs", "docs")


class TargetNotEmptyError(Exception):
    pass


class DirtyWorktreeError(Exception):
    pass


@dataclass
class WriteResult:
    created: list[Path] = field(default_factory=list)
    overwritten: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)


def _is_protected(relpath: str) -> bool:
    head = Path(relpath).parts[0]
    return head in PROTECTED_PATHS


def _is_inside_protected_dir(relpath: str) -> bool:
    parts = Path(relpath).parts
    return len(parts) > 1 and parts[0] in PROTECTED_PATHS


def check_git_clean(out: Path) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=out,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return  # git 管理下でなければ何も言わない
    if result.stdout.strip():
        raise DirtyWorktreeError(
            f"{out} に未コミットの変更があります。"
            "--force は生成物を上書きするため、先に commit か stash をしてください。"
        )


def write_files(out: Path, files: dict[str, str], force: bool) -> WriteResult:
    out = Path(out)
    if out.exists() and any(out.iterdir()) and not force:
        raise TargetNotEmptyError(
            f"{out} は空ではありません。既存の環境を更新する場合は --force を付けてください。"
        )

    result = WriteResult()
    for relpath, body in sorted(files.items()):
        target = out / relpath
        if target.exists():
            if _is_protected(relpath):
                result.skipped.append(target)
                continue
            result.overwritten.append(target)
        elif _is_inside_protected_dir(relpath) and target.parent.exists() and any(
            target.parent.iterdir()
        ):
            # docs/ に人が書いたものがある状態で .gitkeep だけ足すのは無意味
            result.skipped.append(target)
            continue
        else:
            result.created.append(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return result


@click.command()
@click.argument("env_yaml", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--out", required=True, type=click.Path(path_type=Path), help="生成先")
@click.option("--force", is_flag=True, help="既存の生成物を上書きする")
def main(env_yaml: Path, out: Path, force: bool) -> None:
    try:
        spec = load_spec(env_yaml)
    except ValidationError as e:
        for error in e.errors():
            click.echo(click.style(str(error["msg"]).removeprefix("Value error, "), fg="red"), err=False)
        sys.exit(1)

    for warning in spec.warnings():
        click.echo(click.style(f"警告: {warning}", fg="yellow"))

    ctx = build_context(spec)

    if force:
        check_git_clean(out)

    out.mkdir(parents=True, exist_ok=True)
    result = write_files(out, render_all(ctx), force=force)

    # 入力そのものを残すことで、後から何を選んだかを compose から逆算しなくて済む
    shutil.copyfile(env_yaml, out / "env.yaml")

    if spec.control_plane.value == "konnect":
        if not (out / ".certs" / "cluster.crt").exists():
            generate_cluster_cert(out / ".certs", common_name=ctx.kong.cp_name)
            result.created.append(out / ".certs" / "cluster.crt")
    else:
        cert_dir = out / "config" / "kong" / "certs"
        if not (cert_dir / "tls.crt").exists():
            generate_cluster_cert(cert_dir, common_name="kong-cluster", basename="tls")
            result.created.append(cert_dir / "tls.crt")

    for label, paths, color in (
        ("作成", result.created, "green"),
        ("上書き", result.overwritten, "yellow"),
        ("保護してスキップ", result.skipped, "cyan"),
    ):
        for path in paths:
            click.echo(click.style(f"{label}: {path.relative_to(out)}", fg=color))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_writer.py -v`
Expected: 全件 PASS

- [ ] **Step 5: 実際に生成して手で確認する**

```bash
cat > /tmp/acme.yaml <<'EOF'
customer: acme
gateway: api-gateway
control_plane: self-managed
idp:
  type: keycloak
  realm: acme
EOF
uv run generator/gen.py /tmp/acme.yaml -o /tmp/acme-env
docker compose -f /tmp/acme-env/compose.yaml config > /dev/null && echo "compose config: OK"
```

Expected: 生成されたパスが一覧表示され、`compose config: OK` が出る

- [ ] **Step 6: コミット**

```bash
git add generator/gen.py tests/test_writer.py
git commit -m "feat(cli): gen.py と --force の保護

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: 組み合わせ総当たりとゴールデン

**Files:**
- Create: `examples/aigw-v2-konnect-redis-stack.yaml`
- Create: `examples/apigw-self-managed-keycloak-pgvector.yaml`
- Create: `examples/aigw-v1-konnect-minimal.yaml`
- Create: `tests/test_matrix.py`
- Create: `tests/test_golden.py`
- Create: `scripts/update_golden.py`

**Interfaces:**
- Consumes: `schema.EnvSpec`、`context.build_context`、`render.render_all`、`gen.write_files`
- Produces: `tests/golden/<example 名>/` 以下に期待出力。`scripts/update_golden.py` で再生成できる

- [ ] **Step 1: 代表 3 構成の example を書く**

`examples/aigw-v2-konnect-redis-stack.yaml`:

```yaml
customer: acme
gateway: ai-gateway-v2
control_plane: konnect
region: us

ai:
  providers:
    - type: azure
      instance: acme-foundry
      auth: api-key
      models:
        - name: gpt-5-6
          deployment_id: gpt-5.6
          api_version: 2024-12-01-preview
  semantic_cache: true

cache:
  type: redis-stack

vectordb:
  type: redis-stack
```

`examples/apigw-self-managed-keycloak-pgvector.yaml`:

```yaml
customer: acme
gateway: api-gateway
control_plane: self-managed

idp:
  type: keycloak
  realm: acme

vectordb:
  type: pgvector

upstream: httpbin
```

`examples/aigw-v1-konnect-minimal.yaml`:

```yaml
customer: acme
gateway: ai-gateway-v1
control_plane: konnect

ai:
  providers:
    - type: azure
      instance: acme-foundry
      auth: api-key
      models:
        - name: gpt-5-6
          deployment_id: gpt-5.6
          api_version: 2024-12-01-preview

idp:
  type: none

cache:
  type: none

vectordb:
  type: none
```

- [ ] **Step 2: 総当たりテストを書く**

`tests/test_matrix.py`:

```python
import itertools
import shutil
import subprocess

import pytest
import yaml
from pydantic import ValidationError

from generator.context import build_context
from generator.render import render_all
from generator.schema import EnvSpec

AZURE_PROVIDER = {
    "type": "azure",
    "instance": "acme-foundry",
    "auth": "api-key",
    "models": [
        {"name": "gpt-5-6", "deployment_id": "gpt-5.6", "api_version": "2024-12-01-preview"}
    ],
}

GATEWAYS = ["ai-gateway-v2", "ai-gateway-v1", "api-gateway"]
CONTROL_PLANES = ["konnect", "self-managed"]
IDPS = [{"type": "none"}, {"type": "keycloak", "realm": "acme"}, {"type": "entra-id"}]
CACHES = ["none", "redis", "redis-stack"]
VECTORDBS = ["none", "redis-stack", "pgvector"]


def _candidates():
    for gateway, cp, idp, cache, vectordb in itertools.product(
        GATEWAYS, CONTROL_PLANES, IDPS, CACHES, VECTORDBS
    ):
        data = {
            "customer": "acme",
            "gateway": gateway,
            "control_plane": cp,
            "idp": idp,
            "cache": {"type": cache},
            "vectordb": {"type": vectordb},
        }
        if gateway.startswith("ai-gateway"):
            data["ai"] = {
                "providers": [AZURE_PROVIDER],
                "semantic_cache": vectordb != "none",
            }
        yield data


def _valid_specs():
    specs = []
    for data in _candidates():
        try:
            specs.append(EnvSpec.model_validate(data))
        except ValidationError:
            continue  # スキーマが弾く組み合わせは対象外
    return specs


VALID_SPECS = _valid_specs()


def _spec_id(spec: EnvSpec) -> str:
    return "-".join(
        [
            spec.gateway.value,
            spec.control_plane.value,
            spec.idp.type.value,
            spec.cache.type.value,
            spec.vectordb.type.value,
        ]
    )


def test_matrix_is_not_trivially_small():
    # 組み合わせの生成条件を壊したときに気づくための番人
    assert len(VALID_SPECS) > 40, len(VALID_SPECS)


@pytest.mark.parametrize("spec", VALID_SPECS, ids=_spec_id)
def test_every_generated_yaml_parses(spec):
    files = render_all(build_context(spec))
    for relpath, body in files.items():
        if relpath.endswith((".yaml", ".yml")):
            if relpath == "config/kongctl.yaml":
                continue  # カスタムタグを含むため test_kongctl.py 側で確認する
            assert yaml.safe_load(body) is not None, relpath


@pytest.mark.docker
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker がない")
@pytest.mark.parametrize("spec", VALID_SPECS, ids=_spec_id)
def test_docker_compose_config_accepts_every_combination(spec, tmp_path):
    from generator.gen import write_files

    out = tmp_path / _spec_id(spec)
    out.mkdir(parents=True)
    write_files(out, render_all(build_context(spec)), force=False)

    result = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=out,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("spec", VALID_SPECS, ids=_spec_id)
def test_every_compose_service_name_matches_wiring(spec):
    doc = yaml.safe_load(render_all(build_context(spec))["compose.yaml"])
    services = set(doc["services"])
    ctx = build_context(spec)
    if ctx.cache.enabled:
        assert ctx.cache.host in services
    if ctx.vector.enabled and ctx.vector.type.value == "pgvector":
        assert ctx.vector.host in services
    if ctx.idp.service_name:
        assert ctx.idp.service_name in services
    if ctx.upstream.enabled:
        assert ctx.upstream.service_name in services
```

- [ ] **Step 3: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_matrix.py -v`
Expected: FAIL する組み合わせがあれば、そのパーシャルのインデントか変数参照に不備がある。全件 PASS するまで該当テンプレートを直す。`test_matrix_is_not_trivially_small` が落ちる場合は `_candidates` の生成条件が厳しすぎる。

- [ ] **Step 4: ゴールデンの生成スクリプトを書く**

`scripts/update_golden.py`:

```python
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
```

- [ ] **Step 5: ゴールデンテストを書く**

`tests/test_golden.py`:

```python
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
```

- [ ] **Step 6: ゴールデンを生成し、目視で確認してからコミットする**

```bash
uv run python scripts/update_golden.py
uv run pytest tests/test_golden.py -v
git status --short tests/golden
```

生成された `tests/golden/*/compose.yaml` を 3 つとも開いて読むこと。ここが今後の全ての差分の基準になるため、この時点で不自然な値が入っていたら該当テンプレートを直してから再生成する。

- [ ] **Step 7: 全テストを実行する**

Run: `uv run pytest -v`
Expected: 全件 PASS

- [ ] **Step 8: コミット**

```bash
git add examples scripts/update_golden.py tests/test_matrix.py tests/test_golden.py tests/golden
git commit -m "test: 組み合わせ総当たりとゴールデン 3 構成

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: 実起動スモークとジェネレータの README

**Files:**
- Create: `tests/test_smoke_apigw.py`
- Create: `README.md`
- Modify: `pyproject.toml`（既定で slow を除外する設定を追加）

**Interfaces:**
- Consumes: `gen.main`、生成物の `mise` タスク
- Produces: 外部クレデンシャル不要の 1 構成に対する実起動テスト。既定の `pytest` 実行からは除外し、`-m slow` で明示的に実行する

**この構成を選ぶ理由:** `api-gateway` / `self-managed` / `keycloak` / `httpbin` は Konnect テナントもクラウドのクレデンシャルも要求しない唯一の組み合わせで、生成物が実際に起動して通信することを機械的に確認できる。Kong Enterprise のライセンスのみローカルに必要なので、環境変数 `KONG_LICENSE_DATA` が無いときはスキップする。

- [ ] **Step 1: pyproject.toml で slow を既定から外す**

`pyproject.toml` の `[tool.pytest.ini_options]` に追記:

```toml
addopts = "-m 'not slow'"
```

- [ ] **Step 2: 失敗テストを書く**

`tests/test_smoke_apigw.py`:

```python
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


def test_proxy_returns_200_through_httpbin(running_env):
    # deck sync が先に走っている必要があるため、同一モジュール内の実行順に依存する
    _wait_for(
        ["curl", "-sS", "-f", "-o", "/dev/null", "http://localhost:8000/httpbin/status/200"],
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
```

- [ ] **Step 3: テストを実行して失敗を確認する**

Run: `uv run pytest tests/test_smoke_apigw.py -v -m slow`
Expected: ライセンスがあれば FAIL または起動待ちのタイムアウト。ライセンスが無ければ SKIP。SKIP になった場合は、`KONG_LICENSE_DATA` を設定してから実行して初めてこの Task の意味がある。

- [ ] **Step 4: 落ちた箇所を直す**

想定される失敗と対処を挙げる。CP が起動しない場合はライセンス JSON に改行が混入している。DP が CP に繋がらない場合は `config/kong/certs/tls.crt` が両方にマウントされているか確認する。`deck sync` が `openid-connect` プラグインを知らないと言う場合は、Kong OSS イメージが使われているので `kong/kong-gateway` になっているか確認する。Keycloak のトークン取得が 404 になる場合は realm が import されていないので `docker compose logs keycloak` で import のログを確認する。

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `uv run pytest tests/test_smoke_apigw.py -v -m slow`
Expected: 全件 PASS

- [ ] **Step 6: ジェネレータの README を書く**

`README.md`:

````markdown
# 顧客デリバリー用 再現環境ジェネレータ

`env.yaml` に構成を宣言すると、そのまま `mise run up` が通る Kong の検証環境一式を生成します。

## 使い方

```bash
uv run generator/gen.py examples/aigw-v2-konnect-redis-stack.yaml -o ~/customer/acme
cd ~/customer/acme
# README.md の初手に従う
```

既存の環境にテンプレートの改善を再適用するときは `--force` を付けます。生成先が git 管理下で未コミットの変更があるときは拒否されるので、先に commit するか stash してください。`.env` と `.certs/` と `docs/` は `--force` でも上書きされません。

## オプション

| キー | 値 | 既定 | 備考 |
|---|---|---|---|
| `customer` | 文字列 | 必須 | namespace と Control Plane 名の素になる |
| `target` | `compose` | `compose` | 他の値は未対応 |
| `gateway` | `ai-gateway-v2` / `ai-gateway-v1` / `api-gateway` | 必須 | |
| `control_plane` | `konnect` / `self-managed` | `konnect` | `ai-gateway-v2` は `konnect` のみ |
| `region` | 文字列 | `us` | Konnect のときのみ有効 |
| `ai.providers[]` | `azure` / `bedrock` / `vertex` / `openai` / `anthropic` | `[]` | 複数指定可 |
| `ai.semantic_cache` | 真偽値 | `false` | `vectordb` が必須になる |
| `ai.semantic_routing` | 真偽値 | `false` | `vectordb` が必須になる |
| `ai.embedding_model` | 文字列 | `text-embedding-3-large` | 次元数は `context.py` の表で解決する |
| `idp.type` | `keycloak` / `entra-id` / `none` | `none` | `keycloak` は `idp.realm` が必須 |
| `cache.type` | `redis` / `redis-stack` / `none` | `none` | |
| `vectordb.type` | `redis-stack` / `pgvector` / `none` | `none` | `redis-stack` は `cache.type: redis-stack` が必須 |
| `observability.otel_lgtm` | 真偽値 | `true` | |
| `upstream` | `httpbin` / `none` | `api-gateway` なら `httpbin` | |

## 弾かれる組み合わせ

`ai-gateway-v2` と `self-managed` は併用できません。AI Gateway v2 のデータプレーンは `KONG_KONNECT_MODE: on` を前提にしており、自前の Control Plane に繋ぐ構成が存在しないためです。

`semantic_cache` または `semantic_routing` を有効にして `vectordb.type: none` にはできません。semantic balancer は vectordb ブロックが必須で、未設定のまま sync すると kongctl が失敗します。

`vectordb.type: redis-stack` には `cache.type: redis-stack` が必要です。素の `redis:8` には検索モジュールが入っておらず、`none` ではコンテナ自体が存在しません。

## テスト

```bash
uv run pytest                      # L1 と L2。数十秒
uv run pytest -m slow              # L3 の実起動スモーク。KONG_LICENSE_DATA が必要
uv run python scripts/update_golden.py   # テンプレート変更後にゴールデンを更新する
```

## 今後

`target: kubernetes` は `~/customer/nksol/kubernetes` の Helm values を、`target: cloud-run` と `target: aca` は `~/customer/sony-sonpo/*/terraform` を出発点として追加する予定です。
````

- [ ] **Step 7: 全テストを実行する**

Run: `uv run pytest -v && uv run pytest -v -m slow`
Expected: 全件 PASS（ライセンス未設定なら slow は SKIP）

- [ ] **Step 8: コミット**

```bash
git add README.md pyproject.toml tests/test_smoke_apigw.py
git commit -m "test: 実起動スモークとジェネレータの README

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```
