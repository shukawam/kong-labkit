# HashiCorp Vault による秘匿情報の外部化 設計

## 目的

API キーやクライアントシークレットを、生成されるマニフェスト（`config/kong/kong.yaml`）にも環境変数経由の展開結果としても載せない選択肢を用意する。金融系を中心に、秘匿情報を構成管理の対象物に一切残さないことを求める顧客がいる。あおぞら銀行の構成（`ai-gateway-v1` × Konnect × LDAP）が具体的な対象で、同社は HashiCorp Vault を構成の検討対象に含めている。

現状の秘匿情報は 2 経路ある。`config/kong/kong.yaml` は `${{ env "DECK_AZURE_OPENAI_API_KEY" }}` を decK が sync 時に展開し、`config/kongctl.yaml` は `!env AZURE_OPENAI_API_KEY` を kongctl が apply 時に展開する。いずれも展開後の値が Kong ないし Konnect の設定として保存される。Vault を使う構成では `{vault://hcv/kong/ai-azure/header_value}` という参照文字列がそのまま保存され、値の解決は実行時にデータプレーンが Vault へ問い合わせて行う。

## スコープ

decK 経路（`gateway: api-gateway` と `gateway: ai-gateway-v1`）のみを対象とする。`ai-gateway-v2` の kongctl 経路は、Konnect の AI Gateway エンティティが vault 参照を受け付けるかを検証していないため、今回は schema で弾く。

Vault 経由にする秘密は、AI プロバイダの認証情報と IdP 系（`ldap-auth-advanced` の bind パスワード、`openid-connect` のクライアントシークレット）とする。pgvector と Redis のパスワードはラボ固定値で実益が薄いため対象外。

Vault は同梱する。dev モードの `hashicorp/vault` コンテナと、`.env` の値を KV に投入する使い捨ての bootstrap コンテナを compose に加える。外部 Vault を指す構成は今回の対象外で、必要になった時点で `secrets.vault.address` 相当を足せばよい。

## 事前検証の結果

実装前に、Kong 3.14 と dev モードの Vault で以下を確認済み。

`ldap_password` を `{vault://hcv/kong/ldap/password}` と書いただけのデクララティブ設定で、Kong が Vault から値を取得して LDAP bind に成功した（正しい資格情報で 200、誤った資格情報で 401）。参照先を存在しないパスに変えると 500 になり、error log に `unable to resolve reference {vault://hcv/kong/ldap/nope} (could not get value from external vault ...)` が出る。Vault エンティティは宣言せず、データプレーンの `KONG_VAULT_HCV_*` だけで解決している。

generator が現在書いているフィールドはすべて `referenceable: true` であることを Admin API の `/schemas/plugins/*` で確認した。`ai-proxy-advanced` の `config.targets.auth.header_value` / `azure_client_id` / `azure_tenant_id` / `aws_access_key_id` / `aws_secret_access_key` / `gcp_service_account_json`、`ldap-auth-advanced` の `config.ldap_password` と `config.bind_dn`、`openid-connect` の `config.client_secret` と `config.client_id`。

`hcv` バックエンドの `kv` は既定が `v1` である。Vault dev モードの `secret/` は KV v2 なので、`v2` を明示しないと全参照が解決不能になる。

## 入力スキーマ

`observability` と同じ形を取る。

```yaml
secrets:
  backend: vault   # env（既定） | vault
```

`backend` の既定は `env` で、既存の構成ファイルは無変更で従来どおり動く。

### 検証ルール

`secrets.backend: vault` と `gateway: ai-gateway-v2` は併用できない。v2 の設定は `config/kongctl.yaml` の AI Gateway エンティティだけで、そこが vault 参照を受け付けるか未検証のため。エラーメッセージで `ai-gateway-v1` か `api-gateway` に倒すよう促す。

## 秘密参照の抽象化

`generator/context.py` の `_DeckEnv.ref(source)` が、いま `${{ env "DECK_<source>" }}` を返している。これを秘密参照のファクトリに一般化し、backend に応じて decK の env 参照か vault 参照のどちらかを返すようにする。呼び出し側（`_build_provider_auth`、LDAP、OIDC の各分岐）は参照文字列を受け取るだけなので、分岐を増やさずに済む。

Vault のパスは `secret/kong/<用途>` とし、参照は `{vault://hcv/kong/<用途>/<キー>}` の形になる。用途は `ai-<provider>`、`ldap`、`oidc` の 3 種類。キー名は参照先の Kong のフィールド名をそのまま使う（`header_value`、`azure_client_id`、`azure_tenant_id`、`aws_access_key_id`、`aws_secret_access_key`、`gcp_service_account_json`、`ldap_password`、`client_secret`）。どの秘密がどの参照になるかを 1 対 1 で追えるようにするため、キー名を独自に付け替えない。

