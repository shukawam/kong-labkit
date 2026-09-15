# acme 検証環境

`env.yaml` から生成された環境です。構成を変えるときは `env.yaml` を編集して再生成してください。

## 構成

| 項目 | 値 |
|---|---|
| Gateway | ai-gateway-v1 |
| Control Plane | konnect（acme-ai-gateway / us.cp.konghq.com） |
| IdP | ldap（lldap / dc=acme,dc=local） |
| キャッシュ | none |
| ベクトル DB | none |
| Observability | otel-lgtm |
| 秘匿情報 | HashiCorp Vault |

## 前提

必要なツールは docker、mise、kongctl、deck です。

`.env` の次の項目を埋めてから起動してください。

- `CONTROL_PLANE_ID`
- `KONNECT_PAT`
- `AZURE_OPENAI_API_KEY`

## 初手

クラスタ証明書（`.certs/cluster.crt`）と秘密鍵は generator がローカルで作成済みです。`mise run sync` は `config/kongctl.yaml` を読み、Control Planeの作成と、この証明書の登録を行います。続けて kongctl の decK 連携で `config/kong/kong.yaml` の Gateway 設定を適用します。秘密鍵はローカルの Data Plane で使用します。

```bash
# Konnect の Personal Access Token を発行し（Konnect > 右上のアカウント > Personal Access Tokens）、.env の KONNECT_PAT に書く
mise run setup                     # acme-ai-gateway を作成し、CONTROL_PLANE_ID を .env に書いて環境を起動する
mise run smoke                     # Data Plane が Konnect に繋がるまで数十秒かかる
```

`mise run setup` は `.certs/cluster.crt` が無ければ作り、`mise run sync` 相当を実行し、作成された Control Plane のエンドポイントから `CONTROL_PLANE_ID` を取り出して `.env` の該当行を書き換えてから `docker compose up -d` します。個別に叩きたいときは `mise run diff` で作成内容を確認し、`mise run sync` と `mise run up` を分けて実行できます。

## エンドポイント

| 用途 | URL |
|---|---|
| Proxy | http://localhost:8000 |
| Status API | http://localhost:8100/status |
| Grafana | http://localhost:3000 |
| lldap 管理 UI | http://localhost:17170 |
| LDAP | localhost:3890 |
| Vault | http://localhost:8200 |

## ダッシュボード

Grafana の `Kong` フォルダに、`config/grafana/dashboards/` の定義が provisioning で読み込まれます。データ源は `opentelemetry` プラグインが OTLP で送るメトリクスで、Prometheus プラグインは使いません。

