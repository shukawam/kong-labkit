# acme 検証環境

`env.yaml` から生成された環境です。構成を変えるときは `env.yaml` を編集して再生成してください。

## 構成

| 項目 | 値 |
|---|---|
| Gateway | ai-gateway-v2 |
| Control Plane | konnect（acme-ai-gateway / us.cp.konghq.com） |
| IdP | none |
| キャッシュ | none |
| ベクトル DB | none |
| Observability | otel-lgtm |
| 秘匿情報 | 環境変数 |
| Prompt Guard | なし |
| Prompt Compressor | なし |
| PII Sanitizer | なし |
| AI Consumer | 2 人（key-auth） |

## 前提

必要なツールは docker、mise、kongctl です。

`.env` の次の項目を埋めてから起動してください。

- `CONTROL_PLANE_ID`
- `KONNECT_PAT`
- `AZURE_OPENAI_API_KEY`
- `AI_CONSUMER_KEY_ALICE`
- `AI_CONSUMER_KEY_BOB`
- `AI_CONSUMER_KEY_BOB_TEMPORARY`

## 初手

クラスタ証明書（`.certs/cluster.crt`）と秘密鍵は generator がローカルで作成済みです。`mise run sync` は `config/kongctl.yaml` を読み、AI Gatewayの作成と、この証明書の登録を行います。秘密鍵はローカルの Data Plane で使用します。

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

## ダッシュボード

Grafana の `Kong` フォルダに、`config/grafana/dashboards/` の定義が provisioning で読み込まれます。データ源は `opentelemetry` プラグインが OTLP で送るメトリクスで、Prometheus プラグインは使いません。

