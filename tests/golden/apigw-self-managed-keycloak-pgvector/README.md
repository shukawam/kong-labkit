# acme 検証環境

`env.yaml` から生成された環境です。構成を変えるときは `env.yaml` を編集して再生成してください。

## 構成

| 項目 | 値 |
|---|---|
| Gateway | api-gateway |
| Control Plane | self-managed |
| IdP | keycloak（realm: acme） |
| キャッシュ | none |
| ベクトル DB | pgvector |
| Observability | otel-lgtm |

## 前提

必要なツールは docker、mise、deck です。

`.env` の次の項目を埋めてから起動してください。

- `KONG_LICENSE_DATA`

## 初手

```bash
mise run certs                      # config/kong/certs/tls.{crt,key} を生成
# .env の KONG_LICENSE_DATA に Kong Enterprise のライセンス JSON を 1 行で入れる
mise run up
mise run sync
mise run smoke
```

## エンドポイント

| 用途 | URL |
|---|---|
| Proxy | http://localhost:8000 |
| Status API | http://localhost:8100/status |
| Admin API | http://localhost:8001 |
| Kong Manager | http://localhost:8002 |
| Grafana | http://localhost:3000 |
| Keycloak | http://localhost:8080 |
| pgvector | localhost:5433 |
| httpbin（直接） | http://localhost:8081 |

## トラブルシュート

**CP が起動直後に落ちる。** `kong migrations bootstrap` が終わる前に CP が起動するとマイグレーション未適用で落ちます。`depends_on` の `service_completed_successfully` で順序は保証していますが、`docker compose up` を個別サービス指定で叩いたときは順序が崩れます。

**ライセンスエラーが出る。** `.env` の `KONG_LICENSE_DATA` に Kong Enterprise のライセンス JSON を 1 行で入れてください。改行が入っていると読めません。

**トークン検証が issuer 不一致で落ちる。** `KC_HOSTNAME` は `http://localhost:8080` ですが、Kong がコンテナ内から見る issuer は `http://keycloak:8080/realms/acme` です。ブラウザから取得したトークンを Kong に渡すときに issuer がずれるため、テストは `direct access grants`（パスワードグラント）でコンテナ内の URL から取得するのが確実です。
