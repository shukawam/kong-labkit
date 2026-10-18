# 既知の未対応事項

`feat/env-generator` のマージ時点で分かっている穴を記録する。いずれも実起動スモークで検証できていない構成に属し、該当構成を実際に使うときに踏む。

## Important

### プロバイダ認証の対応表が 2 箇所にある

`generator/context.py` の `_build_provider_auth` と `generator/templates/config/kongctl.yaml.j2` が、「プロバイダ → ヘッダ名・環境変数名」の同じ対応表を別々に持っている。decK 側は `${{ env "DECK_X" }}`、kongctl 側は `!env X` と参照構文が違うため機械的な共有ができず、片側だけ直る形が残っている。

あわせて、AI Gateway v2 の kongctl テンプレートは vertex が `auth: type: vertex` に落ち、資格情報を一切出さない。vertex を v2 で使う構成は現状では動かない（bedrock は `auth.type: aws` で対応済み）。

### `ai-prompt-compressor` は Bedrock の model に掛けると必ず 403 になる

AI Gateway v2 の DP（2.0.3 と 2.2.0 で実測）では、`ai-proxy-advanced` が Bedrock 向けに Converse 形式へ変換した body を SigV4 で署名した後に、`ai-prompt-compressor` が OpenAI 形式の body を書き戻す。結果として Bedrock には署名と一致しない上に形式も違う body が届き、`The request signature we calculated does not match` の 403 になる。Azure OpenAI は署名が無く body 形式も同じなので表に出ない。generator は policy を `global: false` にして bedrock 以外の model にだけ `policies` で付けることで回避している。Kong 側の修正が入れば global に戻せる。

### `idp.type: ldap` が AI Gateway v2 で使えない

`ai-gateway-v2` の生成物は `config/kongctl.yaml` の AI Gateway エンティティだけで、Kong のプラグインを書く経路が generator に無い。そのため `idp.type: ldap` は schema で v2 を弾いている。`ai.prompt_guard` の追加で `ai_gateways[].policies` を出す経路自体はできたが、そこに `ldap-auth-advanced` のような Gateway プラグインを載せて Konnect が受けるかは未検証で、`ai-prompt-guard` でしか確かめていない。v2 でも認証を掛けるには、kongctl テンプレートに plugin を出す仕組みを足す必要がある。同じ制約は将来 v2 に `openid-connect` を掛けたくなったときにも出る。

### `semantic_routing` が AI Gateway v1 で no-op

`gateway: ai-gateway-v1` に `semantic_routing: true` を指定してもスキーマ検証は通り vectordb も起動するが、生成される `config/kong/kong.yaml` には semantic 関連の設定が何も出ない。`ai-proxy-advanced` には `balancer.algorithm: semantic` があるので、そこに配線する余地がある。`semantic_cache` は v1 でも効く。

### vertex の `gcp_service_account_json` にパスを渡している

`generator/context.py` の vertex 分岐が `GOOGLE_APPLICATION_CREDENTIALS`（慣習としてファイルパスを入れる欄）をそのまま `gcp_service_account_json` に渡している。Kong が要求するのは JSON の中身そのもの。`.env` の `KEY="value"` 形式に複数行 JSON を入れられないため、base64 で持つかファイルをマウントするか、渡し方自体の設計が必要。

### `secrets.backend: vault` が AI Gateway v2 で使えない

v2 の設定は `config/kongctl.yaml` の AI Gateway エンティティだけで、Konnect 側が vault 参照を受け付けるかを検証できていないため schema で弾いている。v2 を Vault 構成で見せたい案件が出たら、kongctl 経路の検証から始める必要がある。

### vault 参照は文字列連結ができない

Kong はフィールドの値全体が `{vault://...}` である場合にのみ解決する。値の一部に埋め込むとリテラル文字列として扱われ、認証が黙って失敗する。generator は OpenAI の `header_value` について `Bearer ` を前置した完成形を Vault に入れることで回避している。同じ形の秘密を足すときは、前置が必要かを確認すること。

### kongctl は秘密の値だけが変わった更新を検出しない

AI Gateway v2 の `config/kongctl.yaml` で `!env` から入れる秘密（`secret_access_key`、`session_token`、azure の `api-key` 値など）は Konnect 側で書き込み専用のため、kongctl の diff はそれらを比較しない。`.env` の値だけを変えて `mise run sync` しても「No changes detected」になり、Konnect には古い値が残る。実測では display_name のような非秘密フィールドを変えると更新が走り、その更新に秘密も乗る。Bedrock を STS の一時クレデンシャル（`auth: session-token`）で使うと期限ごとにこれを踏むので、生成物の README にトラブルシュートとして書いてある。恒久対応には kongctl 側の強制更新フラグか、generator が秘密のハッシュを非秘密フィールドに刻む仕組みが要る。

### 同梱の dev Vault は再起動で中身が消える

`hashicorp/vault` を dev モードで動かしているためメモリ保持で、`docker compose restart vault` すると KV が空になる。`vault-bootstrap` の流し直しが必要。

## 構造（実害は出ていないが、将来の修正が片側だけ入る形）

データプレーンのサービス名が構成によって `kong` / `gateway` / `kong-dp` の 3 種類になり、`kong-aigw-v2.yaml.j2` と `kong-dp.yaml.j2` は image 以外が同一。`ctx.kong.service_name` を作って 1 枚に統合すると、`mise run logs <service>` と README がサービス名を一意に示せるようになる。

`gateway: ai-gateway-v1` に `upstream: httpbin` を明示すると、`kong.yaml` は AI 側の分岐に入って httpbin のルートを出さないのに `mise run smoke` は `/httpbin/status/200` を叩くため必ず 404 になる。既定値では発生しない。

`.env` の `KEYCLOAK_CLIENT_SECRET` は、`kong.yaml` と `realm-export.json` の両方が実値埋め込みになったため生成物側から参照されない。書き換えても何も変わらない。

## 次にやると効く作業

クレデンシャル不要のファイル妥当性検証層（`deck file validate`、kongctl のドライラン）をテストに足すこと。今回マージ前に見つかった「decK が `${VAR}` を展開しない」「Konnect × 非 v2 の sync が decK 形式を kongctl に渡していた」「v2 の pgvector に接続情報が足りない」は、いずれもこの層があれば机上で捕捉できた。実起動スモークは 1 構成しか覆えないので、その手前に構文より一段深い網を置く価値が大きい。
