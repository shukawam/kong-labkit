# 既知の未対応事項

`feat/env-generator` のマージ時点で分かっている穴を記録する。いずれも実起動スモークで検証できていない構成に属し、該当構成を実際に使うときに踏む。

## Important

### プロバイダ認証の対応表が 2 箇所にある

`generator/context.py` の `_build_provider_auth` と `generator/templates/config/kongctl.yaml.j2` が、「プロバイダ → ヘッダ名・環境変数名」の同じ対応表を別々に持っている。decK 側は `${{ env "DECK_X" }}`、kongctl 側は `!env X` と参照構文が違うため機械的な共有ができず、片側だけ直る形が残っている。

あわせて、AI Gateway v2 の kongctl テンプレートは bedrock と vertex が `auth: type: <provider>` に落ち、資格情報を一切出さない。この 2 プロバイダを v2 で使う構成は現状では動かない。

### `idp.type: ldap` が AI Gateway v2 で使えない

`ai-gateway-v2` の生成物は `config/kongctl.yaml` の AI Gateway エンティティだけで、Kong のプラグインを書く経路が generator に無い。そのため `idp.type: ldap` は schema で v2 を弾いている。v2 でも認証を掛けるには、kongctl テンプレートに plugin を出す仕組みを足す必要がある。同じ制約は将来 v2 に `openid-connect` を掛けたくなったときにも出る。

### `semantic_routing` が AI Gateway v1 で no-op

`gateway: ai-gateway-v1` に `semantic_routing: true` を指定してもスキーマ検証は通り vectordb も起動するが、生成される `config/kong/kong.yaml` には semantic 関連の設定が何も出ない。`ai-proxy-advanced` には `balancer.algorithm: semantic` があるので、そこに配線する余地がある。`semantic_cache` は v1 でも効く。

### vertex の `gcp_service_account_json` にパスを渡している

`generator/context.py` の vertex 分岐が `GOOGLE_APPLICATION_CREDENTIALS`（慣習としてファイルパスを入れる欄）をそのまま `gcp_service_account_json` に渡している。Kong が要求するのは JSON の中身そのもの。`.env` の `KEY="value"` 形式に複数行 JSON を入れられないため、base64 で持つかファイルをマウントするか、渡し方自体の設計が必要。

## 構造（実害は出ていないが、将来の修正が片側だけ入る形）

データプレーンのサービス名が構成によって `kong` / `gateway` / `kong-dp` の 3 種類になり、`kong-aigw-v2.yaml.j2` と `kong-dp.yaml.j2` は image 以外が同一。`ctx.kong.service_name` を作って 1 枚に統合すると、`mise run logs <service>` と README がサービス名を一意に示せるようになる。

`gateway: ai-gateway-v1` に `upstream: httpbin` を明示すると、`kong.yaml` は AI 側の分岐に入って httpbin のルートを出さないのに `mise run smoke` は `/httpbin/status/200` を叩くため必ず 404 になる。既定値では発生しない。

`.env` の `KEYCLOAK_CLIENT_SECRET` は、`kong.yaml` と `realm-export.json` の両方が実値埋め込みになったため生成物側から参照されない。書き換えても何も変わらない。

## 次にやると効く作業

クレデンシャル不要のファイル妥当性検証層（`deck file validate`、kongctl のドライラン）をテストに足すこと。今回マージ前に見つかった「decK が `${VAR}` を展開しない」「Konnect × 非 v2 の sync が decK 形式を kongctl に渡していた」「v2 の pgvector に接続情報が足りない」は、いずれもこの層があれば机上で捕捉できた。実起動スモークは 1 構成しか覆えないので、その手前に構文より一段深い網を置く価値が大きい。
