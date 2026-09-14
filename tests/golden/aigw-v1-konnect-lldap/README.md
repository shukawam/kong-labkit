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

## LDAP のユーザー

`config/lldap/` の内容を `lldap-bootstrap` が投入します。変更するときは `env.yaml` の `idp.users[]` を編集して再生成してください。

| ユーザー | パスワード | 所属グループ |
|---|---|---|
| developer | developer-password | developer-dep |
| researcher | researcher-password | developer-dep、researcher-dep |

## トラブルシュート

**データプレーンが Konnect に繋がらない。** `mise run sync` が成功し、`.certs/cluster.crt` が Konnect に登録されているか確認してください。クラスタ証明書を `mise run certs` で再生成したときは、`mise run sync` で再登録してから `docker compose restart` で Data Plane に証明書を読み直させてください。

**`CONTROL_PLANE_ID` が空のまま起動した。** `KONG_CLUSTER_CONTROL_PLANE` が `.us.cp.konghq.com:443` という形になり、名前解決に失敗します。`.env` を埋めて `mise run reset` してください。

**LDAP 認証の通し方。** `header_type: ldap` を指定しているため、`curl -H 'Authorization: ldap ZGV2ZWxvcGVyOmRldmVsb3Blci1wYXNzd29yZA=='` で認証情報を送ります。ヘッダーの値は `developer:developer-password` を Base64 化したものです。`curl -u` は Basic 認証を送るため、この設定では使えません。bind は `cn=admin,ou=people,dc=acme,dc=local` で行い、`ou=people,dc=acme,dc=local` を `uid` で検索します。lldap の管理 UI には `admin` / `.env` の `LLDAP_ADMIN_PASSWORD` でログインできます。

**テストユーザーが存在しない。** `lldap-bootstrap` は `config/lldap/` の JSON を投入する使い捨てのコンテナで、`lldap` が healthy になってから 1 度だけ走ります。`docker compose logs lldap-bootstrap` を確認してください。このスクリプトは実行時に `jq` と `jo` を apk で取得するため、起動時にネットワークが必要です。投入内容を変えたときは `docker compose up -d --force-recreate lldap-bootstrap` で流し直せます。

**認証は通るのに Consumer が付かない。** `consumer_optional: true` にしてあるため、`config/kong/kong.yaml` の `consumers` に無いユーザーでもリクエストは通ります。`env.yaml` の `idp.users[]` に足せば Consumer も一緒に生成されます。

**グループでアクセスを分けたい。** `ldap-auth-advanced` は認証したユーザーの LDAP グループを ACL に載せるので、`config/kong/kong.yaml` のルートに `acl` プラグインを足し、`allow` に `developer-dep` や `researcher-dep` を並べれば経路ごとに通すグループを絞れます。どのグループをどの経路に許すかは構成ごとの判断なので、generator では生成していません。

**認証後に 502 になる／AI Proxy Advanced が見当たらない。** `.env` のプロバイダ用 API キーが空だと、`ai-proxy-advanced` の作成がスキーマ検証で失敗します。decK の適用は一括でロールバックされないため、Service や LDAP プラグインだけが残ることがあります。`mise run setup`・`sync`・`diff` は必要な環境変数が空なら処理を開始せずに停止します。キーを設定して `mise run diff` で差分を確認し、`mise run sync` で再適用してください。ログの接続先が `127.0.0.1:32000` なら、AI Proxy が仮の Service URL を差し替えていません。

**疎通テストの範囲。** `mise run smoke` は無認証の 401 と、LDAP 認証付きチャットの 200 を確認します。上流 LLM の有効な API キーとデプロイが必要で、テスト時に LLM を呼び出します。401 以外でも、502 などのエラーは失敗になります。
