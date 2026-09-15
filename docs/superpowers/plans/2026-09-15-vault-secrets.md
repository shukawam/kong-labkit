# HashiCorp Vault による秘匿情報の外部化 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `secrets.backend: vault` を指定した構成で、API キーやクライアントシークレットを `config/kong/kong.yaml` に実値として載せず、`{vault://hcv/...}` 参照として書き、実行時にデータプレーンが同梱の HashiCorp Vault から解決する。

**Architecture:** 秘密の参照文字列を作る責務を `generator/context.py` の `_SecretRefs` に集約し、backend に応じて decK の env 参照か vault 参照のどちらかを返す。Vault への投入が必要な値は同じ場所で `SecretSeed` として集め、`vault-bootstrap` コンテナの `vault kv put` に変換する。Kong 側は Vault エンティティを宣言せず、データプレーンの `KONG_VAULT_HCV_*` だけで解決する。

**Tech Stack:** Python 3.12+ / Pydantic / Jinja2（StrictUndefined）/ Click / Docker Compose / Kong Gateway 3.14 / HashiCorp Vault（dev モード）

**Spec:** `docs/superpowers/specs/2026-09-15-vault-secrets-design.md`

## Global Constraints

- Python は 4 スペースインデント、型注釈あり、`snake_case` 関数、`PascalCase` クラス、`UPPER_SNAKE_CASE` 定数。YAML は 2 スペース。
- 検証は `generator/schema.py`、派生値の計算は `generator/context.py` に置く。Jinja の `StrictUndefined` は外さない。
- コメントは why が非自明な箇所にだけ書く。「この項目は何か」は書かない。
- Vault の参照形式は `{vault://hcv/kong/<用途>/<キー>}`。用途は `ai-<provider>` / `ldap` / `oidc` の 3 種類。
- KV キー名は参照先の Kong のフィールド名をそのまま使う（`header_value`、`azure_client_id`、`azure_tenant_id`、`aws_access_key_id`、`aws_secret_access_key`、`gcp_service_account_json`、`ldap_password`、`client_secret`）。
- Vault 参照は文字列連結できない。フィールドの値全体が参照である必要がある。
- `KONG_VAULT_HCV_KV` は `v2` 固定。Kong 側の既定は `v1` で、dev モードの Vault は KV v2 のため。
- Vault のイメージは `hashicorp/vault:2.1.0`、ルートトークンのラボ既定値は `local-dev-token`。
- 秘密でない識別子（`AZURE_CLIENT_ID`、`AZURE_TENANT_ID`）は Vault に入れず、従来どおり decK の env 参照のままにする。
- テストは `uv run pytest`（`slow` を除く既定スイート）が常に緑であること。テンプレートを変えたら `uv run python scripts/update_golden.py`、スキーマを変えたら `uv run python scripts/update_schema.py` を実行して差分をレビューする。
- コミットは Conventional Commits の日本語要約（`feat:` / `fix:` / `docs:` / `chore:`）。

---

### Task 1: `secrets.backend` をスキーマに足す

**Files:**
- Modify: `generator/schema.py`
- Modify: `schemas/env.schema.json`（`scripts/update_schema.py` で再生成）
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: なし（最初のタスク）
- Produces: `SecretsBackend`（`StrEnum`、値は `"env"` / `"vault"`）、`SecretsSpec`（フィールド `backend: SecretsBackend`）、`EnvSpec.secrets: SecretsSpec`。以降のタスクは `spec.secrets.backend` を読む。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_schema.py` の import を差し替える。

```python
from generator.schema import (
    ControlPlane,
    EnvSpec,
    Gateway,
    NewRelicRegion,
    ObservabilityBackend,
    SecretsBackend,
    UpstreamType,
    load_spec,
)
```

`tests/test_schema.py` の末尾に足す。

```python
def test_secrets_backend_defaults_to_env():
    assert spec_from().secrets.backend is SecretsBackend.ENV


def test_vault_backend_is_rejected_for_aigw_v2():
    with pytest.raises(ValidationError) as e:
        spec_from(gateway="ai-gateway-v2", secrets={"backend": "vault"})
    assert "secrets.backend: vault" in str(e.value)


def test_vault_backend_is_accepted_for_deck_gateways():
    assert spec_from(secrets={"backend": "vault"}).secrets.backend is SecretsBackend.VAULT
    assert (
        spec_from(gateway="ai-gateway-v1", secrets={"backend": "vault"}).secrets.backend
        is SecretsBackend.VAULT
    )
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `uv run pytest tests/test_schema.py -q`
Expected: FAIL（`ImportError: cannot import name 'SecretsBackend'`）

- [ ] **Step 3: スキーマを実装する**

`generator/schema.py` の `class ProviderType(StrEnum):` の直前に足す。

```python
class SecretsBackend(StrEnum):
    ENV = "env"
    VAULT = "vault"
```

`class ObservabilitySpec(Strict):` の直前に足す。

```python
class SecretsSpec(Strict):
    backend: SecretsBackend = SecretsBackend.ENV
```

`EnvSpec` のフィールド一覧、`observability` の次の行に足す。

```python
    secrets: SecretsSpec = Field(default_factory=SecretsSpec)
```

`EnvSpec` の validator 群の末尾に足す。

```python
    @model_validator(mode="after")
    def _check_vault_needs_the_deck_path(self):
        if (
            self.secrets.backend is SecretsBackend.VAULT
            and self.gateway is Gateway.AI_GATEWAY_V2
        ):
            raise ValueError(
                "secrets.backend: vault は ai-gateway-v2 では使えません"
                "（v2 の設定は config/kongctl.yaml の AI Gateway エンティティだけで、"
                "そこが vault 参照を受け付けるか検証できていません）。"
                "gateway: ai-gateway-v1 か api-gateway にしてください。"
            )
        return self
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest tests/test_schema.py -q`
Expected: PASS（`test_json_schema_is_up_to_date` だけが落ちる）

