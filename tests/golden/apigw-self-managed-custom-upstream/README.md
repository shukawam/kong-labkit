# upstream-fixture 検証環境

`env.yaml` から生成された環境です。構成を変えるときは `env.yaml` を編集して再生成してください。

## 構成

| 項目 | 値 |
|---|---|
| Gateway | api-gateway |
| Control Plane | self-managed |
| IdP | none |
| キャッシュ | none |
| ベクトル DB | none |
| Observability | なし |
| 秘匿情報 | 環境変数 |
| Prompt Guard | なし |
| Prompt Compressor | なし |
| PII Sanitizer | なし |

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

## 外部 API への接続

この構成は専用の local Control Plane 用です。Service と Route は各 1 個、名前は `upstream` です。外部 API の起動・停止・DNS・network・到達許可は API owner が管理します。generator は API の container、image、公開 port を追加しません。

`upstream.url` は Data Plane（DP）から接続する非秘密の base URL です。host/browser の URL とは別です。別 Compose project の API は `kong-network` に自動参加しません。API owner が生成 Compose の実際の network への参加・DNS alias、または DP から到達できる host/IP を用意し、DP 側から直接疎通を確認してから `mise run smoke` を実行してください。host 上の API には Docker 実装に応じた host gateway 設定が必要です。`localhost` と loopback IP は DP 自身を指すため入力で拒否します。

生成 directory を cwd にし、同梱 `compose.yaml` と mise が読む `.env` を使用してください。外部 API の成功 path は入力で指定します。`mise run smoke` は host の Proxy へ匿名 GET し、HTTP 200 と完全一致する場合だけ成功します。redirect は追わず、body は表示しません。URL の base path と Route の `strip_path` により上流 path は変わるため、Proxy 用の smoke path を上流 URL へそのまま足さないでください。

```bash
mise run diff       # config/kong/kong.yaml と現在の設定を比較
mise run sync       # 同じ設定を decK で適用。別 writer は不要
mise run smoke      # 外部 API の準備と設定伝播の後に実行

docker compose stop   # 一時停止（container/network の除去ではない）
docker compose start  # 再開後に smoke で確認
```

URL・Route を変更するときは元の入力 YAML を編集し、kong-labkit 側で同じ出力先に `--force` で再生成してから、生成 directory で `mise run diff` → `mise run sync` → `mise run smoke` を実行します。未コミット変更は拒否され、`.env`・`.certs/`・`docs/` は保護されます。他の生成 file の手編集は上書き対象です。有効な URL は `env.yaml` と Gateway 設定へ保存されるため、秘密を入れないでください。userinfo・query・fragment・認証 header・env 補間は非対応です。path に含む秘密の完全検出や、SSRF・DNS 再束縛の防御を保証するものではありません。HTTPS は既存 DP の TLS 動作・信頼条件に従い、独自 CA・client certificate・TLS 検証無効化は追加しません。

`mise run down` は container/network を除去します。`mise run reset` は `down -v` → `up -d` で volume のデータを破棄します。fresh DB には `mise run sync` で設定を再適用してください。custom 設定を除くには入力を `upstream: none` にして再生成し、diff と sync を実行します。外部 API とその network/volume は除去しません。API owner が別途管理してください。固定 container 名と host 公開 port があるため、専用環境で逐次実行してください。

## エンドポイント

| 用途 | URL |
|---|---|
| Proxy | http://localhost:8000 |
| Status API | http://localhost:8100/status |
| Admin API | http://localhost:8001 |
| Kong Manager | http://localhost:8002 |

## トラブルシュート

**CP が起動直後に落ちる。** `kong migrations bootstrap` が終わる前に CP が起動するとマイグレーション未適用で落ちます。`depends_on` の `service_completed_successfully` で順序は保証していますが、`docker compose up` を個別サービス指定で叩いたときは順序が崩れます。

**ライセンスエラーが出る。** `.env` の `KONG_LICENSE_DATA` に Kong Enterprise のライセンス JSON を 1 行で入れてください。改行が入っていると読めません。
