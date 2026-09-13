# 顧客デリバリー用 再現環境ジェネレータ 設計

## 目的

顧客へのデリバリーが始まった時点で、検証環境を素早く再現できるようにする。YAML で構成を宣言的に定義し、`uv run generator/gen.py <env.yaml> -o <dir>` を実行すると、そのまま `mise run up` が通る一通りのファイル群が生成される。

既存の `~/customer/*` 配下には良い出発点が蓄積されているが、新しい案件ごとに過去の環境から手でコピーして書き換えている。この作業の中で失われやすいのは、コンポーネント間の配線値と、過去に踏んだ罠への対処（`KONG_ROUTER_FLAVOR: expressions`、X-Forwarded-For 周りの `KONG_TRUSTED_IPS`、Redis Stack でないと vectordb として機能しないこと）である。ジェネレータの価値は雛形のコピーではなく、これらをテンプレートとスキーマ制約として固定することにある。

## スコープ

### v1

`target: compose` のみを実装する。以下の組み合わせを全て通す。

- Gateway 3 種: AI Gateway v2 / AI Gateway v1 / API Gateway
- 制御面 2 種: Konnect / self-managed（ただし AI Gateway v2 は Konnect のみ）
- IdP 3 種: Keycloak / Entra ID / なし
- キャッシュ 3 種: Redis / Redis Stack / なし
- ベクトル DB 3 種: Redis Stack / pgvector / なし
- observability: otel-lgtm（既定有効）

`target` はスキーマに最初から含め、`kubernetes` / `cloud-run` / `aca` は明示的に unsupported エラーを返す。

### v2 以降

v2 で `kubernetes`（Helm values ベース、`~/customer/nksol/kubernetes` が出発点）、v3 で `cloud-run` と `aca`（Terraform ベース、`~/customer/sony-sonpo/*/terraform` が出発点）を追加する。

### スコープ外

顧客固有の調査メモや回答文書の生成は行わない。`docs/` ディレクトリは `.gitkeep` のみを置いて作成し、中身は案件ごとに人間が書く。

## 実装形態

Python CLI + Jinja2。`uv` の single-file script（PEP 723）形式とし、`uv run generator/gen.py` で依存解決から実行まで完結する。依存は `pydantic`、`jinja2`、`pyyaml`、`click`。

Jinja2 を選ぶ理由は、既存ファイルの資産性を壊さないことにある。`x-default: &default` アンカーと、値の傍に書かれた why コメントは手書きの蓄積であり、Python の dict から `yaml.dump` で生成すると全て失われる。Jinja2 ならテンプレートが出力とほぼ同形なので、新しい罠を踏んだときに生成物を直してテンプレートに戻す作業が短く済む。

## 入力スキーマ

```yaml
customer: smbc                    # 生成ディレクトリ名・namespace・CP 名の素
target: compose                   # compose | kubernetes | cloud-run | aca（v1 は compose のみ）
gateway: ai-gateway-v2            # ai-gateway-v2 | ai-gateway-v1 | api-gateway
control_plane: konnect            # konnect | self-managed
region: us                        # Konnect リージョンのみに効く。control_plane: self-managed では無視される

ai:                               # gateway が ai-gateway-* のときだけ有効
  providers:
    - type: azure                 # azure | bedrock | vertex | openai | anthropic
      instance: shukawam-ai-foundry-resource
      auth: api-key               # api-key | managed-identity
      models:
        - name: gpt-5-6
          deployment_id: gpt-5.6
          api_version: 2024-12-01-preview
  semantic_cache: true
  semantic_routing: false

idp:
  type: keycloak                  # keycloak | entra-id | none
  realm: smbc                     # keycloak のときのみ

cache:
  type: redis-stack               # redis | redis-stack | none

vectordb:
  type: pgvector                  # redis-stack | pgvector | none

observability:
  otel_lgtm: true

upstream: httpbin                 # httpbin | none（api-gateway のとき既定 httpbin）
```

### 検証ルール

pydantic のモデルバリデータで以下を強制する。エラーメッセージは「何がダメか」ではなく「どう直すか」を出す。

1. `gateway: ai-gateway-v2` と `control_plane: self-managed` の併用はエラー。AI Gateway v2 のデータプレーンは `KONG_KONNECT_MODE: on` 前提で、自前 CP に繋ぐ構成が存在しない。弾かないと、起動直後に死ぬだけの成果物が生成される。
2. `ai.semantic_cache: true` または `ai.semantic_routing: true` のとき `vectordb.type: none` はエラー。v2 の `balancer.algorithm: semantic` は `vectordb` ブロックが必須で、埋まっていない設定は kongctl の sync 時点で落ちる。
3. `vectordb.type: redis-stack` のとき `cache.type` が `redis-stack` 以外（`redis` または `none`）であればエラー。素の `redis:8` には検索モジュールが入っておらず vectordb として指定すると実行時に失敗し、`none` では参照先のコンテナ自体が存在しない。`cache.type: redis-stack` なら 1 コンテナが両役を兼ねる。
4. `idp.type: entra-id` のとき、`.env` の `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET` を空欄で生成し、警告を出して続行する。テナント側の app registration は手作業なので、ここで止めると使い物にならない。