- [ ] **Step 5: JSON Schema を再生成して全体を通す**

Run: `uv run python scripts/update_schema.py && uv run pytest -q`
Expected: 全件 PASS

- [ ] **Step 6: コミット**

```bash
git add generator/schema.py schemas/env.schema.json tests/test_schema.py
git commit -m "feat: secrets.backend を入力スキーマに足す"
```

---

### Task 2: 秘密参照のファクトリと `SecretsCtx`

**Files:**
- Modify: `generator/context.py`
- Modify: `generator/render.py:22-38`（`template_vars` に `secrets` を足す）
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: Task 1 の `SecretsBackend`、`spec.secrets.backend`
- Produces:
  - `SecretSeed`（frozen dataclass、フィールド `path: str` / `key: str` / `env_var: str | None` / `prefix: str` / `literal: str | None`、プロパティ `shell_value: str`）
  - `SecretsCtx`（frozen dataclass、フィールド `backend: SecretsBackend` / `enabled: bool` / `host: str` / `mount: str` / `prefix: str` / `token: str` / `seeds: tuple[SecretSeed, ...]`、プロパティ `seeds_by_path: dict[str, tuple[SecretSeed, ...]]` と `env_sources: tuple[str, ...]`）
  - `_SecretRefs`（メソッド `env(source) -> str` / `secret_from_env(source, *, path, key, prefix="") -> str` / `secret_from_literal(value, *, path, key) -> str`、属性 `aliases: dict[str, str]` と `seeds: list[SecretSeed]`）
  - `Ctx.secrets: SecretsCtx`、テンプレート変数 `secrets`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_context.py` の末尾に足す。

```python
OPENAI_PROVIDER = {"type": "openai", "models": [{"name": "gpt-5-6"}]}


def aigw_spec(**overrides):
    return spec_from(gateway="ai-gateway-v1", ai={"providers": [AZURE_PROVIDER]}, **overrides)


def test_provider_auth_uses_deck_env_by_default():
    auth = build_context(aigw_spec()).deck.provider_auth[0]
    assert auth["header_value"] == "'${{ env \"DECK_AZURE_OPENAI_API_KEY\" }}'"


def test_provider_auth_uses_a_vault_reference():
    ctx = build_context(aigw_spec(secrets={"backend": "vault"}))
    assert ctx.deck.provider_auth[0]["header_value"] == "'{vault://hcv/kong/ai-azure/header_value}'"
    # vault 構成では decK に渡す環境変数が要らなくなる
    assert ctx.deck.env_aliases == {}


def test_vault_seeds_carry_the_env_var_to_inject():
    ctx = build_context(aigw_spec(secrets={"backend": "vault"}))
    seed = ctx.secrets.seeds[0]
    assert (seed.path, seed.key, seed.env_var) == ("ai-azure", "header_value", "AZURE_OPENAI_API_KEY")
    assert seed.shell_value == "${AZURE_OPENAI_API_KEY}"


def test_openai_bearer_prefix_moves_into_the_vault_value():
    """Kong は値の一部に埋めた参照を解決しない。Bearer ごと Vault に入れる必要がある。"""
    ctx = build_context(
        spec_from(gateway="ai-gateway-v1", ai={"providers": [OPENAI_PROVIDER]}, secrets={"backend": "vault"})
    )
    assert ctx.deck.provider_auth[0]["header_value"] == "'{vault://hcv/kong/ai-openai/header_value}'"
    assert ctx.secrets.seeds[0].shell_value == "Bearer ${OPENAI_API_KEY}"


def test_openai_bearer_prefix_stays_inline_for_the_env_backend():
    auth = build_context(
        spec_from(gateway="ai-gateway-v1", ai={"providers": [OPENAI_PROVIDER]})
    ).deck.provider_auth[0]
    assert auth["header_value"] == "'Bearer ${{ env \"DECK_OPENAI_API_KEY\" }}'"


def test_identifiers_stay_on_the_env_backend_even_with_vault():
    """client_id / tenant_id は秘密ではないので Vault に入れない。"""
    provider = {**AZURE_PROVIDER, "auth": "managed-identity"}
    ctx = build_context(
        spec_from(gateway="ai-gateway-v1", ai={"providers": [provider]}, secrets={"backend": "vault"})
    )
    auth = ctx.deck.provider_auth[0]
    assert auth["azure_client_id"] == "'${{ env \"DECK_AZURE_CLIENT_ID\" }}'"
    assert ctx.secrets.seeds == ()


def test_secrets_context_is_disabled_by_default():
    secrets = build_context(spec_from()).secrets
    assert secrets.enabled is False
    assert secrets.seeds == ()


def test_secrets_context_describes_the_vault():
    secrets = build_context(aigw_spec(secrets={"backend": "vault"})).secrets
    assert (secrets.enabled, secrets.host, secrets.mount, secrets.prefix) == (
        True,
        "vault",
        "secret",
        "kong",
    )
    assert secrets.env_sources == ("AZURE_OPENAI_API_KEY",)
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `uv run pytest tests/test_context.py -q`
Expected: FAIL（`AttributeError: 'Ctx' object has no attribute 'secrets'`）

- [ ] **Step 3: `_SecretRefs` と `SecretsCtx` を実装する**

`generator/context.py` の import に `SecretsBackend` を足す（`ProviderType` の前、アルファベット順の位置）。

`class PortConflictError(Exception):` の直前に定数を足す。

```python
VAULT_HOST = "vault"
VAULT_MOUNT = "secret"
VAULT_PATH_PREFIX = "kong"
# ラボ固定。乱数にするとゴールデンテストが毎回落ちる
VAULT_ROOT_TOKEN = "local-dev-token"
```

`class _DeckEnv:` を次で丸ごと置き換える。

