---
name: kong-labkit-env
description: Use when someone describes a Kong verification environment in natural language and needs this lab kit's input YAML — 「v2 で Azure OpenAI を試したい」「Keycloak 認証付きの API Gateway が欲しい」「セマンティックキャッシュを見せたい」 — or is writing or editing customer/<name>.yaml, or asks which key a requirement maps to.
---

# 要件から kong-labkit の入力 YAML を起こす

`customer/<name>.yaml` を 1 枚書けば `mise run generate <name>` で検証環境が出る。このスキルは「やりたいこと」をその 1 枚に翻訳する。リポジトリルートで作業する。

## 値の一覧はここに無い

キーと値の候補は `schemas/env.schema.json`（`generator/schema.py` から生成）と README の表が正。**このファイルと食い違ったらそちらが正しい。** ここにあるのは、そこを読んでも分からないことだけ。

## 手順

1. `customer` と `gateway` と `control_plane` を決める。この 3 つで `examples/` のどれを写すかが決まるので、**その example を読んで出発点にする**。
2. 残りの要件を差分として足す。
3. 決められない値は聞く → §聞かずに埋めない値
4. 矛盾していたら聞く → §解けない組み合わせ
5. `customer/<name>.yaml` に書く。ファイル名の `<name>` と `customer:` の値は**必ず一致させる**（出力先はファイル名、namespace は `customer:` から決まるので、ずれると両者が食い違う）。1 顧客に構成を 2 つ持たせたいときは `customer/<顧客名>-<用途>.yaml` にして `customer:` も同じ文字列にする。
6. §検証 を通す。
7. 完成した YAML を見せ、`mise run generate <name>` を案内する。**生成後に `.env` へ手で入れる値**も一緒に伝える（`konnect` なら `KONNECT_PAT` と `CONTROL_PLANE_ID`、`self-managed` なら `KONG_LICENSE_DATA`、New Relic なら `NEW_RELIC_LICENSE_KEY`、各プロバイダの API キー）。YAML には現れないので、言わないと生成物の README を読むまで気づかない。

## 聞かずに埋めない値

顧客側にしか正解が無く、**間違っていても検証は通る**値。推測で埋めない。他の `customer/*.yaml` から写さない（別顧客の実設定が混ざる）。

| キー | 間違えるとどうなるか |
|---|---|
| `customer` | namespace と CP 名の素。LDAP では `dc=<値>,dc=local` の base DN にもなる。→ §書く前に確かめる |
| `ai.providers[].instance` | Azure Foundry の実リソース名。起動して疎通するまで誤りが出ない |
| `ai.providers[].models[].name` / `deployment_id` | 実在しないモデルでも生成は成功する |
| `idp.realm` | issuer URL と `realm-export.json` の両方に入る |
| `idp.users[].groups` | LDAP の group DN と Kong の Consumer 名になる |
| `observability.newrelic.region` | EU アカウントで `us` のままだと 403 |

聞けないまま進めるときは `# TODO: <何を入れるか>` を付ける。ただし **TODO では守れないキーがある**。`idp.realm` は必須なので暫定値を入れるしかなく、`observability.newrelic.region` は既定値があるので TODO を付けても黙って US に送られる。どちらも暫定値がそのまま生成物に焼き込まれるので、**最後の報告で「これは推測した値」として列挙する**。YAML のコメントだけで済ませない。

## 解けない組み合わせ

`generator/schema.py` が弾く。**YAML を書いて検証エラーで気づくのではなく、聞いている途中で気づいて、どちらを優先するかユーザーに聞く。** 片方を勝手に落とさない。

