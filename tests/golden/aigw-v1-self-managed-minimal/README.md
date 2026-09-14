# acme 検証環境

`env.yaml` から生成された環境です。構成を変えるときは `env.yaml` を編集して再生成してください。

## 構成

| 項目 | 値 |
|---|---|
| Gateway | ai-gateway-v1 |
| Control Plane | self-managed |
| IdP | none |
| キャッシュ | none |
| ベクトル DB | none |
| Observability | otel-lgtm |

## 前提

必要なツールは docker、mise、deck です。

`.env` の次の項目を埋めてから起動してください。

- `KONG_LICENSE_DATA`
- `AZURE_OPENAI_API_KEY`

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

## トラブルシュート

**CP が起動直後に落ちる。** `kong migrations bootstrap` が終わる前に CP が起動するとマイグレーション未適用で落ちます。`depends_on` の `service_completed_successfully` で順序は保証していますが、`docker compose up` を個別サービス指定で叩いたときは順序が崩れます。

**ライセンスエラーが出る。** `.env` の `KONG_LICENSE_DATA` に Kong Enterprise のライセンス JSON を 1 行で入れてください。改行が入っていると読めません。

**認証後に 502 になる／AI Proxy Advanced が見当たらない。** `.env` のプロバイダ用 API キーが空だと、`ai-proxy-advanced` の作成がスキーマ検証で失敗します。decK の適用は一括でロールバックされないため、Service や LDAP プラグインだけが残ることがあります。`mise run setup`・`sync`・`diff` は必要な環境変数が空なら処理を開始せずに停止します。キーを設定して `mise run diff` で差分を確認し、`mise run sync` で再適用してください。ログの接続先が `127.0.0.1:32000` なら、AI Proxy が仮の Service URL を差し替えていません。