`target` が `compose` 以外のときは unsupported として停止する。

## 生成物のレイアウト

`gateway: ai-gateway-v2` / `control_plane: konnect` / `idp: keycloak` / `cache: redis-stack` / `vectordb: redis-stack` の場合。

```
<out>/
├── compose.yaml              # kong + redis-stack + keycloak + otel-lgtm
├── .env                      # PREFIX, AZURE_OPENAI_API_KEY=（空欄）, KEYCLOAK_*
├── .env.example              # 同内容で値は空。git に乗る側
├── .gitignore                # .env, .certs/, .venv/
├── mise.toml                 # タスク群と [env] mise.file = ".env"
├── README.md                 # この環境固有の初手。生成時の選択が埋まった状態
├── env.yaml                  # 入力 YAML のコピー
├── .certs/
│   ├── cluster.crt           # gen 時に openssl で生成
│   └── cluster.key
├── config/
│   ├── kongctl.yaml          # ai_gateways / model_providers / models / data_plane_certificates
│   └── keycloak/
│       └── realm-export.json # client + テストユーザ + scope 入り
└── docs/
    └── .gitkeep
```

`env.yaml` を生成先にコピーするのは、半年後に「この環境はどのオプションで作ったのか」を compose.yaml から逆算する作業を消すため。再生成時の入力にもそのまま使える。

`gateway: api-gateway` / `control_plane: self-managed` では `config/kong/kong.yaml`（decK 用）と `config/kong/certs/`（CP-DP 間の shared mTLS 証明書）に変わり、compose に `database` / `kong-bootstrap` / `kong-cp` / `kong-dp` が入る。`.certs/` は Konnect 用なので生成されない。ディレクトリ構造が分岐するのは `config/` 配下のみで、トップレベルは全構成で同一にする。

生成されるファイルは、外部クレデンシャル（`.env` の API キー類）だけが空欄で、それ以外の配線値は全て埋まった状態にする。

## mise タスク

タスク名は全顧客で統一する。どの環境に入っても `mise run up` で上がる状態を保証する。

| タスク | 内容 |
|---|---|
| `up` | `docker compose up -d` |
| `down` | `docker compose down` |
| `reset` | `docker compose down -v && docker compose up -d` |
| `certs` | openssl で `.certs/` を再生成 |
| `sync` | Konnect: `kongctl sync konnect -f config/kongctl.yaml --auto-approve` / self-managed: `deck gateway sync` |
| `diff` | Konnect: `kongctl diff konnect -f config/kongctl.yaml` / self-managed: `deck gateway diff` |
| `logs` | `docker compose logs -f`（引数でサービス指定） |
| `smoke` | curl で主要経路を叩き、非 200 なら非ゼロ終了 |

`sync` と `diff` は control_plane によって中身だけが差し替わり、名前は変わらない。

## ジェネレータの構造

```
generator/
├── gen.py                    # CLI。引数処理、出力、--force 判定のみ
├── schema.py                 # pydantic モデルと検証
├── context.py                # EnvSpec → 描画コンテキスト。配線解決の全責任
├── certs.py                  # openssl でクラスタ証明書
└── templates/
    ├── compose.yaml.j2            # 骨格。x-default アンカー、networks、include の列
    ├── services/
    │   ├── kong-aigw-v2.yaml.j2
    │   ├── kong-dp.yaml.j2        # ai-gateway-v1 / api-gateway の Konnect データプレーン
    │   ├── kong-self-managed.yaml.j2   # database + bootstrap + cp + dp の 4 サービス
    │   ├── keycloak.yaml.j2
    │   ├── redis.yaml.j2          # mode で image を分岐
    │   ├── pgvector.yaml.j2
    │   ├── otel-lgtm.yaml.j2
    │   └── httpbin.yaml.j2
    ├── config/
    │   ├── kongctl.yaml.j2        # AI Gateway v2 エンティティ
    │   ├── kong.yaml.j2           # ai-gateway-v1 / api-gateway の decK 宣言的設定
    │   └── realm-export.json.j2
    ├── mise.toml.j2
    ├── README.md.j2
    ├── env.j2
    └── gitignore.j2
examples/
└── *.yaml                    # 代表構成の env.yaml。L2 のゴールデン入力を兼ねる
tests/
README.md                     # ジェネレータ自身の使い方とオプション一覧
```