| 要件 | 選べる形 |
|---|---|
| `ai-gateway-v2` × `self-managed` | v2 は Konnect 専用。v1 にするか Konnect を使うか |
| `ai-gateway-v2` × `idp.type: ldap` | v2 にプラグインを書く経路が無い。v1 にするか認証を諦めるか |
| `ai-gateway-v2` × `secrets.backend: vault` | 同上。v1 にするか `env` に戻すか |
| `ai.prompt_guard` × `api-gateway` | 検査する LLM リクエストが無い。`ai-gateway-v1` か `v2` にする |
| `ai.prompt_guard: observe` × `ai-gateway-v1` | v1 の DP に `continue_on_detection` が無い。v2 にするか `block` にする |
| `ai.prompt_compressor` × `ai-gateway-v2` 以外 | generator が v2 の `policies` にしか出さない |
| `semantic_cache` / `semantic_routing` × `vectordb.type: none` | vectordb が必須。`redis-stack` か `pgvector` |
| `vectordb.type: redis-stack` × `cache.type` がそれ以外 | 素の redis に検索モジュールが無い。`cache.type: redis-stack` にするか vectordb を `pgvector` に |

## ユーザーに聞けないとき

**選択肢を 1 つに潰す判断を代行しない。** §解けない組み合わせ に当たったとき、既存の `customer/<name>.yaml` を上書きすることになったとき —— ファイルを書かずに、候補と論点を並べて返す。矛盾が複数絡んでも同じで、こちらで順位を付けない。

§聞かずに埋めない値 はこれとは別。暫定値を入れて書いてよく、代わりに推測した値を最後に列挙する。

## 検証は通るが動かないもの

`docs/superpowers/known-gaps.md` が正。要件がここに当たったら、**YAML を書く前に**「この構成では届かない」と伝える。

| 要件 | 実際に起きること |
|---|---|
| v1 で `semantic_routing: true` | 検証は通り vectordb も起動するが `kong.yaml` に balancer が出ない。完全な no-op |
| v2 で複数プロバイダに振り分け | v2 は model 1 つにつき専用ルート `/v1/<model名>` を出す。1 model = 1 target なので振り分けにならない |
| v2 で `bedrock` / `vertex` | kongctl テンプレートが資格情報を出さない |
| v1 で `vertex` | `gcp_service_account_json` にパスが入る。Kong が要求するのは JSON の中身 |
| LDAP でグループごとに権限を変える | 出るのは `ldap-auth-advanced` だけ。グループ別の認可は生成されない |
| Keycloak で `.env` の `KEYCLOAK_CLIENT_SECRET` を変える | `kong.yaml` と `realm-export.json` に実値が埋め込み済みで、`.env` 側は参照されない |
| `ai-gateway-v1` に `upstream: httpbin` を明示 | ルートが出ないのに `mise run smoke` が叩くので必ず 404。既定のままにする |

## 自然文 → キー

1 つの要望が複数キーに散るものだけ。単純な 1 対 1 は README の表を見る。

| 言われ方 | 落とすキー |
|---|---|
| 「最新の AI Gateway」「v2」 | `gateway: ai-gateway-v2` + `control_plane: konnect`（他に選択肢が無い） |
| 「プラグインを試したい」「AI ではない」 | `gateway: api-gateway` + `upstream: httpbin` |
| 「セマンティックキャッシュ」 | `ai.semantic_cache: true` + `vectordb.type` + `redis-stack` なら `cache.type: redis-stack` |
| 「プロンプトインジェクション対策」「ガードレール」 | `ai.prompt_guard: observe`（検知して通す、v2 のみ）か `block`（検知して弾く）。パターンは `context.py` の `PROMPT_GUARD_DENY_PATTERNS` 固定で、入力からは変えられない |
| 「トークンを減らしたい」「プロンプト圧縮」「LLMLingua」 | `ai.prompt_compressor: true`。v2 のみ。圧縮サービスのコンテナが同梱されるが、イメージは Cloudsmith の私有レジストリなので Kong Support のトークンで `docker login` が要ると伝える |
| 「キーを置きたくない」「マネージド ID」 | `auth: managed-identity`（`azure` のみ） |
| 「秘密を外出ししたい」「Vault」 | `secrets.backend: vault`。秘密を持つ provider か `idp` が無いと弾かれる |
| 「New Relic に送りたい」 | `observability.backend: newrelic` + `newrelic.region`。traces/metrics/logs が全部行く（トレースだけには絞れない）。Grafana とダッシュボードは生成されなくなる |
| 「Grafana で見たい」 | 既定の `otel-lgtm`。何も書かない |
| 「コストを見たい」 | `models[].input_cost` / `output_cost`（100 万トークンあたり USD）。無いと常に 0 |

