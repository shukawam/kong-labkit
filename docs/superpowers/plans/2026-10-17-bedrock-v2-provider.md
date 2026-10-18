# AI Gateway v2 で Bedrock プロバイダを動かす

## 背景

`gateway: ai-gateway-v2` で `ai.providers[].type: bedrock` を指定すると、`generator/templates/config/kongctl.yaml.j2` が `auth: {type: bedrock}` を出すだけで資格情報が一切出ず、Konnect に sync できない。`docs/superpowers/known-gaps.md` の「プロバイダ認証の対応表が 2 箇所にある」に記録済みの穴。顧客案件（toray、Microsoft Foundry に加えて Bedrock を並べたい）で必要になったので恒久対応する。

対象は Bedrock のみ。vertex は今回触らず、known-gaps に残す。

## Konnect 側の正しい形（`kongctl scaffold` で確認済み、2026-10-17）

`kongctl scaffold ai_gateway_model_providers` と `kongctl scaffold ai_gateway_models` を自分でも実行して、以下が現行スキーマと一致することを最初に確かめること。

モデルプロバイダ（`type: bedrock`）の `config.auth` は `type: aws` で、フィールドは `access_key_id` / `secret_access_key` / `session_token` / `assume_role_arn` / `role_session_name` / `sts_endpoint_url` / `batch_role_arn`。`access_key_id` を省くと Data Plane 側のデフォルト AWS 認証チェーンが使われる。プロバイダの `config` に `region` は無い。

モデルの `targets[].config`（`type: bedrock`）に `region` がある。Bedrock のリージョンはここに書く。`deployment_id` / `api_version` は Azure 専用なので Bedrock では出さない。

期待する生成物:

```yaml
    model_providers:
      - ref: bedrock
        ai_gateway: !ref <cp_name>#id
        name: bedrock
        type: bedrock
        display_name: bedrock
        config:
          auth:
            type: aws
            access_key_id: !env AWS_ACCESS_KEY_ID
            secret_access_key: !env AWS_SECRET_ACCESS_KEY

    models:
      - ref: claude-opus-5
        ...
        targets:
          - name: claude-opus-5
            provider: bedrock
            weight: 100
            config:
              type: bedrock
              region: ap-northeast-1
```

`!env` の書き方は同ファイルの azure 分岐（`value: !env AZURE_OPENAI_API_KEY`）に揃える。scaffold が出す `!secret {source: !env ...}` 形式は、このリポジトリではまだ使っていないので採用しない。

## 作業

TDD で進める（`superpowers:test-driven-development`）。テストを先に赤にしてからテンプレートを直す。

### 1. テンプレート `generator/templates/config/kongctl.yaml.j2`

- `model_providers` の auth 分岐に `provider.type.value == 'bedrock'` を足し、上記の `auth: {type: aws, access_key_id, secret_access_key}` を出す。
- `{% if provider.region %} region: ... {% endif %}` はプロバイダ `config` から外し、`targets[].config` 側へ移す。bedrock 以外で `region` を使っているプロバイダが無いことを `grep -n region generator/templates/` と `tests/` で確認してから動かす。
- 末尾の `{% else %} auth: type: {{ provider.type.value }}` は vertex 用に残る。そのままでよい。

### 2. `.env` の出力

`generator/context.py` の `_PROVIDER_ENV` に `BEDROCK: ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]` は既にある。v2 構成でも `_build_env_vars` がこれを通って `.env` に両キーが出ることをテストで確認する。出ていなければ v2 の分岐を直す。

### 3. テスト

- `tests/test_kongctl.py` の `test_multiple_providers_emit_multiple_model_providers` を拡張するか新テストを足し、bedrock プロバイダの `config.auth` が `{type: aws, access_key_id: !env AWS_ACCESS_KEY_ID, secret_access_key: !env AWS_SECRET_ACCESS_KEY}` であること、プロバイダ `config` に `region` が無いこと、モデルの `targets[0].config` が `{type: bedrock, region: ...}` で `deployment_id` を含まないことを assert する。`!env` タグの比較は同ファイルの `{"__tag__": "!file", ...}` の書き方に倣う。
- `.env` 生成のテスト（`tests/test_writer.py` か `tests/test_context.py` の既存の env テストの隣）に、v2 + bedrock で `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` が出ることを足す。
- golden: `examples/aigw-v2-konnect-bedrock.yaml` を新設し（`aigw-v2-konnect-minimal.yaml` をベースに provider を bedrock、`region: ap-northeast-1`、モデル名は Bedrock の cross-region inference profile 名を 1 つ）、`tests/golden/aigw-v2-konnect-bedrock/` を生成する。golden の生成・更新手順は `tests/test_golden.py` の冒頭に従う。

### 4. ドキュメント

- `README.md` の examples 表に `aigw-v2-konnect-bedrock.yaml` を 1 行足す。
- `docs/superpowers/known-gaps.md` の「プロバイダ認証の対応表が 2 箇所にある」から bedrock を外し、vertex だけが残る文に直す。
- `.claude/skills/kong-labkit-env/SKILL.md` の「検証は通るが動かないもの」の `v2 で bedrock / vertex` 行を vertex のみに直す。
- `generator/templates/README.md.j2` に、bedrock のとき `.env` に `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` を埋める旨が出るか確認する。azure の `AZURE_OPENAI_API_KEY` が「前提」節に出ている仕組みが `.env` の内容から導出されているなら追加作業なし。

### 5. 検証

- `uv run pytest` を全件通す。
- `uv run python scripts/update_schema.py` を実行し、`schemas/env.schema.json` に差分が出ないことを確認する（schema.py は触らない想定なので差分は無いはず）。
- 新 example から実際に環境を生成し、`CONTROL_PLANE_ID` 以外を埋めずに `kongctl` のドライラン（`mise run diff` 相当）を実行して、Konnect が `auth.type: aws` と `targets[].config.region` を受理することを確認する。Konnect の PAT は手元の toray 環境の `.env` にあるものを使ってよいが、`mise run sync` までは実行せず diff で止める。
- 実 Bedrock への疎通確認は AWS 資格情報が必要なので本タスクの範囲外。diff が通れば完了とする。

## コミット

1 コミットでよい。メッセージ例: `feat: ai-gateway-v2 で bedrock プロバイダの aws 認証と region を出す`。