```python
@dataclass(frozen=True)
class SecretSeed:
    """vault-bootstrap が KV に投入する 1 項目。"""

    path: str
    key: str
    env_var: str | None
    prefix: str
    literal: str | None

    @property
    def shell_value(self) -> str:
        """bootstrap の sh から見た値。環境変数由来なら展開式、固定値ならそのまま。"""
        if self.env_var:
            return self.prefix + "${" + self.env_var + "}"
        return self.prefix + (self.literal or "")


@dataclass(frozen=True)
class SecretsCtx:
    backend: SecretsBackend
    enabled: bool
    host: str
    mount: str
    prefix: str
    token: str
    seeds: tuple[SecretSeed, ...]

    @property
    def seeds_by_path(self) -> dict[str, tuple[SecretSeed, ...]]:
        """vault kv put は 1 パスに複数キーをまとめて書けるので、パス単位に畳む。"""
        grouped: dict[str, list[SecretSeed]] = {}
        for seed in self.seeds:
            grouped.setdefault(seed.path, []).append(seed)
        return {path: tuple(seeds) for path, seeds in grouped.items()}

    @property
    def env_sources(self) -> tuple[str, ...]:
        # dict.fromkeys で宣言順を保ったまま重複を潰す
        return tuple(dict.fromkeys(s.env_var for s in self.seeds if s.env_var))


class _SecretRefs:
    """設定に書く参照文字列を決め、必要な別名と Vault への投入項目を集める。

    decK が展開するのは ${{ env "DECK_..." }} だけで、裸の ${VAR} はただの文字列になる。
    vault 構成では参照文字列がそのまま設定に残り、解決はデータプレーンが行う。
    """

    def __init__(self, backend: SecretsBackend, deck_enabled: bool) -> None:
        self.backend = backend
        self.deck_enabled = deck_enabled
        self.aliases: dict[str, str] = {}
        self.seeds: list[SecretSeed] = []

    def env(self, source: str) -> str:
        """秘密ではない識別子。backend によらず decK の env 参照のままにする。"""
        alias = DECK_ENV_PREFIX + source
        if self.deck_enabled:
            self.aliases[alias] = source
        return '${{ env "' + alias + '" }}'

    def secret_from_env(self, source: str, *, path: str, key: str, prefix: str = "") -> str:
        if self.backend is SecretsBackend.VAULT:
            self.seeds.append(
                SecretSeed(path=path, key=key, env_var=source, prefix=prefix, literal=None)
            )
            return self._vault_ref(path, key)
        return prefix + self.env(source)

    def secret_from_literal(self, value: str, *, path: str, key: str) -> str:
        if self.backend is SecretsBackend.VAULT:
            self.seeds.append(
                SecretSeed(path=path, key=key, env_var=None, prefix="", literal=value)
            )
            return self._vault_ref(path, key)
        return value

    @staticmethod
    def _vault_ref(path: str, key: str) -> str:
        return "{vault://hcv/" + f"{VAULT_PATH_PREFIX}/{path}/{key}" + "}"
```

`_build_provider_auth` を次で置き換える。

```python
def _build_provider_auth(provider, secrets: _SecretRefs) -> dict[str, str]:
    path = f"ai-{provider.type.value}"
    if provider.type is ProviderType.AZURE:
        if provider.auth is AuthType.MANAGED_IDENTITY:
            return {
                "azure_use_managed_identity": "true",
                "azure_client_id": _yaml_scalar(secrets.env("AZURE_CLIENT_ID")),
                "azure_tenant_id": _yaml_scalar(secrets.env("AZURE_TENANT_ID")),
            }
        return {
            "header_name": "api-key",
            "header_value": _yaml_scalar(
                secrets.secret_from_env("AZURE_OPENAI_API_KEY", path=path, key="header_value")
            ),
        }
    if provider.type is ProviderType.OPENAI:
        return {
            "header_name": "Authorization",
            "header_value": _yaml_scalar(
                secrets.secret_from_env(
                    "OPENAI_API_KEY", path=path, key="header_value", prefix="Bearer "
                )
            ),
        }
    if provider.type is ProviderType.ANTHROPIC:
        return {
            "header_name": "x-api-key",
            "header_value": _yaml_scalar(
                secrets.secret_from_env("ANTHROPIC_API_KEY", path=path, key="header_value")
            ),
        }
    if provider.type is ProviderType.BEDROCK:
        return {
            "aws_access_key_id": _yaml_scalar(
                secrets.secret_from_env("AWS_ACCESS_KEY_ID", path=path, key="aws_access_key_id")
            ),
            "aws_secret_access_key": _yaml_scalar(
                secrets.secret_from_env(
                    "AWS_SECRET_ACCESS_KEY", path=path, key="aws_secret_access_key"
                )
            ),
        }
    return {
        "gcp_use_service_account": "true",
        # パスではなくサービスアカウント JSON の中身そのものを渡す必要がある
        "gcp_service_account_json": _yaml_scalar(
            secrets.secret_from_env(
                "GOOGLE_APPLICATION_CREDENTIALS", path=path, key="gcp_service_account_json"
            )
        ),
    }
```

`_build_deck` の引数名を合わせる。

```python
def _build_deck(spec: EnvSpec, secrets: _SecretRefs) -> DeckCtx:
    provider_auth = [_build_provider_auth(p, secrets) for p in spec.ai.providers]
    first = spec.ai.providers[0] if spec.ai.providers else None
    return DeckCtx(
        env_aliases=secrets.aliases,
        provider_auth=provider_auth,
        # 埋め込みモデルは先頭のプロバイダに属するものとして扱う
        embeddings_provider=first.type.value if first else None,
        embeddings_auth=provider_auth[0] if provider_auth else None,
    )
```

`SecretsCtx` を組み立てる関数を `_build_deck` の直後に足す。

