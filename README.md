# 顧客デリバリー用 再現環境ジェネレータ

`env.yaml` に構成を宣言すると、そのまま `mise run up` が通る Kong の検証環境一式を生成します。

## 使い方

`customer/<顧客名>.yaml` に顧客ごとの構成を保存し、mise の引数に拡張子なしの顧客名を渡します。入力ファイル内の `customer` にも同じ顧客名を設定してください。

```bash
mkdir -p customer
cp examples/aigw-v2-konnect-redis-stack.yaml customer/acme.yaml
# customer/acme.yaml を顧客の構成に合わせて編集する
mise run generate acme
cd ~/customer/acme
# README.md の初手に従う
```

`mise run generate acme` は `uv run generator/gen.py customer/acme.yaml -o ~/customer/acme` を実行します。たとえば `mise run generate aozora-bank` は `customer/aozora-bank.yaml` を読み、`~/customer/aozora-bank` に出力します。これらのコマンドは template プロジェクト内で実行してください。

```bash
mise run generate acme --out ~/customer/acme-test  # 出力先を変更
mise run generate acme --force                    # 既存の環境を更新
mise run generate --help                         # 引数とオプションを表示
```

既存の環境にテンプレートの改善を再適用するときは `--force` を付けます。生成先が git 管理下で未コミットの変更があるときは拒否されるので、先に commit するか stash してください。`.env` と `.certs/` と `docs/` は `--force` でも上書きされません。顧客ごとの入力を置く `customer/` は、このリポジトリでは git 管理対象外です。

すべての構成で `config/kongctl.yaml` を生成します。Konnect 構成では `mise run sync` が Control Plane（AI Gateway v2 では AI Gateway）を作成し、generator がローカルで作成した `.certs/cluster.crt` を登録します。API Gateway と AI Gateway v1 の Gateway 設定は、kongctl の decK 連携で適用します。self-managed 構成では `kongctl.yaml` に Konnect リソースを定義せず、Control Plane は Docker Compose、Gateway 設定は decK で管理します。

## オプション

| キー | 値 | 既定 | 備考 |
|---|---|---|---|
| `customer` | 文字列 | 必須 | namespace と Control Plane 名の素になる。出力先は入力ファイル名から決まるのでこれとは独立 |
| `target` | `compose` | `compose` | 他の値は未対応 |
| `gateway` | `ai-gateway-v2` / `ai-gateway-v1` / `api-gateway` | 必須 | |
| `control_plane` | `konnect` / `self-managed` | `konnect` | `ai-gateway-v2` は `konnect` のみ |
| `konnect_name` | 文字列 | `customer` | Konnect の Control Plane 名だけを差し替える。suffix（`-ai-gateway` / `-gateway`）は gateway の種類から付く |
| `region` | 文字列 | `us` | Konnect のときのみ有効 |
| `ai.providers[]` | `azure` / `bedrock` / `vertex` / `openai` / `anthropic` | `[]` | 複数指定可 |
| `ai.semantic_cache` | 真偽値 | `false` | `vectordb` が必須になる |
| `ai.semantic_routing` | 真偽値 | `false` | `vectordb` が必須になる |
| `ai.embedding_model` | 文字列 | `text-embedding-3-large` | 次元数は `context.py` の表で解決する |
| `idp.type` | `keycloak` / `entra-id` / `ldap` / `none` | `none` | `keycloak` は `idp.realm` が必須。`ldap` は lldap を同梱し `ai-gateway-v2` とは併用不可 |
| `idp.users[]` | `name` / `email` / `password` / `groups[]` | `tester` 1 名 | `ldap` のときだけ。グループは `users[].groups` から導出。`email` は `<name>@example.com`、`password` は `<name>-password` が既定 |
| `cache.type` | `redis` / `redis-stack` / `none` | `none` | |
| `vectordb.type` | `redis-stack` / `pgvector` / `none` | `none` | `redis-stack` は `cache.type: redis-stack` が必須 |
| `observability.otel_lgtm` | 真偽値 | `true` | |
| `upstream` | `httpbin` / `none` | `api-gateway` なら `httpbin` | |

## 弾かれる組み合わせ

`ai-gateway-v2` と `self-managed` は併用できません。AI Gateway v2 のデータプレーンは `KONG_KONNECT_MODE: on` を前提にしており、自前の Control Plane に繋ぐ構成が存在しないためです。

`semantic_cache` または `semantic_routing` を有効にして `vectordb.type: none` にはできません。semantic balancer は vectordb ブロックが必須で、未設定のまま sync すると kongctl が失敗します。

`ai-gateway-v2` と `idp.type: ldap` は併用できません。v2 の設定は `config/kongctl.yaml` の AI Gateway エンティティだけで、`ldap-auth-advanced` のようなプラグインを書く経路が generator にありません。

`vectordb.type: redis-stack` には `cache.type: redis-stack` が必要です。素の `redis:8` には検索モジュールが入っておらず、`none` ではコンテナ自体が存在しません。

## テスト

```bash
uv run pytest                      # L1 と L2。数十秒
uv run pytest -m slow              # L3 の実起動スモーク。KONG_LICENSE_DATA が必要
uv run python scripts/update_golden.py   # テンプレート変更後にゴールデンを更新する
```

## 今後

`target: kubernetes` は `~/customer/nksol/kubernetes` の Helm values を、`target: cloud-run` と `target: aca` は `~/customer/sony-sonpo/*/terraform` を出発点として追加する予定です。