### 文字列連結ができない制約

Kong の vault 参照はフィールドの値全体が参照である場合にのみ解決される。値の一部に埋め込んでも解決されず、リテラル文字列として扱われる。

現状の `_build_provider_auth` は OpenAI について `"Bearer " + ref` を組み立てている。Vault 構成では Vault 側に `Bearer sk-...` という完成形を格納し、`header_value` の全体を 1 つの参照にする。bootstrap が `.env` の `OPENAI_API_KEY` に `Bearer ` を前置して投入することで、`.env` の書き方は従来のまま保つ。

## 生成物

### Vault サービス

`generator/templates/services/vault.yaml.j2` を追加する。`hashicorp/vault` を dev モードで起動し、ルートトークンは `.env` の `VAULT_TOKEN` から与える。既定値はラボ固定の `local-dev-token` とする（`KEYCLOAK_CLIENT_SECRET` と同じ扱い。乱数にするとゴールデンテストが毎回落ちる）。`PREFERRED_PORTS` に `vault: 8200` を追加し、`_ALLOCATION_ORDER` の末尾寄りに置く。

dev モードはメモリ保持なので、コンテナを再起動すると KV の中身が消える。この性質は README のトラブルシュートに書き、`vault-bootstrap` を流し直す手順を示す。

### Vault bootstrap

`generator/templates/services/vault-bootstrap.yaml.j2` を追加する。`lldap-bootstrap` と同じ手口で、`vault` が healthy になってから一度だけ走り、`.env` の値を `vault kv put` で KV v2 に投入して終了する。投入するキーは、その構成で実際に参照される秘密だけに絞る。

### Kong への配線

`kong-dp.yaml.j2` と `kong-self-managed.yaml.j2` に、`secrets.backend: vault` のときだけ `KONG_VAULT_HCV_PROTOCOL` / `HOST` / `PORT` / `MOUNT` / `KV` / `TOKEN` を出す。`KV` は `v2` 固定。Vault エンティティは宣言しないため、`config/kong/kong.yaml` の構造は変わらず、値が参照文字列に置き換わるだけになる。

Konnect のハイブリッド構成でも、参照を解決するのはローカルのデータプレーンなので同じ配線で動く。

### decK 呼び出し

`secrets.backend: vault` のとき、Vault 経由にした秘密については `DECK_*` 環境変数が不要になる。`DeckCtx.env_prefix` が空文字になる経路と、`mise run sync` / `setup` / `diff` が持っている「必要な環境変数が空なら処理を開始せずに停止する」チェックの対象から、Vault 経由の分を外す必要がある。既存コードへの影響が最も大きいのはこの箇所。

## テスト

L1 として、schema の検証ルール（v2 との併用を弾く）、参照文字列の生成（backend ごとに `${{ env ... }}` と `{vault://...}` を出し分ける）、`DECK_*` の要求が減ること、compose への `vault` / `vault-bootstrap` の追加、`KONG_VAULT_HCV_*` の付与を単体テストで押さえる。

L2 として `examples/aigw-v1-konnect-vault.yaml` を 1 本追加し、golden を増やす。既存の組み合わせマトリクスには `secrets.backend` の軸を足す。

L3 として、事前検証で手作業により確認した「Vault 経由の LDAP 認証が正しい資格情報で 200、誤った資格情報で 401」を `slow` マークの実起動テストに落とす。参照先を壊したときに 500 になることも同じテストで確かめると、偽陽性（Vault を経由せずに通っている）を防げる。

## 既知の制約として記録するもの

`docs/superpowers/known-gaps.md` に次を追記する。`ai-gateway-v2` では `secrets.backend: vault` が使えないこと。vault 参照は文字列連結ができず、値全体が参照である必要があること。同梱の dev Vault は再起動で中身が消えるため bootstrap の再実行が要ること。

## 生成される README

Vault 構成のときだけ、秘密がどこを通るかを示す節を足す。`.env` に入れた値を `vault-bootstrap` が KV に投入し、`config/kong/kong.yaml` には参照文字列だけが載り、データプレーンが `KONG_VAULT_HCV_*` を使って実行時に解決する、という流れを書く。

トラブルシュートには、参照が解決できないときに `docker compose logs gateway | grep "unable to resolve reference"` で原因が出ること、`KONG_VAULT_HCV_KV` を `v2` から変えると全参照が壊れること、dev Vault の再起動で KV が空になることを書く。