```python
def _build_secrets(spec: EnvSpec, refs: _SecretRefs) -> SecretsCtx:
    enabled = spec.secrets.backend is SecretsBackend.VAULT
    return SecretsCtx(
        backend=spec.secrets.backend,
        enabled=enabled,
        host=VAULT_HOST,
        mount=VAULT_MOUNT,
        prefix=VAULT_PATH_PREFIX,
        token=VAULT_ROOT_TOKEN,
        seeds=tuple(refs.seeds) if enabled else (),
    )
```

`Ctx` のフィールドに足す（`deck: DeckCtx` の次の行）。

```python
    secrets: SecretsCtx
```

`build_context` を書き換える。`_build_secrets` は参照を集め終えた後に呼ぶ必要があるため、`_build_deck` より後に置く。

```python
def build_context(spec: EnvSpec) -> Ctx:
    ports = allocate_ports(spec)
    kong = _build_kong(spec)
    # ai-gateway-v2 は kongctl 経路なので decK の別名は要らない
    refs = _SecretRefs(spec.secrets.backend, deck_enabled=spec.gateway is not Gateway.AI_GATEWAY_V2)
    idp = _build_idp(spec, ports, refs)
    deck = _build_deck(spec, refs)
    return Ctx(
        spec=spec,
        kong=kong,
        idp=idp,
        otel=_build_otel(spec),
        cache=_build_cache(spec),
        vector=_build_vector(spec),
        upstream=_build_upstream(spec),
        semantic=_build_semantic(spec),
        certs=_build_certs(spec, kong),
        deck=deck,
        secrets=_build_secrets(spec, refs),
        ports=ports,
        services=_build_services(spec),
        env_vars=_build_env_vars(spec),
        namespace=spec.customer,
    )
```

`_build_idp` の引数名を `deck_env` から `refs` に変え、本体の `deck_env.ref(` を `refs.env(` に置換する（Task 3 で `client_secret` 側を差し替えるので、ここでは機械的な置換のみ）。

`generator/render.py` の `template_vars` に 1 行足す（`"deck": ctx.deck,` の次）。

```python
        "secrets": ctx.secrets,
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest tests/test_context.py -q`
Expected: PASS

- [ ] **Step 5: 既存テストへの影響を確認する**

Run: `uv run pytest -q`
Expected: 全件 PASS（`env` backend の出力は変わらないので golden も無傷）

- [ ] **Step 6: コミット**

```bash
git add generator/context.py generator/render.py tests/test_context.py
git commit -m "feat: 秘密の参照文字列を作る責務を _SecretRefs に集約する"
```

---

### Task 3: IdP の秘密を Vault 参照にする

**Files:**
- Modify: `generator/context.py`（`LdapCtx`、`IdpCtx`、`_build_ldap`、`_build_idp`）
- Modify: `generator/templates/config/kong.yaml.j2:10`（`client_secret`）と `:23`（`ldap_password`）
- Test: `tests/test_context.py`、`tests/test_kong_yaml.py`

**Interfaces:**
- Consumes: Task 2 の `_SecretRefs.secret_from_env` / `secret_from_literal`
- Produces: `LdapCtx.bind_password_ref: str` と `IdpCtx.client_secret_ref: str | None`。`kong.yaml` はこの 2 つを使い、`bind_password` と `client_secret`（実値）は lldap のサービス定義と `realm-export.json` が引き続き使う。

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_context.py` の末尾に足す。

```python
def test_ldap_password_is_a_literal_by_default():
    idp = build_context(spec_from(idp={"type": "ldap"})).idp
    assert idp.ldap.bind_password_ref == idp.ldap.bind_password


def test_ldap_password_becomes_a_vault_reference():
    ctx = build_context(spec_from(idp={"type": "ldap"}, secrets={"backend": "vault"}))
    assert ctx.idp.ldap.bind_password_ref == "{vault://hcv/kong/ldap/ldap_password}"
    # lldap コンテナ自身は実値が要るので、こちらは平文のまま残る
    assert ctx.idp.ldap.bind_password == "local-dev-password"
    seed = next(s for s in ctx.secrets.seeds if s.path == "ldap")
    assert (seed.key, seed.literal) == ("ldap_password", "local-dev-password")


def test_keycloak_client_secret_becomes_a_vault_reference():
    ctx = build_context(
        spec_from(idp={"type": "keycloak", "realm": "acme"}, secrets={"backend": "vault"})
    )
    assert ctx.idp.client_secret_ref == "{vault://hcv/kong/oidc/client_secret}"
    # realm-export.json は Keycloak 側の設定なので実値が要る
    assert ctx.idp.client_secret == "local-dev-secret"


def test_entra_client_secret_becomes_a_vault_reference():
    ctx = build_context(spec_from(idp={"type": "entra-id"}, secrets={"backend": "vault"}))
    assert ctx.idp.client_secret_ref == "{vault://hcv/kong/oidc/client_secret}"
    # client_id は秘密ではないので decK の env 参照のまま
    assert ctx.idp.client_id == '${{ env "DECK_AZURE_CLIENT_ID" }}'
```

`tests/test_kong_yaml.py` の末尾に足す。

```python
def test_ldap_plugin_writes_a_vault_reference():
    doc = kong_yaml_of(ctx_for(idp={"type": "ldap"}, secrets={"backend": "vault"}))
    for service in doc["services"]:
        for route in service["routes"]:
            for plugin in route.get("plugins", []):
                if plugin["name"] == "ldap-auth-advanced":
                    assert plugin["config"]["ldap_password"] == "{vault://hcv/kong/ldap/ldap_password}"
                    return
    raise AssertionError("ldap-auth-advanced が無い")


def test_no_plaintext_secret_remains_in_kong_yaml_with_vault():
    body = render_all(ctx_for(idp={"type": "ldap"}, secrets={"backend": "vault"}))["config/kong/kong.yaml"]
    assert "local-dev-password" not in body
