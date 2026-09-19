# acme 検証環境

`env.yaml` から生成された環境です。構成を変えるときは `env.yaml` を編集して再生成してください。

## 構成

| 項目 | 値 |
|---|---|
| Gateway | api-gateway |
| Control Plane | self-managed |
| IdP | keycloak（realm: acme） |
| キャッシュ | none |
| ベクトル DB | none |
| Observability | otel-lgtm |
| 秘匿情報 | 環境変数 |
| Prompt Guard | なし |

## 前提

必要なツールは docker、mise、deck です。

`.env` の次の項目を埋めてから起動してください。

- `KONG_LICENSE_DATA`

## 初手

`config/kongctl.yaml` は同梱されますが、Konnect リソースは定義しません。Control Plane は `compose.yaml` で起動し、`mise run sync` は decK で `config/kong/kong.yaml` を適用します。

```bash
# .env の KONG_LICENSE_DATA に Kong Enterprise のライセンス JSON を 1 行で入れる
mise run setup                      # 証明書を用意して環境を起動し、Admin API が開いてから設定を適用する
mise run smoke
```

`mise run setup` は `config/kong/certs/tls.crt` が無ければ作り、`docker compose up -d` の後に Admin API が応答するまで待ってから `mise run sync` 相当を実行します。

## エンドポイント

| 用途 | URL |
|---|---|
| Proxy | http://localhost:8000 |
| Status API | http://localhost:8100/status |
| Admin API | http://localhost:8001 |
| Kong Manager | http://localhost:8002 |
| Grafana | http://localhost:3000 |
| Keycloak | http://localhost:8080 |
| httpbin（直接） | http://localhost:8081 |

## ダッシュボード

Grafana の `Kong` フォルダに、`config/grafana/dashboards/` の定義が provisioning で読み込まれます。データ源は `opentelemetry` プラグインが OTLP で送るメトリクスで、Prometheus プラグインは使いません。

| ダッシュボード | 内容 |
|---|---|
| Kong Gateway (OpenTelemetry) | Grafana 公式の [Kong Official (7424)](https://grafana.com/grafana/dashboards/7424-kong-official/) と同じ粒度。RPS、ステータスコード、Kong 内部／上流／全体のレイテンシ、帯域、共有メモリ、Nginx コネクション |

編集したものを残したいときは、Grafana の Export JSON を `config/grafana/dashboards/` に書き戻してください。ここは generator の生成物なので、恒久的に変えるなら `kong-labkit` の `generator/assets/grafana/` を直してください。

## トラブルシュート

**CP が起動直後に落ちる。** `kong migrations bootstrap` が終わる前に CP が起動するとマイグレーション未適用で落ちます。`depends_on` の `service_completed_successfully` で順序は保証していますが、`docker compose up` を個別サービス指定で叩いたときは順序が崩れます。

**ライセンスエラーが出る。** `.env` の `KONG_LICENSE_DATA` に Kong Enterprise のライセンス JSON を 1 行で入れてください。改行が入っていると読めません。

**ブラウザで Keycloak の管理コンソールを開くと動作がおかしい。** `KC_HOSTNAME` を Kong がコンテナ内で検証する issuer（`http://keycloak:8080/realms/acme`）に合わせて固定しているため、`http://localhost:8080` 経由でアクセスしてもページ内のリンクや API 呼び出しは `keycloak:8080` 宛てになります。ブラウザ（ホスト側）はこのホスト名を解決できないため、管理コンソールや認可コードフローをブラウザで直接使いたい場合は `/etc/hosts` に `127.0.0.1 keycloak` を追記してください。`direct access grants`（パスワードグラント）でトークンを取得するだけなら、`http://localhost:8080` 経由でも発行されるトークンの issuer は Kong の設定と一致するため、この追記は不要です。

**Grafana にメトリクスが 1 つも出ない。** `opentelemetry` プラグインは「その値が最後に更新された時刻」を datapoint のタイムスタンプに使います。起動時に一度だけ立つゲージ（`kong.cp.connection.status` など）は時間とともに古くなり、Prometheus に `too old sample` で弾かれます。OTLP は 1 点でも弾かれるとリクエスト全体が 400 になるため、Kong のメトリクスが丸ごと落ちます。`config/otel-lgtm/otelcol-config.yaml` の `transform/kong` プロセッサが送信時刻で上書きしてこれを避けているので、このマウントを外さないでください。`docker compose logs otel-lgtm | grep "Exporting failed"` で確認できます。

**ダッシュボードが Grafana に現れない。** `config/grafana/dashboards.yaml` の `path` は `conf/provisioning` の下を指す必要があります。Grafana の `permitted_provisioning_paths` がそれ以外を拒否します。
