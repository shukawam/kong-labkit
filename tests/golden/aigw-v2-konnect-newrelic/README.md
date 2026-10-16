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
| Observability | New Relic（https://otlp.nr-data.net） |
| 秘匿情報 | 環境変数 |
| Prompt Guard | なし |
| Prompt Compressor | なし |

## 前提

必要なツールは docker、mise、kongctl です。

`.env` の次の項目を埋めてから起動してください。

- `NEW_RELIC_LICENSE_KEY`
- `CONTROL_PLANE_ID`
- `KONNECT_PAT`
- `AZURE_OPENAI_API_KEY`

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

## New Relic

`otel-collector`（`otel/opentelemetry-collector-contrib`）が OTLP を受け、`https://otlp.nr-data.net` へ中継します。Kong 側の送信先はこの Collector で、New Relic のライセンスキーは Collector だけが持ちます。

`.env` の `NEW_RELIC_LICENSE_KEY` に Ingest License Key（New Relic > 右上のアカウント > API keys > Ingest - License）を入れてから `mise run up` してください。データは New Relic の APM & Services、Metrics Explorer、Logs に `kong` 由来のエンティティとして現れます。

送信先リージョンを変えるときは `env.yaml` の `observability.newrelic.region`（`us` / `eu`）を変えて再生成してください。中継の設定を直に触りたいときは `config/otel-collector/config.yaml` を編集します。

## トラブルシュート

**データプレーンが Konnect に繋がらない。** `mise run sync` が成功し、`.certs/cluster.crt` が Konnect に登録されているか確認してください。クラスタ証明書を `mise run certs` で再生成したときは、`mise run sync` で再登録してから `docker compose restart` で Data Plane に証明書を読み直させてください。

**`CONTROL_PLANE_ID` が空のまま起動した。** `KONG_CLUSTER_CONTROL_PLANE` が `.us.cp.konghq.com:443` という形になり、名前解決に失敗します。`.env` を埋めて `mise run reset` してください。

**New Relic にメトリクスが 1 つも出ない。** まず `docker compose logs otel-collector | grep -i "permanent error"` を見てください。401 ならライセンスキー（`NEW_RELIC_LICENSE_KEY`）が空か、User Key を入れています。必要なのは Ingest License Key です。403 ならリージョンが合っていません（EU アカウントは `observability.newrelic.region: eu`）。次に、`opentelemetry` プラグインは「その値が最後に更新された時刻」を datapoint のタイムスタンプに使うため、起動時に一度だけ立つゲージ（`kong.cp.connection.status` など）は時間とともに古くなり、New Relic の受け付ける時間窓から外れて捨てられます。`config/otel-collector/config.yaml` の `transform/kong` プロセッサが送信時刻で上書きしてこれを避けているので、このマウントを外さないでください。