```

`tests/test_kong_yaml.py` の import に `render_all` が無ければ足す。

- [ ] **Step 2: テストが落ちることを確認する**

Run: `uv run pytest tests/test_context.py tests/test_kong_yaml.py -q`
Expected: FAIL（`AttributeError: 'LdapCtx' object has no attribute 'bind_password_ref'`）

- [ ] **Step 3: コンテキストとテンプレートを実装する**

`generator/context.py` の `LdapCtx` に足す（`bind_password: str` の次の行）。

```python
    # kong.yaml が書く形。vault 構成では参照文字列になる
    bind_password_ref: str
```

`IdpCtx` に足す（`client_secret: str | None` の次の行）。

```python
    client_secret_ref: str | None
```

`_build_ldap` のシグネチャに `refs` を足し、`bind_password_ref` を埋める。

```python
def _build_ldap(spec: EnvSpec, ports: dict[str, int], refs: _SecretRefs) -> LdapCtx:
```

`LdapCtx(...)` の `bind_password=LLDAP_ADMIN_PASSWORD,` の次に足す。

```python
        bind_password_ref=refs.secret_from_literal(
            LLDAP_ADMIN_PASSWORD, path="ldap", key="ldap_password"
        ),
```

`_build_idp` の 4 つの `return IdpCtx(...)` それぞれに `client_secret_ref` を足す。

Keycloak 分岐:

```python
            client_secret=KEYCLOAK_CLIENT_SECRET,
            client_secret_ref=refs.secret_from_literal(
                KEYCLOAK_CLIENT_SECRET, path="oidc", key="client_secret"
            ),
```

Entra ID 分岐:

```python
            client_id=refs.env("AZURE_CLIENT_ID"),
            client_secret=None,
            client_secret_ref=refs.secret_from_env(
                "AZURE_CLIENT_SECRET", path="oidc", key="client_secret"
            ),
```

LDAP 分岐と none 分岐:

```python
            client_secret=None,
            client_secret_ref=None,
```

LDAP 分岐の `_build_ldap` 呼び出しを `ldap = _build_ldap(spec, ports, refs)` に変える。

`generator/templates/config/kong.yaml.j2` の 2 箇所を差し替える。

```jinja
          client_secret:
            - '{{ idp.client_secret_ref }}'
```

```jinja
          ldap_password: {{ idp.ldap.bind_password_ref }}
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `uv run pytest tests/test_context.py tests/test_kong_yaml.py -q`
Expected: PASS

- [ ] **Step 5: 全体を通してゴールデンを確認する**

Run: `uv run pytest -q`
Expected: 全件 PASS。`env` backend では `bind_password_ref == bind_password` なので既存の生成物は 1 文字も変わらない。落ちる場合は `git diff tests/golden` を見て、実値が参照に変わっていないことを確かめる。

- [ ] **Step 6: コミット**

```bash
git add generator/context.py generator/templates/config/kong.yaml.j2 tests/test_context.py tests/test_kong_yaml.py
git commit -m "feat: IdP の秘密を vault 参照として書けるようにする"
```

---

### Task 4: Vault サービスと Kong への配線

**Files:**
- Create: `generator/templates/services/vault.yaml.j2`
- Create: `generator/templates/services/vault-bootstrap.yaml.j2`
- Modify: `generator/context.py`（`PREFERRED_PORTS`、`_ALLOCATION_ORDER`、`_required_keys`、`_build_services`、`_build_env_vars`）
- Modify: `generator/templates/services/kong-dp.yaml.j2`、`generator/templates/services/kong-self-managed.yaml.j2`
- Test: `tests/test_vault.py`（新規）、`tests/test_ports.py`

**Interfaces:**
- Consumes: Task 2 の `SecretsCtx`（`enabled` / `host` / `mount` / `prefix` / `token` / `seeds_by_path` / `env_sources`）と `SecretSeed.shell_value`
- Produces: compose のサービス名 `vault` と `vault-bootstrap`、ポートキー `vault`、`.env` の `VAULT_TOKEN`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_vault.py` を作る。

```python
import yaml

from generator.render import render_all
from tests.conftest import ctx_for


def vault_ctx(**overrides):
    return ctx_for(idp={"type": "ldap"}, secrets={"backend": "vault"}, **overrides)


def compose_doc(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)["compose.yaml"])


def test_vault_services_are_absent_by_default():
    services = compose_doc(ctx_for())["services"]
    assert "vault" not in services
    assert "vault-bootstrap" not in services


def test_vault_runs_in_dev_mode_with_the_lab_token():
    vault = compose_doc(vault_ctx())["services"]["vault"]
    assert vault["environment"]["VAULT_DEV_ROOT_TOKEN_ID"] == "${VAULT_TOKEN:-local-dev-token}"
    assert vault["ports"] == ["8200:8200"]
    assert vault["healthcheck"]["test"] == ["CMD", "vault", "status"]


def test_bootstrap_puts_every_seed_into_the_kv():
    bootstrap = compose_doc(vault_ctx())["services"]["vault-bootstrap"]
    script = bootstrap["entrypoint"][-1]
    assert 'vault kv put -mount=secret kong/ldap ldap_password="local-dev-password"' in script
    assert bootstrap["restart"] == "no"
    assert bootstrap["depends_on"] == {"vault": {"condition": "service_healthy"}}


def test_bootstrap_receives_env_backed_secrets():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [{"type": "openai", "models": [{"name": "gpt-5-6"}]}]},
        secrets={"backend": "vault"},
    )
    bootstrap = compose_doc(ctx)["services"]["vault-bootstrap"]
    assert bootstrap["environment"]["OPENAI_API_KEY"] == "${OPENAI_API_KEY}"
    assert 'header_value="Bearer ${OPENAI_API_KEY}"' in bootstrap["entrypoint"][-1]