| ダッシュボード | 内容 |
|---|---|
| Kong Gateway (OpenTelemetry) | Grafana 公式の [Kong Official (7424)](https://grafana.com/grafana/dashboards/7424-kong-official/) と同じ粒度。RPS、ステータスコード、Kong 内部／上流／全体のレイテンシ、帯域、共有メモリ、Nginx コネクション |
| Kong AI Gateway (OpenTelemetry) | Konnect の AI gateway dashboard 相当。モデル別／プロバイダ別の利用回数、AI ステータスコード、Consumer 別のトークン使用量とコスト、LLM レイテンシ。加えて TTFT / TPOT とキャッシュの内訳 |
| Kong AI Gateway コスト (OpenTelemetry) | コスト専用。トークン単価からの試算を主に、モデル別・Consumer 別の内訳、30 日換算、キャッシュによる節約 |

編集したものを残したいときは、Grafana の Export JSON を `config/grafana/dashboards/` に書き戻してください。ここは generator の生成物なので、恒久的に変えるなら `kong-labkit` の `generator/assets/grafana/` を直してください。

この構成は Gateway の設定を Konnect 側で持つため、`opentelemetry` は `config/kongctl.yaml` の `policies` として生成され、`mise run sync` で Konnect に反映されます。送信先は `http://otel-lgtm:4318/v1/metrics` です。Konnect の UI で手を入れる必要はありません。

## AI Consumer と API キー

全モデルに key-auth が掛かっていて、`apikey` ヘッダでキーを送ります。キーの値は `.env` の `AI_CONSUMER_KEY_<キー名>` にあり、`mise run setup`（単体なら `mise run keys`）が空のものだけを乱数で埋めます。値を変えたら `mise run sync` で Konnect に書き込んでください。キーは Konnect 側で書き込み専用なので、`.env` を失うと取り出せません。

| Consumer | グループ | キー | `.env` の変数 | 期限 |
|---|---|---|---|---|
| alice | sales | alice | `AI_CONSUMER_KEY_ALICE` | なし |
| bob | engineering | bob | `AI_CONSUMER_KEY_BOB` | なし |
| bob | engineering | bob-temporary | `AI_CONSUMER_KEY_BOB_TEMPORARY` | 作成から 3600 秒 |

`ai-rate-limiting-advanced` をグループ単位で掛けています。数えるのは入力と出力の合計トークン（`total_tokens`）で、窓は固定です。上限は応答を受け取った後に加算されるので、上限をまたぐ 1 回は通り、次のリクエストから 429 になります。

| グループ | 上限 |
|---|---|
| sales | 60 秒あたり 10000 トークン |

```bash
curl http://localhost:8000/v1/<model>/chat/completions \
  -H "apikey: $AI_CONSUMER_KEY_ALICE" -H 'Content-Type: application/json' \
  -d '{"model":"<model>","messages":[{"role":"user","content":"ping"}]}'
```

OpenAI SDK の `api_key` は `Authorization: Bearer` で送られますが、key-auth はこのヘッダを読みません。SDK から呼ぶときは `api_key` にダミーを入れ、`default_headers={"apikey": ...}` でキーを渡してください。

期限付きのキーの `ttl` は「作成時点からの残り秒数」で、Konnect の credential には更新 API がありません。期限はデータプレーンがリクエストのたびに判定し、切れたキーは 401 になります。

Consumer 別の利用量は Konnect の Observability > Explorer で見られます。From を「LLM usage」、by を「AI gateway consumer」（グループ単位なら「AI gateway consumer group」）にすると、Consumer 別のトークン数とコストが出ます。Grafana の Consumer 別パネルは `kong.auth.consumer.name`（AI Consumer の名前）で集計しています。AI Consumer Group は OTel の属性に出ないので、Grafana ではグループ単位に畳めません。

## トラブルシュート

**データプレーンが Konnect に繋がらない。** `mise run sync` が成功し、`.certs/cluster.crt` が Konnect に登録されているか確認してください。クラスタ証明書を `mise run certs` で再生成したときは、`mise run sync` で再登録してから `docker compose restart` で Data Plane に証明書を読み直させてください。

**`CONTROL_PLANE_ID` が空のまま起動した。** `KONG_CLUSTER_CONTROL_PLANE` が `.us.cp.konghq.com:443` という形になり、名前解決に失敗します。`.env` を埋めて `mise run reset` してください。

**`.env` のプロバイダ秘密を変えたら `mise run sync` し直す。** kongctl は書き込み専用フィールド（秘密）の値を比較しないため、生成物の `sync` / `diff` は常に `--write-secrets` を付けて `.env` の秘密を書き込みます。`mise run diff` の `secret write` がその対象です。Bedrock の `{"message":"The security token included in the request is invalid."}` は token が Konnect に届いていない状態、`... is expired` は届いているが期限切れの状態です。`config/kongctl.yaml` を手で `kongctl sync` するときも `--write-secrets` を付けてください。付けない更新は azure の `api-key` ヘッダを消します（kongctl 1.20.2 で実測）。

**Grafana にメトリクスが 1 つも出ない。** `opentelemetry` プラグインは「その値が最後に更新された時刻」を datapoint のタイムスタンプに使います。起動時に一度だけ立つゲージ（`kong.cp.connection.status` など）は時間とともに古くなり、Prometheus に `too old sample` で弾かれます。OTLP は 1 点でも弾かれるとリクエスト全体が 400 になるため、Kong のメトリクスが丸ごと落ちます。`config/otel-lgtm/otelcol-config.yaml` の `transform/kong` プロセッサが送信時刻で上書きしてこれを避けているので、このマウントを外さないでください。`docker compose logs otel-lgtm | grep "Exporting failed"` で確認できます。

**Loki にログが出ない、または飛び飛びになる。** Kong のアクセスログは 1 件で構造化メタデータを 150 個以上持ちます（`gen_ai.*` と全リクエスト/レスポンスヘッダ）。Loki の既定上限は 128 で、超えると `has too many structured metadata labels` で 400 を返します。OTLP は全か無かなので、そのバッチに同梱された内部ログまで丸ごと捨てられ、ログが飛び飛びに見えます。compose の `LOKI_EXTRA_ARGS` で上限を上げてあるので、この環境変数を外さないでください。`docker compose logs otel-lgtm | grep "Exporting failed"` で確認できますが、otel-lgtm は既定で各コンポーネントのログを捨てるため、`ENABLE_LOGS_ALL=true` を付けて起動する必要があります。

**ダッシュボードが Grafana に現れない。** `config/grafana/dashboards.yaml` の `path` は `conf/provisioning` の下を指す必要があります。Grafana の `permitted_provisioning_paths` がそれ以外を拒否します。