## 書く前に確かめる

- **`customer:` の重複**: 書く前に `ls customer/` で既存の値と重なっていないか確かめる。`control_plane: konnect` では `customer:` が kongctl の namespace になり、`mise run sync` は `kongctl sync --auto-approve` なので、**重なると既存環境の Konnect エンティティが削除対象に入る**。Konnect 上の表示名だけ揃えたいなら `konnect_name` で分ける（namespace は分かれたまま）。`self-managed` では sync が `deck gateway sync --kong-addr http://localhost:<admin>` なので削除は起きず、`konnect_name` も schema が弾く。代わりにファイル名ごと分ける。
- **ポートは固定**: ホスト側ポートは `generator/context.py` の `PREFERRED_PORTS` から決まり、顧客ごとには振り直されない。2 つの環境を**同時に起動することはできない**。
- **埋め込みの出どころ**: v1 は `ai.providers` の**先頭**のプロバイダから埋め込み認証を取る。v2 は**モデルごとにそのモデルの provider** で解決するので、プロバイダが複数あるなら**全部**に `ai.embedding_model` と同名のデプロイが要る（`deployment_id` は付かない）。
- **vectordb の選び方**: 指定が無ければ `redis-stack`（`cache.type: redis-stack` とセットでコンテナ 1 つに収まる）。キャッシュを Redis で持ちたくないなら `pgvector`。
- **v2 のモデル名**: `name` がそのまま ref とルートパス `/v1/<name>` になる。ドットを入れない（examples は `name: gpt-5-6` + `deployment_id: gpt-5.6`）。v2 は provider の ref が type 名なので、同じ type を 2 つ並べると衝突する。

## 検証

**`mise run generate` や `gen.py` は検証のために走らせない**（実環境が生える）。ユーザーが明示的に生成を頼んだときだけ走らせる。

リポジトリルートで、ファイルを 1 つも書かない dry-run を使う:

```bash
uv run python -c "
import sys
from pathlib import Path
from generator.schema import load_spec
from generator.context import build_context
from generator.render import render_all
try:
    s = load_spec(Path(sys.argv[1]))
    for w in s.warnings():
        print('警告:', w)
    render_all(build_context(s))
    print('OK')
except Exception as e:
    print(e)
    sys.exit(1)
" customer/<name>.yaml
```

まだ `customer/` に置きたくない候補は、スクラッチパッドなど任意の場所に書いて同じコマンドに渡せる（`load_spec` はパスを受けるだけ）。エラーは理由と直し方まで日本語で出る。読んで直して再実行する。`load_spec` だけで止めると埋め込みモデルの次元数エラーを取りこぼすので、3 段とも通すこと。

## 書き方

- 1 行目は `# yaml-language-server: $schema=../schemas/env.schema.json`。
- ベースにした example が明示している既定値（`idp.type: none` など）はそのまま残す。example が書いていない既定値は足さない。
- example から写した値も推測値。`idp.realm` / `ai.providers[].instance` / モデル名は §聞かずに埋めない値 なので写したまま残さない（`examples/apigw-self-managed-keycloak.yaml` の `realm: acme` は、顧客名がたまたま acme だと正しく埋まったように見える）。
- 解説コメントは入れない。例外は `# TODO:` だけ。
- 既存の `customer/<name>.yaml` を上書きする前に確認を取る。