def test_data_plane_is_wired_to_the_vault():
    env = compose_doc(vault_ctx())["services"]["kong-dp"]["environment"]
    assert env["KONG_VAULT_HCV_HOST"] == "vault"
    assert env["KONG_VAULT_HCV_MOUNT"] == "secret"
    # Kong の既定は v1。dev モードの Vault は KV v2 なので外すと全参照が壊れる
    assert env["KONG_VAULT_HCV_KV"] == "v2"
    assert env["KONG_VAULT_HCV_TOKEN"] == "${VAULT_TOKEN:-local-dev-token}"


def test_data_plane_waits_for_the_bootstrap():
    dp = compose_doc(vault_ctx())["services"]["kong-dp"]
    assert dp["depends_on"]["vault-bootstrap"] == {"condition": "service_completed_successfully"}


def test_no_vault_env_on_the_data_plane_by_default():
    env = compose_doc(ctx_for())["services"]["kong-dp"]["environment"]
    assert not [k for k in env if k.startswith("KONG_VAULT_")]
```

`tests/test_ports.py` の末尾に足す。

```python
def test_vault_backend_allocates_the_vault_port():
    ports = allocate_ports(spec_from(secrets={"backend": "vault"}, upstream="none"))
    assert ports["vault"] == 8200
    assert "vault" not in allocate_ports(spec_from(upstream="none"))
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `uv run pytest tests/test_vault.py tests/test_ports.py -q`
Expected: FAIL（`KeyError: 'vault'`）

- [ ] **Step 3: ポートとサービス一覧を実装する**

`generator/context.py` の `PREFERRED_PORTS` に足す（`lldap_web` の次）。

```python
    "vault": 8200,
```

`_ALLOCATION_ORDER` の `"lldap_web",` の次に `"vault",` を足す。

`_required_keys` に足す。

```python
    if spec.secrets.backend is SecretsBackend.VAULT:
        keys.add("vault")
```

`_build_services` に足す（`otel` の分岐の前）。

```python
    if spec.secrets.backend is SecretsBackend.VAULT:
        services += ["vault", "vault-bootstrap"]
```

`_build_env_vars` に足す（`if spec.observability...` の次）。

```python
    if spec.secrets.backend is SecretsBackend.VAULT:
        env["VAULT_TOKEN"] = VAULT_ROOT_TOKEN
```

- [ ] **Step 4: サービステンプレートを作る**

`generator/templates/services/vault.yaml.j2`:

```jinja
vault:
  <<: *default
  container_name: vault
  image: hashicorp/vault:2.1.0
  # dev モードは自動で unseal され KV v2 が secret/ に載る。ラボ用途なので永続化しない
  cap_add:
    - IPC_LOCK
  environment:
    VAULT_DEV_ROOT_TOKEN_ID: ${VAULT_TOKEN:-{{ secrets.token }}}
    VAULT_DEV_LISTEN_ADDRESS: 0.0.0.0:8200
    VAULT_ADDR: http://127.0.0.1:8200
  ports:
    - {{ ports.vault }}:8200
  healthcheck:
    test: ["CMD", "vault", "status"]
    interval: 5s
    timeout: 3s
    retries: 10
```

`generator/templates/services/vault-bootstrap.yaml.j2`:

```jinja
vault-bootstrap:
  <<: *default
  container_name: vault-bootstrap
  image: hashicorp/vault:2.1.0
  # x-default の on-failure のままだと、投入が終わった後も延々と再実行される
  restart: "no"
  environment:
    VAULT_ADDR: http://{{ secrets.host }}:8200
    VAULT_TOKEN: ${VAULT_TOKEN:-{{ secrets.token }}}
{% for source in secrets.env_sources %}
    {{ source }}: ${{ '{' }}{{ source }}{{ '}' }}
{% endfor %}
  entrypoint:
    - sh
    - -e
    - -c
    - |
{% for path, seeds in secrets.seeds_by_path.items() %}
      vault kv put -mount={{ secrets.mount }} {{ secrets.prefix }}/{{ path }}{% for seed in seeds %} {{ seed.key }}="{{ seed.shell_value }}"{% endfor %}

{% endfor %}
  depends_on:
    vault:
      condition: service_healthy
```

- [ ] **Step 5: Kong への配線を足す**

`generator/templates/services/kong-dp.yaml.j2` の `{% if otel.enabled %}` ブロックの直後（`volumes:` の前）に足す。

```jinja
{% if secrets.enabled %}
    KONG_VAULT_HCV_PROTOCOL: http
    KONG_VAULT_HCV_HOST: {{ secrets.host }}
    KONG_VAULT_HCV_PORT: "8200"
    KONG_VAULT_HCV_MOUNT: {{ secrets.mount }}
    # 既定は v1。dev モードの Vault は KV v2 なので、外すと全参照が解決できなくなる
    KONG_VAULT_HCV_KV: v2
    KONG_VAULT_HCV_TOKEN: ${VAULT_TOKEN:-{{ secrets.token }}}
{% endif %}
```

同じファイルの `volumes:` の直前に足す。

```jinja
{% if secrets.enabled %}
  depends_on:
    vault-bootstrap:
      condition: service_completed_successfully
{% endif %}
```

`generator/templates/services/kong-self-managed.yaml.j2` の `kong-dp` サービス（ファイル後半）にも同じ環境変数ブロックを、`{% if otel.enabled %}` ブロックの直後・`volumes:` の前に足す。`kong-cp` には足さない（参照を解決するのはデータプレーンのため）。

`depends_on` は既にあるので、キーを消さずに追加する。

```jinja
  depends_on:
    kong-bootstrap:
      condition: service_completed_successfully
{% if secrets.enabled %}
    vault-bootstrap:
      condition: service_completed_successfully
{% endif %}
```

- [ ] **Step 6: テストが通ることを確認する**

Run: `uv run pytest tests/test_vault.py tests/test_ports.py -q`
Expected: PASS