| ダッシュボード | 内容 |
|---|---|
| Kong Gateway (OpenTelemetry) | Grafana 公式の [Kong Official (7424)](https://grafana.com/grafana/dashboards/7424-kong-official/) と同じ粒度。RPS、ステータスコード、Kong 内部／上流／全体のレイテンシ、帯域、共有メモリ、Nginx コネクション |
| Kong AI Gateway (OpenTelemetry) | Konnect の AI gateway dashboard 相当。モデル別／プロバイダ別の利用回数、AI ステータスコード、Consumer 別のトークン使用量とコスト、LLM レイテンシ。加えて TTFT / TPOT とキャッシュの内訳 |
| Kong AI Gateway コスト (OpenTelemetry) | コスト専用。トークン単価からの試算を主に、モデル別・Consumer 別の内訳、30 日換算、キャッシュによる節約 |

編集したものを残したいときは、Grafana の Export JSON を `config/grafana/dashboards/` に書き戻してください。ここは generator の生成物なので、恒久的に変えるなら `kong-labkit` の `generator/assets/grafana/` を直してください。

## Vault

秘匿情報は `config/kong/kong.yaml` に実値として載らず、`{vault://hcv/kong/.../...}` という参照として書かれます。解決するのはデータプレーンで、`KONG_VAULT_HCV_*` で指定した Vault に実行時に問い合わせます。Konnect 側にも実値は保存されません。

`vault-bootstrap` が `vault` の起動後に一度だけ走り、次の値を KV に投入します。

| パス | キー | 出どころ |
|---|---|---|
| `secret/kong/ldap` | `ldap_password` | ラボ固定値 |
| `secret/kong/ai-azure` | `header_value` | `.env` の `AZURE_OPENAI_API_KEY` |

OpenAI の `header_value` だけは `Bearer ` を前置した完成形を格納します。Kong の vault 参照は値の一部に埋め込んでも解決されず、フィールドの値全体が参照である必要があるためです。

Vault の UI は http://localhost:8200 で、トークンは `.env` の `VAULT_TOKEN` です。

## LDAP のユーザー

`config/lldap/` の内容を `lldap-bootstrap` が投入します。変更するときは `env.yaml` の `idp.users[]` を編集して再生成してください。

| ユーザー | パスワード | 所属グループ |
|---|---|---|
| tester | tester-password | acme-ai-users |

## トラブルシュート

**データプレーンが Konnect に繋がらない。** `mise run sync` が成功し、`.certs/cluster.crt` が Konnect に登録されているか確認してください。クラスタ証明書を `mise run certs` で再生成したときは、`mise run sync` で再登録してから `docker compose restart` で Data Plane に証明書を読み直させてください。

**`CONTROL_PLANE_ID` が空のまま起動した。** `KONG_CLUSTER_CONTROL_PLANE` が `.us.cp.konghq.com:443` という形になり、名前解決に失敗します。`.env` を埋めて `mise run reset` してください。

**LDAP 認証の通し方。** `header_type: ldap` を指定しているため、`curl -H 'Authorization: ldap dGVzdGVyOnRlc3Rlci1wYXNzd29yZA=='` で認証情報を送ります。ヘッダーの値は `tester:tester-password` を Base64 化したものです。`curl -u` は Basic 認証を送るため、この設定では使えません。bind は `cn=admin,ou=people,dc=acme,dc=local` で行い、`ou=people,dc=acme,dc=local` を `uid` で検索します。lldap の管理 UI には `admin` / `.env` の `LLDAP_ADMIN_PASSWORD` でログインできます。

**テストユーザーが存在しない。** `lldap-bootstrap` は `config/lldap/` の JSON を投入する使い捨てのコンテナで、`lldap` が healthy になってから 1 度だけ走ります。`docker compose logs lldap-bootstrap` を確認してください。このスクリプトは実行時に `jq` と `jo` を apk で取得するため、起動時にネットワークが必要です。投入内容を変えたときは `docker compose up -d --force-recreate lldap-bootstrap` で流し直せます。

**LDAP では通るはずのユーザーが 403 になる。** `consumer_optional: false` にしてあるため、`config/kong/kong.yaml` の `consumers` に無いユーザーは弾かれます。`env.yaml` の `idp.users[]` に足せば、lldap のユーザーと Consumer が一緒に生成されます。`true` にすれば通るようになりますが、`ldap-auth-advanced` は `consumer_optional` が true のとき Consumer の解決自体を行わないので、Consumer 単位のダッシュボードが全て空になります。

**グループでアクセスを分けたい。** `ldap-auth-advanced` は認証したユーザーの LDAP グループを ACL に載せるので、`config/kong/kong.yaml` のルートに `acl` プラグインを足し、`allow` に `acme-ai-users` を並べれば経路ごとに通すグループを絞れます。どのグループをどの経路に許すかは構成ごとの判断なので、generator では生成していません。

**認証後に 502 になる／AI Proxy Advanced が見当たらない。** `.env` のプロバイダ用 API キーが空だと、`ai-proxy-advanced` の作成がスキーマ検証で失敗します。decK の適用は一括でロールバックされないため、Service や LDAP プラグインだけが残ることがあります。`mise run setup`・`sync`・`diff` は必要な環境変数が空なら処理を開始せずに停止します。キーを設定して `mise run diff` で差分を確認し、`mise run sync` で再適用してください。ログの接続先が `127.0.0.1:32000` なら、AI Proxy が仮の Service URL を差し替えていません。

**疎通テストの範囲。** `mise run smoke` は無認証の 401 と、LDAP 認証付きチャットの 200 を確認します。上流 LLM の有効な API キーとデプロイが必要で、テスト時に LLM を呼び出します。401 以外でも、502 などのエラーは失敗になります。

**認証だけが失敗する／上流が 401 を返す。** `docker compose logs | grep "unable to resolve reference"` を見てください。Vault の参照が解決できていないと、Kong は参照文字列そのものを値として使うため、資格情報が誤っているのと同じ挙動になります。`KONG_VAULT_HCV_KV` を `v2` から変えると全参照が壊れます（Kong 側の既定は `v1` ですが、dev モードの Vault は KV v2 です）。

**Vault を再起動したら認証が通らなくなった。** 同梱の Vault は dev モードでメモリ保持のため、再起動で KV の中身が消えます。`docker compose up -d --force-recreate vault-bootstrap` で投入し直してください。

**Grafana にメトリクスが 1 つも出ない。** `opentelemetry` プラグインは「その値が最後に更新された時刻」を datapoint のタイムスタンプに使います。起動時に一度だけ立つゲージ（`kong.cp.connection.status` など）は時間とともに古くなり、Prometheus に `too old sample` で弾かれます。OTLP は 1 点でも弾かれるとリクエスト全体が 400 になるため、Kong のメトリクスが丸ごと落ちます。`config/otel-lgtm/otelcol-config.yaml` の `transform/kong` プロセッサが送信時刻で上書きしてこれを避けているので、このマウントを外さないでください。`docker compose logs otel-lgtm | grep "Exporting failed"` で確認できます。

**ダッシュボードが Grafana に現れない。** `config/grafana/dashboards.yaml` の `path` は `conf/provisioning` の下を指す必要があります。Grafana の `permitted_provisioning_paths` がそれ以外を拒否します。

**AI 系のパネルだけ空になる。** `ai-proxy-advanced` の `logging.log_statistics` は既定が false で、false のままだと `gen_ai.*` / `kong.gen_ai.*` メトリクスが一切出ません。generator は target ごとに true を入れています。なお `logging` はプラグインの直下ではなく target の下に書く必要があり、直下に書くと Konnect が `validation error: unknown field` を返して sync 全体が失敗します（kongctl はこれを成功として表示することがあるので、`deck gateway diff` で確かめてください）。

**コストが 0 のまま。** 2 つ原因があります。1 つは `env.yaml` の `ai.providers[].models[].input_cost` / `output_cost`（100 万トークンあたりの USD）が未設定なこと。もう 1 つは、設定していても Kong が `kong.gen_ai.llm.cost` を OTLP 上で整数として送るため、累計が 1 ドルを超えるまで 0 に丸められることです。金額はコスト用ダッシュボードの「コスト試算（トークン単価ベース）」を見てください。こちらはトークン数と単価から計算するので端数が落ちません。

**Consumer 別のパネルが空。** `kong.auth.consumer.name` 属性は、認証プラグインがリクエストを Consumer に紐付けたときだけ入ります。`ldap-auth-advanced` は `consumer_optional` が true だと Consumer の解決を丸ごと省くので、generator は false を設定しています。変更したときは `docker compose logs gateway | grep "consumer found"` で紐付いているか確認してください。なお `cache_ttl` の間は資格情報がキャッシュされるため、設定を変えた直後のリクエストは古い判定のままになることがあります。
