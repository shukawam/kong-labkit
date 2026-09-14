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
