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
| AI Consumer | なし（認証なし） |

## 前提

必要なツールは docker、mise、kongctl、vllm-metal（後述） です。

`.env` の次の項目を埋めてから起動してください。

- `CONTROL_PLANE_ID`
- `KONNECT_PAT`

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

## ローカル LLM（vLLM）

`upstream_url` の先の vLLM を手元で起動します。1 プロセスに 1 モデルで、Kong の Data Plane からは `host.docker.internal` 経由で届きます。待ち受けは `127.0.0.1` だけなので LAN には晒しません。公開名（`--served-model-name`）は Kong が本文の `model` に入れる target 名に揃えてあります。

| モデル | 実行方法 | ポート | 読み込むモデル | 公開名 |
|---|---|---|---|---|
| qwen3-5-0-8b | vllm-metal（ホスト） | 8001 | `mlx-community/Qwen3.5-0.8B-MLX-4bit` | `qwen3-5-0-8b` |
| bge-reranker-v2-m3 | vLLM CPU コンテナ | 8003 | `BAAI/bge-reranker-v2-m3` | `BAAI/bge-reranker-v2-m3` |
| multilingual-e5-base | vllm-metal（ホスト） | 8002 | `intfloat/multilingual-e5-base` | `intfloat/multilingual-e5-base` |

```bash
mise run vllm:up                   # 起動して全モデルの応答を待つ。mise run setup / up からも呼ばれる
mise run vllm:status               # 各ポートが応答するか
mise run vllm:logs <モデル名>       # 省略すると全モデル
mise run vllm:down                 # 停止する。mise run down からも呼ばれる
```

初回は Hugging Face から重みを取るので、起動に数分かかります。重みは `~/.cache/huggingface` に置かれ、ホストとコンテナで共有します。

vllm-metal はホストに Homebrew で入れます。tap を先に足さないと `brew install` がリポジトリを見つけられません。

```bash
brew tap vllm-project/vllm-metal https://github.com/vllm-project/vllm-metal
brew install vllm-project/vllm-metal/vllm-metal
```

ホストで動かすモデルの PID とログは `.vllm/`（git 管理外）に置きます。vllm-metal の `--gpu-memory-utilization`（既定 0.92）は、Metal に使えるメモリのうち重み・作業領域・KV キャッシュの合計に充てる割合です。既定のまま複数モデルを並べると取り合いになるので、`env.yaml` の `serve.args` で下げてください。下げすぎると `kv_budget` が負になって起動しないので、`mise run vllm:logs <モデル名>` のエラーに出る `model_memory` と `overhead` の合計を上回る値にします。

vLLM CPU コンテナ（`vllm/vllm-openai-cpu:v0.33.0-arm64`）は compose のサービスで、`mise run up` で Kong と一緒に起動します。vllm-metal が扱えないモデル（ModernBERT の分類ヘッドを持つ reranker など）をこちらで動かします。

## トラブルシュート

**データプレーンが Konnect に繋がらない。** `mise run sync` が成功し、`.certs/cluster.crt` が Konnect に登録されているか確認してください。クラスタ証明書を `mise run certs` で再生成したときは、`mise run sync` で再登録してから `docker compose restart` で Data Plane に証明書を読み直させてください。

**`CONTROL_PLANE_ID` が空のまま起動した。** `KONG_CLUSTER_CONTROL_PLANE` が `.us.cp.konghq.com:443` という形になり、名前解決に失敗します。`.env` を埋めて `mise run reset` してください。

**`.env` のプロバイダ秘密を変えたら `mise run sync` し直す。** kongctl は書き込み専用フィールド（秘密）の値を比較しないため、生成物の `sync` / `diff` は常に `--write-secrets` を付けて `.env` の秘密を書き込みます。`mise run diff` の `secret write` がその対象です。Bedrock の `{"message":"The security token included in the request is invalid."}` は token が Konnect に届いていない状態、`... is expired` は届いているが期限切れの状態です。`config/kongctl.yaml` を手で `kongctl sync` するときも `--write-secrets` を付けてください。付けない更新は azure の `api-key` ヘッダを消します（kongctl 1.20.2 で実測）。

**Grafana にメトリクスが 1 つも出ない。** `opentelemetry` プラグインは「その値が最後に更新された時刻」を datapoint のタイムスタンプに使います。起動時に一度だけ立つゲージ（`kong.cp.connection.status` など）は時間とともに古くなり、Prometheus に `too old sample` で弾かれます。OTLP は 1 点でも弾かれるとリクエスト全体が 400 になるため、Kong のメトリクスが丸ごと落ちます。`config/otel-lgtm/otelcol-config.yaml` の `transform/kong` プロセッサが送信時刻で上書きしてこれを避けているので、このマウントを外さないでください。`docker compose logs otel-lgtm | grep "Exporting failed"` で確認できます。

**Loki にログが出ない、または飛び飛びになる。** Kong のアクセスログは 1 件で構造化メタデータを 150 個以上持ちます（`gen_ai.*` と全リクエスト/レスポンスヘッダ）。Loki の既定上限は 128 で、超えると `has too many structured metadata labels` で 400 を返します。OTLP は全か無かなので、そのバッチに同梱された内部ログまで丸ごと捨てられ、ログが飛び飛びに見えます。compose の `LOKI_EXTRA_ARGS` で上限を上げてあるので、この環境変数を外さないでください。`docker compose logs otel-lgtm | grep "Exporting failed"` で確認できますが、otel-lgtm は既定で各コンポーネントのログを捨てるため、`ENABLE_LOGS_ALL=true` を付けて起動する必要があります。

**ダッシュボードが Grafana に現れない。** `config/grafana/dashboards.yaml` の `path` は `conf/provisioning` の下を指す必要があります。Grafana の `permitted_provisioning_paths` がそれ以外を拒否します。