- [ ] **Step 7: compose が docker に受け付けられることを確認する**

```bash
uv run python -c "
from pathlib import Path
from generator.context import build_context
from generator.render import render_all
from generator.schema import EnvSpec
out = Path('/tmp/vault-compose-check'); out.mkdir(parents=True, exist_ok=True)
spec = EnvSpec.model_validate({'customer':'acme','gateway':'api-gateway','control_plane':'self-managed','idp':{'type':'ldap'},'secrets':{'backend':'vault'}})
for rel, body in render_all(build_context(spec)).items():
    p = out / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(body)
"
cd /tmp/vault-compose-check && docker compose config --quiet && echo OK
```

Expected: `OK`

- [ ] **Step 8: 全体を通してコミット**

```bash
cd -
uv run pytest -q
git add generator/context.py generator/templates/services/ tests/test_vault.py tests/test_ports.py
git commit -m "feat: vault と vault-bootstrap を compose に足し、データプレーンを配線する"
```

---

### Task 5: mise タスク・README・example・known-gaps

**Files:**
- Modify: `generator/templates/mise.toml.j2:1-5`（マクロ）と `up` / `setup` タスク
- Modify: `generator/templates/README.md.j2`
- Modify: `README.md`（設定キーの表）
- Modify: `docs/superpowers/known-gaps.md`
- Create: `examples/aigw-v1-konnect-vault.yaml`
- Test: `tests/test_setup_task.py`、`tests/test_vault.py`

**Interfaces:**
- Consumes: Task 2 の `SecretsCtx.env_sources`、Task 4 のサービス名
- Produces: なし（最終のドキュメントと生成物）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_vault.py` の末尾に足す。

```python
def test_up_task_checks_the_env_vars_the_bootstrap_needs():
    ctx = ctx_for(
        gateway="ai-gateway-v1",
        ai={"providers": [{"type": "openai", "models": [{"name": "gpt-5-6"}]}]},
        secrets={"backend": "vault"},
    )
    mise = render_all(ctx)["mise.toml"]
    assert '${OPENAI_API_KEY:?Set OPENAI_API_KEY in .env}' in mise


def test_up_task_has_no_check_without_vault():
    mise = render_all(ctx_for())["mise.toml"]
    assert ":?Set " not in mise.split("[tasks.up]")[1].split("[tasks.")[0]


def test_readme_documents_the_vault_flow():
    readme = render_all(ctx_for(idp={"type": "ldap"}, secrets={"backend": "vault"}))["README.md"]
    assert "## Vault" in readme
    assert "unable to resolve reference" in readme


def test_readme_has_no_vault_section_by_default():
    assert "## Vault" not in render_all(ctx_for())["README.md"]
```

- [ ] **Step 2: テストが落ちることを確認する**

Run: `uv run pytest tests/test_vault.py -q`
Expected: FAIL（`assert '## Vault' in readme`）

- [ ] **Step 3: mise タスクを実装する**

`generator/templates/mise.toml.j2` の先頭、`check_deck_env` マクロの直後に足す。

```jinja
{% macro check_vault_env() -%}
{% for source in secrets.env_sources %}
: "{{ '${' ~ source ~ ':?Set ' ~ source ~ ' in .env}' }}"
{% endfor %}
{%- endmacro %}
```

`[tasks.up]` を書き換える。Vault 構成では `.env` の値が空のまま起動すると、`vault kv put` は通るのに Kong の参照解決だけが後で失敗して原因が分かりにくいため、起動前に止める。

```jinja
[tasks.up]
description = "環境を起動する"
run = '''
set -euo pipefail
{{ check_vault_env() }}
docker compose up -d
'''
```

`[tasks.setup]` の `{{ check_deck_env() }}` の次の行に `{{ check_vault_env() }}` を足す。

- [ ] **Step 4: README テンプレートを実装する**

`generator/templates/README.md.j2` の構成表に 1 行足す（`| Observability |` の次）。

```jinja
| 秘匿情報 | {{ 'HashiCorp Vault' if secrets.enabled else '環境変数' }} |
```

「エンドポイント」の表に足す。

```jinja
{% if secrets.enabled %}
| Vault | http://localhost:{{ ports.vault }} |
{% endif %}
```

`## LDAP のユーザー` 節の直前に節を足す。