### 責務の分離

テンプレートの責務は YAML の形を持つことだけに限る。`{% if %}` はサービスブロックを出すか出さないかのレベルに留め、値を決める判断は `context.py` に集約する。`{% if idp.type == 'keycloak' %}http://keycloak:8080/realms/{{ idp.realm }}{% endif %}` のような式はテンプレートに書かず、`ctx.idp.issuer` を確定させてから渡す。分岐がテンプレートと Python に散ると、値の決定箇所を両方読む必要が出る。

### 配線値

`context.py` が解決する値。

| コンテキスト | 値 | 依存 |
|---|---|---|
| `ctx.kong.image` | `kong/kong-ai-gateway:2.0.1` / `kong/kong-gateway:3.14` | gateway |
| `ctx.kong.konnect_host` | `{prefix}.{region}.cp.konghq.com` | control_plane, region |
| `ctx.idp.issuer` | `http://keycloak:8080/realms/{realm}` または Entra の v2.0 エンドポイント | idp |
| `ctx.otel.endpoint` | `http://otel-lgtm:4318` | observability |
| `ctx.vector.host` / `ctx.vector.port` | `redis:6379` または `pgvector:5432` | vectordb |
| `ctx.vector.dimensions` | 埋め込みモデル名から決定（text-embedding-3-large なら 3072） | ai.providers |
| `ctx.ports` | ポート割り当て表 | 全コンポーネント |

### ポート割り当て

`context.py` に集約する。`control_plane: self-managed` は Kong のメタストア用に `postgres:5432` を使うため、`vectordb: pgvector` が乗ると 5432 が二重になる。この場合 pgvector を 5433 に退避させる。同様に self-managed は 8001 / 8002 を Admin API と Manager に使うため、他のコンポーネントを衝突させない。衝突が解消できない場合はエラーで停止する。起動して初めて気づく衝突をテンプレートの `{% if %}` で防ぐことはできないため、Python 側で割り当て表を持つ。

### Jinja2 環境設定

`undefined=StrictUndefined` を有効にする。既定では未定義変数が空文字になり、`KONG_CLUSTER_CONTROL_PLANE: .us.cp.konghq.com:443` のような一見正しい形の壊れた設定が黙って生成される。StrictUndefined なら生成時点で落ちる。

`trim_blocks` と `lstrip_blocks` を有効にして、`{% if %}` の行が空行として残らないようにする。`keep_trailing_newline` も有効にする。

インデントは `compose.yaml.j2` 側で `{% filter indent(2) %}{% include 'services/redis.yaml.j2' %}{% endfilter %}` と吸収し、各パーシャルはネストされることを知らない（インデント 0 で書く）。パーシャルを単体で `yaml.safe_load` できるため、テストが書きやすい。

## テスト戦略

### L1: スキーマ検証

pytest、依存なし、1 秒未満。検証ルール 4 件それぞれについて、それを踏む `env.yaml` で期待するエラーが出ることを確認する。`ai-gateway-v2` × `self-managed`、`semantic_cache: true` × `vectordb: none`、`vectordb: redis-stack` × `cache: redis` が異常系。`entra-id` は警告のみで続行するため正常系。

エラーメッセージの文面まで assert する。「どう直すか」を書いた文面は書いた直後は自明で後から劣化するため、テストで固定する。

### L2: 組み合わせ生成テスト

pytest、Docker 必須、数十秒。有効な組み合わせを総当たりで生成し、各生成物について次の 2 点を確認する。

1. 生成された全 YAML が `yaml.safe_load` を通る
2. `docker compose config` が成功する

`docker compose config` を中核に置く。コンテナを起動せずに compose ファイルを検証し、`x-default` アンカーを展開し、`${PREFIX}` の参照を解決し、インデント崩れとサービス名の重複を拾う。パーシャルを `indent(2)` で合成する方式の最大のリスクがインデント事故であるため、それを網羅的に潰す手段として最も費用対効果が高い。

加えて、代表 3 構成についてはゴールデンファイル（期待する出力の全文）を置いて diff する。`docker compose config` は正規化した出力を返すため、アンカーとコメントが消えたことを検知できない。why コメントをテンプレート編集時に落としても L2 の 2 点は通ってしまう。代表 3 構成は次のとおり。

- AI Gateway v2 + Konnect + Redis Stack
- API Gateway + self-managed + Keycloak + pgvector
- AI Gateway v1 + Konnect + IdP なし

### L3: 実起動スモーク

