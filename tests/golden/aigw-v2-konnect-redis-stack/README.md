# acme 検証環境

`env.yaml` から生成された環境です。構成を変えるときは `env.yaml` を編集して再生成してください。

## 構成

| 項目 | 値 |
|---|---|
| Gateway | ai-gateway-v2 |
| Control Plane | konnect（acme-ai-gateway / us.cp.konghq.com） |
| IdP | none |
| キャッシュ | redis-stack |
| ベクトル DB | redis-stack |
| Observability | otel-lgtm |

## 前提

必要なツールは docker、mise、kongctl です。

`.env` の次の項目を埋めてから起動してください。

- `CONTROL_PLANE_ID`
- `KONNECT_PAT`
- `AZURE_OPENAI_API_KEY`

## 初手

```bash
# Konnect の Personal Access Token を発行し（Konnect > 右上のアカウント > Personal Access Tokens）、.env の KONNECT_PAT に書く
mise run certs                      # .certs/cluster.{crt,key} を生成
# Konnect で acme-ai-gateway を作成し、.certs/cluster.crt を登録する
# 発行された Control Plane ID を .env の CONTROL_PLANE_ID に書く
mise run up
mise run sync
mise run smoke
```

## エンドポイント

| 用途 | URL |
|---|---|
| Proxy | http://localhost:8000 |
| Status API | http://localhost:8100/status |
| Grafana | http://localhost:3000 |
| Redis | localhost:6379 |

## トラブルシュート

**データプレーンが Konnect に繋がらない。** `.certs/cluster.crt` を Konnect の Control Plane に登録し忘れている場合がほとんどです。クラスタ証明書を再生成したときは登録もやり直す必要があります。

**`CONTROL_PLANE_ID` が空のまま起動した。** `KONG_CLUSTER_CONTROL_PLANE` が `.us.cp.konghq.com:443` という形になり、名前解決に失敗します。`.env` を埋めて `mise run reset` してください。

**ベクトル検索が動かない。** `redis:8` ではなく `redis/redis-stack` を使っているか確認してください。素の Redis には検索モジュールが入っていません。