```jinja
{% if secrets.enabled %}

## Vault

秘匿情報は `config/kong/kong.yaml` に実値として載らず、`{vault://hcv/kong/.../...}` という参照として書かれます。解決するのはデータプレーンで、`KONG_VAULT_HCV_*` で指定した Vault に実行時に問い合わせます。Konnect 側にも実値は保存されません。

`vault-bootstrap` が `vault` の起動後に一度だけ走り、次の値を KV に投入します。

| パス | キー | 出どころ |
|---|---|---|
{% for path, seeds in secrets.seeds_by_path.items() %}
{% for seed in seeds %}
| `{{ secrets.mount }}/{{ secrets.prefix }}/{{ path }}` | `{{ seed.key }}` | {{ '`.env` の `' ~ seed.env_var ~ '`' if seed.env_var else 'ラボ固定値' }} |
{% endfor %}
{% endfor %}

OpenAI の `header_value` だけは `Bearer ` を前置した完成形を格納します。Kong の vault 参照は値の一部に埋め込んでも解決されず、フィールドの値全体が参照である必要があるためです。

Vault の UI は http://localhost:{{ ports.vault }} で、トークンは `.env` の `VAULT_TOKEN` です。
{% endif %}
```

トラブルシュートに足す（`{% if vector.enabled ... %}` ブロックの後ろ）。

```jinja
{% if secrets.enabled %}

**認証だけが失敗する／上流が 401 を返す。** `docker compose logs gateway | grep "unable to resolve reference"` を見てください。Vault の参照が解決できていないと、Kong は参照文字列そのものを値として使うため、資格情報が誤っているのと同じ挙動になります。`KONG_VAULT_HCV_KV` を `v2` から変えると全参照が壊れます（Kong 側の既定は `v1` ですが、dev モードの Vault は KV v2 です）。

**Vault を再起動したら認証が通らなくなった。** 同梱の Vault は dev モードでメモリ保持のため、再起動で KV の中身が消えます。`docker compose up -d --force-recreate vault-bootstrap` で投入し直してください。
{% endif %}
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `uv run pytest tests/test_vault.py -q`
Expected: PASS

- [ ] **Step 6: example とドキュメントを足す**

`examples/aigw-v1-konnect-vault.yaml` を作る。

```yaml
# yaml-language-server: $schema=../schemas/env.schema.json
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
  type: ldap

cache:
  type: none

vectordb:
  type: none

secrets:
  backend: vault
```

`README.md` の設定キーの表、`observability.newrelic.region` の行の次に足す。

```markdown
| `secrets.backend` | `env` / `vault` | `env` | `vault` は Vault を同梱し、kong.yaml の秘密を `{vault://hcv/...}` 参照にする。`ai-gateway-v2` とは併用不可 |
```

`docs/superpowers/known-gaps.md` の `## Important` 節の末尾に足す。

```markdown
### `secrets.backend: vault` が AI Gateway v2 で使えない

v2 の設定は `config/kongctl.yaml` の AI Gateway エンティティだけで、Konnect 側が vault 参照を受け付けるかを検証できていないため schema で弾いている。v2 を Vault 構成で見せたい案件が出たら、kongctl 経路の検証から始める必要がある。

### vault 参照は文字列連結ができない

Kong はフィールドの値全体が `{vault://...}` である場合にのみ解決する。値の一部に埋め込むとリテラル文字列として扱われ、認証が黙って失敗する。generator は OpenAI の `header_value` について `Bearer ` を前置した完成形を Vault に入れることで回避している。同じ形の秘密を足すときは、前置が必要かを確認すること。

### 同梱の dev Vault は再起動で中身が消える

`hashicorp/vault` を dev モードで動かしているためメモリ保持で、`docker compose restart vault` すると KV が空になる。`vault-bootstrap` の流し直しが必要。
```

- [ ] **Step 7: 組み合わせマトリクスに secrets の軸を足す**

`tests/test_matrix.py` の定数に足す。

```python
SECRETS = ["env", "vault"]
```

`_candidates()` の `itertools.product` に `SECRETS` を加え、`data` に `"secrets": {"backend": secrets}` を入れる。

```python
def _candidates():
    for gateway, cp, idp, cache, vectordb, secrets in itertools.product(
        GATEWAYS, CONTROL_PLANES, IDPS, CACHES, VECTORDBS, SECRETS
    ):
        data = {
            "customer": "acme",
            "gateway": gateway,
            "control_plane": cp,
            "idp": idp,
            "cache": {"type": cache},
            "vectordb": {"type": vectordb},
            "secrets": {"backend": secrets},
        }
```

`_spec_id` にも軸を足して、失敗したケースを特定できるようにする。

```python
            spec.vectordb.type.value,
            spec.secrets.backend.value,
```

Run: `uv run pytest tests/test_matrix.py -q`
Expected: PASS（`vault` × `ai-gateway-v2` は schema が弾くので候補から自動的に落ちる）

- [ ] **Step 8: ゴールデンを再生成して全体を通す**

Run: `uv run python scripts/update_golden.py && uv run pytest -q`
Expected: 全件 PASS。既存のゴールデンには `mise.toml` の `[tasks.up]` の書き換えによる差分が出る。`docker compose up -d` が実行されること自体は変わっていないことを `git diff tests/golden` で確認する。

- [ ] **Step 9: コミット**

```bash
git add generator/templates/mise.toml.j2 tests/test_matrix.py generator/templates/README.md.j2 README.md docs/superpowers/known-gaps.md examples/aigw-v1-konnect-vault.yaml tests/test_vault.py tests/golden
git commit -m "feat: Vault 構成の mise タスク・README・example を足す"
```

---

### Task 6: 実起動スモーク

**Files:**
- Create: `tests/test_smoke_vault.py`

**Interfaces:**
- Consumes: Task 1〜5 の全て。生成した環境を実際に起動する。
- Produces: なし

- [ ] **Step 1: スモークテストを書く**

`tests/test_smoke_vault.py` を作る。`api-gateway` × `self-managed` にするのは、Konnect の PAT を必要とせず `KONG_LICENSE_DATA` だけで起動できるため。ポートは既存の `tests/test_smoke_apigw.py` と衝突しないよう、生成後に `compose.override.yaml` でずらす。

```python
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
    ports: !override
      - 23890:3890
      - 27170:17170
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

    subprocess.run(["docker", "compose", "up", "-d"], cwd=out, check=True)
    try:
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
```

- [ ] **Step 2: スモークを実行する**

Run: `uv run pytest tests/test_smoke_vault.py -m slow -q`
Expected: 4 件 PASS（初回は Vault と Kong のイメージ取得で数分かかる）

失敗したときの切り分け。`vault-bootstrap` の ExitCode が 0 でなければ `docker compose logs vault-bootstrap` を見る。認証が 200 にならないときは `docker compose logs kong-dp | grep "unable to resolve reference"` を見て、参照の解決に失敗していないか確認する。解決に失敗していれば `KONG_VAULT_HCV_KV` が `v2` になっているかを最初に疑う。

- [ ] **Step 3: 既定スイートに影響がないことを確認する**

Run: `uv run pytest -q`
Expected: 全件 PASS（`slow` は deselect される）

- [ ] **Step 4: コミット**

```bash
git add tests/test_smoke_vault.py
git commit -m "test: Vault 経由の LDAP 認証を実起動スモークで確かめる"
```