生成環境を実際に起動して curl で叩く層。AI Gateway v2 は Konnect テナントとクラスタ証明書の登録、および Azure OpenAI のキーを要するため、大部分は CI に入れられず、生成物側の `mise run smoke` として手動実行する。

ただし `gateway: api-gateway` / `control_plane: self-managed` / `idp: keycloak` / `upstream: httpbin` の組み合わせは、外部クラウドのクレデンシャルを要求しない（Kong Enterprise のライセンスのみローカルに必要）。この構成は `docker compose up` から curl での 200 確認まで自動化できるため、L3 の唯一の自動テストケースとしてジェネレータのテストに含める。生成物が実際に起動して通信することを 1 つでも機械的に保証できるかどうかが、このジェネレータの信頼性を左右する。

`upstream: httpbin` は検証資産ではなく L3 を成立させる構成要素として位置づけ、`gateway: api-gateway` のとき既定で有効、`upstream: none` で無効にできる。AI Gateway 系では上流が LLM provider であるため httpbin は生成しない。

## 生成される README

生成物の README には、この環境固有の初手のみを書く。ジェネレータの使い方やオプション一覧は書かない（`template/README.md` の役割であり、複製すると二重管理になる）。

1. 構成表: `env.yaml` の選択をそのまま表にしたもの
2. 前提: この構成で必要なツール（`docker` / `mise` / Konnect なら `kongctl` / self-managed なら `deck`）と、`.env` のどの欄を埋める必要があり、値をどこから取るか
3. 初手: 実行順のコマンド列。Konnect 構成なら CP 作成 → `mise run certs` → 証明書を CP に登録 → `mise run up` → `mise run sync` → `mise run smoke`。CP 名は生成時に確定した値が埋まる
4. エンドポイント一覧: `ctx.ports` の割り当て表。pgvector が 5433 に退避した構成ではそれが書かれる
5. トラブルシュート: 構成に応じた項目のみ

トラブルシュートを構成別にするのは、時間を取られる箇所が構成ごとに違うため。Konnect なら証明書の登録漏れで DP が繋がらない件、self-managed なら `kong migrations bootstrap` 完了前に CP が起動する順序問題、Keycloak なら `KC_HOSTNAME` とブラウザから見える URL がずれて issuer 検証が落ちる件。網羅的な FAQ にはせず構成ごとに 2〜3 項目に絞る。

## 再実行と `--force`

生成先が空でない場合、`--force` なしではエラーで停止する。

### 常に保護（`--force` でも上書きしない）

`.env` / `.certs/` / `docs/`。`.env` には手で入れた API キーが入り、`.certs/` の証明書は Konnect の CP に登録済みで再生成すると DP が繋がらなくなる。`docs/` は顧客向け成果物そのもの。この 3 つを失う事故は、どのフラグの組み合わせでも起こらないようにする。

### `--force` で上書き

`compose.yaml` / `mise.toml` / `README.md` / `config/**` / `.gitignore` / `.env.example` / `env.yaml`。テンプレート改善を既存環境に再適用することが `--force` の主目的である。

### `--force` の実行条件

`config/keycloak/realm-export.json` は上書き対象だが、Keycloak の管理画面で触って export し直した内容を失う危険がある。これに対する保護は git に預ける。生成先が git リポジトリで未コミットの変更がある場合、`--force` を拒否し「commit か stash してから実行してください」と出して停止する。git 管理下でない場合は上書き対象のファイル一覧を警告として出して続行する。ジェネレータ側で差分管理を実装するより確実で、実装も十数行で済む。

### 実行結果の出力

`gen.py` は実行のたびに、作成したファイル・上書きしたファイル・保護してスキップしたファイルの 3 分類でパスを列挙する。件数ではなくパスを出すことで、`--force` の後に何が変わったかを git diff の前に把握できる。

## 参照した既存環境

| 出発点 | 参照先 |
|---|---|
| Konnect DP の compose とアンカー構成 | `~/customer/smbc/ai-gateway/compose.yaml` |
| self-managed CP+DP、Keycloak、otel-lgtm | `~/customer/nksol/compose.yaml` |
| AI Gateway v2 エンティティ（semantic balancer、vectordb） | `~/customer/smbc/ai-gateway/kongctl.yaml` |
| AI Gateway v2 エンティティ（auth_strategies、managed identity） | `~/customer/sony-sonpo/ai-gateway/config/kongctl.yaml` |
| mise タスクと `[env] mise.file` | `~/customer/smbc/mise.toml` |
| otel-collector を挟む構成 | `~/customer/smbc/api-gateway/compose.yaml` |
| K8s Helm values（v2 で使用） | `~/customer/nksol/kubernetes/` |
| ACA / Cloud Run の Terraform（v3 で使用） | `~/customer/sony-sonpo/*/terraform/` |
