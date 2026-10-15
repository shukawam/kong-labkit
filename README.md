# kong-labkit

Generate reproducible Kong environments from a single YAML file.

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

`mise run generate acme` は `uv run generator/gen.py customer/acme.yaml -o ~/customer/acme` を実行します。これらのコマンドは kong-labkit プロジェクト内で実行してください。

```bash
mise run generate acme --out ~/customer/acme-test  # 出力先を変更
mise run generate acme --force                    # 既存の環境を更新
mise run generate --help                         # 引数とオプションを表示
```

既存の環境にテンプレートの改善を再適用するときは `--force` を付けます。生成先が git 管理下で未コミットの変更があるときは拒否されるので、先に commit するか stash してください。`.env` と `.certs/` と `docs/` は `--force` でも上書きされません。顧客ごとの入力を置く `customer/` は、このリポジトリでは git 管理対象外です。

すべての構成で `config/kongctl.yaml` を生成します。Konnect 構成では `mise run sync` が Control Plane（AI Gateway v2 では AI Gateway）を作成し、generator がローカルで作成した `.certs/cluster.crt` を登録します。API Gateway と AI Gateway v1 の Gateway 設定は、kongctl の decK 連携で適用します。self-managed 構成では `kongctl.yaml` に Konnect リソースを定義せず、Control Plane は Docker Compose、Gateway 設定は decK で管理します。

## 例

`examples/` には gateway × control_plane の組み合わせごとに minimal を置いてあります。まずこれを写して、必要な機能だけ足していくのが想定している使い方です。

各 example の先頭には `# yaml-language-server: $schema=../schemas/env.schema.json` が入っています。`schemas/env.schema.json` は `generator/schema.py` から生成した JSON Schema で、`customer/` も `examples/` と同階層なのでコピーしたファイルでもそのまま補完と検証が効きます。表せるのはキーと値の候補までで、`ai-gateway-v2` × `self-managed` のような組み合わせの検証は生成時に行われます。スキーマを変えたら `uv run python scripts/update_schema.py` で再生成してください（忘れるとテストが落ちます）。

| ファイル | gateway | control_plane |
|---|---|---|
| `aigw-v2-konnect-minimal.yaml` | `ai-gateway-v2` | `konnect` |
| `aigw-v1-konnect-minimal.yaml` | `ai-gateway-v1` | `konnect` |
| `aigw-v1-self-managed-minimal.yaml` | `ai-gateway-v1` | `self-managed` |
| `apigw-konnect-minimal.yaml` | `api-gateway` | `konnect` |
| `apigw-self-managed-minimal.yaml` | `api-gateway` | `self-managed` |

minimal からの差分が 1 テーマに絞られた応用例もあります。

| ファイル | minimal との差分 |
|---|---|
| `aigw-v2-konnect-redis-stack.yaml` | semantic cache を redis-stack で有効にする |
| `aigw-v2-konnect-prompt-guard.yaml` | ai-prompt-guard を観測モードで掛ける |
| `aigw-v1-konnect-lldap.yaml` | lldap を IdP として同梱する |
| `aigw-v1-konnect-vault.yaml` | 秘匿情報のバックエンドを Vault にする |
| `apigw-self-managed-keycloak.yaml` | Keycloak を IdP として同梱する |

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
| `ai.prompt_guard` | `none` / `observe` / `block` | `none` | `ai-prompt-guard` を全ルートに掛ける。AI Gateway 専用。`observe` は `ai-gateway-v2` のみ |
| `idp.type` | `keycloak` / `entra-id` / `ldap` / `none` | `none` | `keycloak` は `idp.realm` が必須。`ldap` は lldap を同梱し `ai-gateway-v2` とは併用不可 |
| `idp.users[]` | `name` / `email` / `password` / `groups[]` | `tester` 1 名 | `ldap` のときだけ。グループは `users[].groups` から導出。`email` は `<name>@example.com`、`password` は `<name>-password` が既定 |
| `cache.type` | `redis` / `redis-stack` / `none` | `none` | |
| `vectordb.type` | `redis-stack` / `pgvector` / `none` | `none` | `redis-stack` は `cache.type: redis-stack` が必須 |
| `observability.backend` | `otel-lgtm` / `newrelic` / `none` | `otel-lgtm` | `newrelic` は素の OTel Collector を同梱し、Grafana とダッシュボードは生成しない |
| `observability.newrelic.region` | `us` / `eu` | `us` | `backend: newrelic` のときだけ指定できる |
| `secrets.backend` | `env` / `vault` | `env` | `vault` は Vault を同梱し、kong.yaml の秘密を `{vault://hcv/...}` 参照にする。`ai-gateway-v2` とは併用不可 |
| `upstream` | `httpbin` / `none` | `api-gateway` なら `httpbin` | |

## 弾かれる組み合わせ

`ai-gateway-v2` と `self-managed` は併用できません。AI Gateway v2 のデータプレーンは `KONG_KONNECT_MODE: on` を前提にしており、自前の Control Plane に繋ぐ構成が存在しないためです。

`semantic_cache` または `semantic_routing` を有効にして `vectordb.type: none` にはできません。semantic balancer は vectordb ブロックが必須で、未設定のまま sync すると kongctl が失敗します。

`ai-gateway-v2` と `idp.type: ldap` は併用できません。v2 の設定は `config/kongctl.yaml` の AI Gateway エンティティだけで、`ldap-auth-advanced` のようなプラグインを書く経路が generator にありません。

`vectordb.type: redis-stack` には `cache.type: redis-stack` が必要です。素の `redis:8` には検索モジュールが入っておらず、`none` ではコンテナ自体が存在しません。

`secrets.backend: vault` と `ai-gateway-v2` は併用できません。v2 の設定は `config/kongctl.yaml` の AI Gateway エンティティだけで、そこが vault 参照を受け付けるか検証できていないためです。

`ai.prompt_guard: observe` は `ai-gateway-v2` でしか使えません。検知しても通す `continue_on_detection` が `kong-gateway:3.16` の `ai-prompt-guard` に存在せず、v1 では必ずブロックになるためです。v1 で掛けたいときは `block` を指定してください。

`ai.prompt_guard` は `api-gateway` では使えません。`ai-prompt-guard` は LLM リクエストの本文を検査するプラグインですが、`api-gateway` の `upstream` は `httpbin` か `none` しか選べず、検査する相手が居ません。

## テスト

```bash
uv run pytest                      # L1 と L2。数十秒
uv run pytest -m slow              # L3 の実起動スモーク。KONG_LICENSE_DATA が必要
uv run python scripts/update_golden.py   # テンプレート変更後にゴールデンを更新する
```

## 今後

`target: kubernetes` は `~/customer/nksol/kubernetes` の Helm values を、`target: cloud-run` と `target: aca` は `~/customer/sony-sonpo/*/terraform` を出発点として追加する予定です。
