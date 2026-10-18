import ipaddress
import re
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class Target(StrEnum):
    COMPOSE = "compose"
    KUBERNETES = "kubernetes"
    CLOUD_RUN = "cloud-run"
    ACA = "aca"


class Gateway(StrEnum):
    AI_GATEWAY_V2 = "ai-gateway-v2"
    AI_GATEWAY_V1 = "ai-gateway-v1"
    API_GATEWAY = "api-gateway"


class ControlPlane(StrEnum):
    KONNECT = "konnect"
    SELF_MANAGED = "self-managed"


class IdpType(StrEnum):
    KEYCLOAK = "keycloak"
    ENTRA_ID = "entra-id"
    LDAP = "ldap"
    NONE = "none"


class CacheType(StrEnum):
    REDIS = "redis"
    REDIS_STACK = "redis-stack"
    NONE = "none"


class VectorDbType(StrEnum):
    REDIS_STACK = "redis-stack"
    PGVECTOR = "pgvector"
    NONE = "none"


class UpstreamType(StrEnum):
    HTTPBIN = "httpbin"
    NONE = "none"


class ObservabilityBackend(StrEnum):
    OTEL_LGTM = "otel-lgtm"
    NEWRELIC = "newrelic"
    NONE = "none"


class NewRelicRegion(StrEnum):
    US = "us"
    EU = "eu"


class SecretsBackend(StrEnum):
    ENV = "env"
    VAULT = "vault"


class ProviderType(StrEnum):
    AZURE = "azure"
    BEDROCK = "bedrock"
    VERTEX = "vertex"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class PromptGuardMode(StrEnum):
    NONE = "none"
    OBSERVE = "observe"
    BLOCK = "block"


class AuthType(StrEnum):
    API_KEY = "api-key"
    MANAGED_IDENTITY = "managed-identity"
    # bedrock 専用。STS の一時クレデンシャル（ASIA...）は session_token が無いと AWS が弾く
    SESSION_TOKEN = "session-token"


class Strict(BaseModel):
    # 未知のキーを黙って捨てると、綴り間違いが「設定したのに効かない」として表れる
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def _literal_path(value: str) -> str:
    if (
        not re.fullmatch(r"/[A-Za-z0-9._~/-]*", value)
        or "//" in value
        or any(part in (".", "..") for part in value.split("/"))
    ):
        raise ValueError("path は dot segment のない literal ASCII 絶対 path にしてください")
    return value


def _upstream_url(value: str) -> str:
    # Parse only: no DNS lookup or HTTP request. Reasons never include the rejected input.
    def reject(reason: str):
        return ValueError(reason)

    if any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise reject("空白・制御文字・非 ASCII 文字は使えません")
    if any(c in value for c in ("@", "?", "#", "%", "\\", "$", "{", "}")):
        raise reject("userinfo・query・fragment・percent-encoding・シェル展開文字は使えません")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise reject("scheme は http / https のみ指定できます")
    if not parts.netloc or not parts.hostname:
        raise reject("host が空です")
    host = parts.hostname
    authority = (r"\[[0-9A-Fa-f:.]+\](?::[0-9]+)?" if ":" in host
                 else r"[A-Za-z0-9.-]+(?::[0-9]+)?")
    if not re.fullmatch(authority, parts.netloc) or parts.netloc.endswith(":"):
        raise reject("host[:port] の形式が不正です")
    try:
        port = parts.port
    except ValueError:
        raise reject("port は 1〜65535 の整数にしてください") from None
    if port is not None and not 1 <= port <= 65535:
        raise reject("port は 1〜65535 の整数にしてください")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        dns = host.removesuffix(".")
        if dns.lower() == "localhost" or dns.lower().endswith(".localhost"):
            raise reject("localhost は DP 自身を指すため指定できません") from None
        # Reject legacy inet_aton decimal/octal/hex forms, not just dotted IPv4.
        if all(re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)", label) for label in dns.split(".")):
            raise reject("数値のみの host は dotted-quad IPv4 で指定してください") from None
        if len(dns) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in dns.split(".")
        ):
            raise reject("host は RFC 1123 のホスト名にしてください") from None
    else:
        if address.is_loopback or (isinstance(address, ipaddress.IPv6Address)
                                   and address.ipv4_mapped and address.ipv4_mapped.is_loopback):
            raise reject("loopback アドレスは DP 自身を指すため指定できません")
    if parts.path:
        try:
            _literal_path(parts.path)
        except ValueError as e:
            raise reject(str(e)) from None
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


class UpstreamSmokeSpec(Strict):
    # Empty internal sentinel is used only for omission; explicit empty/null input is rejected.
    path: str = Field(default_factory=lambda: "", strict=True, description="省略時は route_path")
    expected_status: int = Field(default=200, strict=True, ge=200, le=299)

    @field_validator("path")
    @classmethod
    def _check_path(cls, value):
        return _literal_path(value)


class CustomUpstreamSpec(Strict):
    url: str = Field(strict=True)
    route_path: str = Field(default="/upstream", strict=True)
    strip_path: bool = Field(default=True, strict=True)
    smoke: UpstreamSmokeSpec = Field(default_factory=UpstreamSmokeSpec)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value):
        return _upstream_url(value)

    @field_validator("route_path")
    @classmethod
    def _check_route_path(cls, value):
        _literal_path(value)
        if value != "/" and value.endswith("/"):
            raise ValueError("route_path の末尾 slash は root 以外では指定できません")
        return value

    @model_validator(mode="after")
    def _resolve_smoke(self):
        if self.smoke.path == "":
            self.smoke.path = self.route_path
        if self.route_path != "/" and not (
            self.smoke.path == self.route_path or self.smoke.path.startswith(self.route_path + "/")
        ):
            raise ValueError("smoke.path は route_path またはその配下にしてください")
        return self


class Model(Strict):
    name: str
    # v2 の targets[].name に渡す上流側のモデル ID。name は ref とルートパスになるので
    # Bedrock の inference profile ID（":0" 付き）のようにコロンを含む値はこちらに書く
    model_id: str | None = None
    deployment_id: str | None = None
    api_version: str | None = None
    # 100 万トークンあたりの USD。ai-proxy-advanced の input_cost / output_cost にそのまま渡す
    input_cost: float | None = None
    output_cost: float | None = None


class Provider(Strict):
    type: ProviderType
    instance: str | None = None
    region: str | None = None
    auth: AuthType = AuthType.API_KEY
    models: list[Model] = Field(default_factory=list)


class AiSpec(Strict):
    providers: list[Provider] = Field(default_factory=list)
    semantic_cache: bool = False
    semantic_routing: bool = False
    embedding_model: str = "text-embedding-3-large"
    prompt_guard: PromptGuardMode = PromptGuardMode.NONE
    prompt_compressor: bool = False


class LdapUser(Strict):
    name: str
    email: str | None = None
    password: str | None = None
    groups: list[str] = Field(default_factory=list)


class IdpSpec(Strict):
    type: IdpType = IdpType.NONE
    realm: str | None = None
    users: list[LdapUser] = Field(default_factory=list)


class CacheSpec(Strict):
    type: CacheType = CacheType.NONE


class VectorDbSpec(Strict):
    type: VectorDbType = VectorDbType.NONE


class NewRelicSpec(Strict):
    region: NewRelicRegion = NewRelicRegion.US


class SecretsSpec(Strict):
    backend: SecretsBackend = SecretsBackend.ENV


class ObservabilitySpec(Strict):
    backend: ObservabilityBackend = ObservabilityBackend.OTEL_LGTM
    newrelic: NewRelicSpec | None = None

    @model_validator(mode="after")
    def _check_newrelic_block(self):
        if self.newrelic and self.backend is not ObservabilityBackend.NEWRELIC:
            raise ValueError(
                "observability.newrelic は observability.backend: newrelic のときだけ"
                f"指定できます（いまは backend: {self.backend.value} で、この設定はどこにも出ません）。"
            )
        return self


class EnvSpec(Strict):
    customer: str
    target: Target = Target.COMPOSE
    gateway: Gateway
    control_plane: ControlPlane = ControlPlane.KONNECT
    # Konnect 上の見せ方だけを customer から切り離すための上書き
    konnect_name: str | None = None
    region: str = "us"
    ai: AiSpec = Field(default_factory=AiSpec)
    idp: IdpSpec = Field(default_factory=IdpSpec)
    cache: CacheSpec = Field(default_factory=CacheSpec)
    vectordb: VectorDbSpec = Field(default_factory=VectorDbSpec)
    observability: ObservabilitySpec = Field(default_factory=ObservabilitySpec)
    secrets: SecretsSpec = Field(default_factory=SecretsSpec)
    upstream: UpstreamType | CustomUpstreamSpec | None = None

    @field_validator("upstream", mode="before")
    @classmethod
    def _parse_custom_upstream(cls, value):
        if isinstance(value, dict):
            try:
                return CustomUpstreamSpec.model_validate(value)
            except ValidationError as e:
                # Re-raise as ValueError so the union does not append its own branch errors.
                # loc of extra_forbidden is the user-typed key itself, so it is never echoed.
                reasons = []
                for err in e.errors(include_input=False, include_context=False, include_url=False):
                    if err["type"] == "extra_forbidden":
                        parent = ".".join(str(p) for p in err["loc"][:-1])
                        reasons.append(f"{parent or 'upstream'} に未知のキーがあります")
                        continue
                    loc = ".".join(str(p) for p in err["loc"])
                    msg = err["msg"].removeprefix("Value error, ")
                    reasons.append(f"{loc}: {msg}" if loc and not msg.startswith(loc) else msg)
                raise ValueError("upstream mapping が不正です: " + "; ".join(reasons)) from None
        return value

    @model_validator(mode="after")
    def _check_custom_scope(self):
        if isinstance(self.upstream, CustomUpstreamSpec) and not (
            self.target is Target.COMPOSE
            and self.gateway is Gateway.API_GATEWAY
            and self.control_plane is ControlPlane.SELF_MANAGED
            and self.idp.type is IdpType.NONE
        ):
            raise ValueError("custom upstream は compose / api-gateway / self-managed / idp:none のみ対応しています")
        return self

    @property
    def is_ai(self) -> bool:
        return self.gateway in (Gateway.AI_GATEWAY_V2, Gateway.AI_GATEWAY_V1)

    @model_validator(mode="after")
    def _resolve_upstream(self):
        if self.upstream is None:
            default = (
                UpstreamType.HTTPBIN
                if self.gateway is Gateway.API_GATEWAY
                else UpstreamType.NONE
            )
            object.__setattr__(self, "upstream", default)
        return self

    @model_validator(mode="after")
    def _check_target(self):
        if self.target is not Target.COMPOSE:
            raise ValueError(
                f"target: {self.target} は v1 では未対応です。"
                "現在は compose のみ対応しています。"
            )
        return self

    @model_validator(mode="after")
    def _check_rule1_aigw_v2_is_konnect_only(self):
        if (
            self.gateway is Gateway.AI_GATEWAY_V2
            and self.control_plane is ControlPlane.SELF_MANAGED
        ):
            raise ValueError(
                "ai-gateway-v2 は Konnect 専用です"
                "（データプレーンが KONG_KONNECT_MODE: on を前提にしているため、"
                "自前の Control Plane に接続する構成が存在しません）。"
                "control_plane: konnect にするか、gateway: ai-gateway-v1 にしてください。"
            )
        return self

    @model_validator(mode="after")
    def _check_rule2_semantic_needs_vectordb(self):
        semantic = self.ai.semantic_cache or self.ai.semantic_routing
        if semantic and self.vectordb.type is VectorDbType.NONE:
            raise ValueError(
                "ai.semantic_cache / ai.semantic_routing を有効にする場合は、"
                "vectordb.type を redis-stack か pgvector にしてください"
                "（semantic balancer は vectordb ブロックが必須で、"
                "未設定のまま sync すると kongctl が失敗します）。"
            )
        return self

    @model_validator(mode="after")
    def _check_rule3_redis_stack_vectordb(self):
        if (
            self.vectordb.type is VectorDbType.REDIS_STACK
            and self.cache.type is not CacheType.REDIS_STACK
        ):
            raise ValueError(
                "vectordb.type: redis-stack には cache.type: redis-stack が必要です"
                "（素の redis には検索モジュールが無く、none ではコンテナ自体が存在しません）。"
                "cache.type を redis-stack にするか、vectordb.type を pgvector にしてください。"
            )
        return self

    @model_validator(mode="after")
    def _check_keycloak_realm(self):
        if self.idp.type is IdpType.KEYCLOAK and not self.idp.realm:
            raise ValueError(
                "idp.type: keycloak のときは idp.realm を指定してください"
                "（realm 名が issuer URL と realm-export.json の両方に入ります）。"
            )
        return self

    @model_validator(mode="after")
    def _check_konnect_name(self):
        if self.konnect_name and self.control_plane is not ControlPlane.KONNECT:
            raise ValueError(
                "konnect_name は control_plane: konnect のときだけ指定できます"
                "（self-managed には Konnect の Control Plane が存在せず、指定しても効きません）。"
            )
        return self

    @model_validator(mode="after")
    def _check_ldap_users(self):
        if self.idp.users and self.idp.type is not IdpType.LDAP:
            raise ValueError(
                "idp.users は idp.type: ldap のときだけ指定できます"
                f"（いまは idp.type: {self.idp.type.value} で、この一覧はどこにも出ません）。"
            )
        seen: set[str] = set()
        for user in self.idp.users:
            if user.name in seen:
                raise ValueError(
                    f"idp.users の name が重複しています: {user.name}"
                    "（LDAP のユーザー名と Kong の Consumer 名の両方になるため、一意にしてください）。"
                )
            seen.add(user.name)
        return self

    @model_validator(mode="after")
    def _check_ldap_needs_a_kong_yaml(self):
        if self.idp.type is IdpType.LDAP and self.gateway is Gateway.AI_GATEWAY_V2:
            raise ValueError(
                "idp.type: ldap は ai-gateway-v2 では使えません"
                "（v2 の設定は config/kongctl.yaml の AI Gateway エンティティだけで、"
                "プラグインを書く経路が generator にありません）。"
                "gateway: ai-gateway-v1 か api-gateway にしてください。"
            )
        return self

    @model_validator(mode="after")
    def _check_vault_needs_the_deck_path(self):
        if (
            self.secrets.backend is SecretsBackend.VAULT
            and self.gateway is Gateway.AI_GATEWAY_V2
        ):
            raise ValueError(
                "secrets.backend: vault は ai-gateway-v2 では使えません"
                "（v2 の設定は config/kongctl.yaml の AI Gateway エンティティだけで、"
                "そこが vault 参照を受け付けるか検証できていません）。"
                "gateway: ai-gateway-v1 か api-gateway にしてください。"
            )
        return self

    @model_validator(mode="after")
    def _check_prompt_guard_needs_an_ai_gateway(self):
        if self.ai.prompt_guard is not PromptGuardMode.NONE and not self.is_ai:
            raise ValueError(
                f"ai.prompt_guard: {self.ai.prompt_guard.value} は "
                f"gateway: {self.gateway.value} では使えません"
                "（ai-prompt-guard は LLM リクエストの本文を検査するプラグインで、"
                "api-gateway の upstream は LLM ではなく、検査する相手が居ません）。"
                "gateway: ai-gateway-v1 か ai-gateway-v2 にしてください。"
            )
        return self

    @model_validator(mode="after")
    def _check_observe_needs_the_v2_data_plane(self):
        if (
            self.ai.prompt_guard is PromptGuardMode.OBSERVE
            and self.gateway is not Gateway.AI_GATEWAY_V2
        ):
            raise ValueError(
                "ai.prompt_guard: observe は ai-gateway-v2 でだけ使えます"
                "（検知しても通す continue_on_detection が v1 のデータプレーンの "
                "ai-prompt-guard に無く、必ずブロックになります）。"
                "gateway: ai-gateway-v2 にするか、ai.prompt_guard: block にしてください。"
            )
        return self

    @model_validator(mode="after")
    def _check_prompt_compressor_needs_the_v2_data_plane(self):
        if self.ai.prompt_compressor and self.gateway is not Gateway.AI_GATEWAY_V2:
            raise ValueError(
                "ai.prompt_compressor は ai-gateway-v2 でだけ使えます"
                "（generator が ai-prompt-compressor を書く経路は config/kongctl.yaml の policies だけで、"
                "kong.yaml 側には出していません）。"
                "gateway: ai-gateway-v2 にするか、ai.prompt_compressor を外してください。"
            )
        return self

    @model_validator(mode="after")
    def _check_vault_needs_a_secret(self):
        # managed-identity は資格情報を持たないので、provider が居ても Vault には何も入らない
        has_secret = self.idp.type is not IdpType.NONE or any(
            not (p.type is ProviderType.AZURE and p.auth is AuthType.MANAGED_IDENTITY)
            for p in self.ai.providers
        )
        if self.secrets.backend is SecretsBackend.VAULT and not has_secret:
            raise ValueError(
                "secrets.backend: vault を指定していますが、Vault に入れる秘密がありません"
                "（秘密を持つプロバイダが 1 つも無く idp.type: none のままだと、"
                "kong.yaml に秘密の値が 1 つも生まれず、Vault も vault-bootstrap も空の入れ物になります）。"
                "ai.providers にプロバイダを足すか、idp.type を none 以外にするか、"
                "secrets.backend を env に戻してください。"
            )
        return self

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.idp.type is IdpType.ENTRA_ID:
            out.append(
                "idp.type: entra-id のため .env の AZURE_TENANT_ID / AZURE_CLIENT_ID / "
                "AZURE_CLIENT_SECRET は空欄で生成します。"
                "Entra テナント側の app registration は手作業で行い、値を .env に転記してください。"
            )
        return out


def load_spec(path: Path) -> EnvSpec:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        position = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise ValueError("YAML 構文が不正です" + position) from None
    return EnvSpec.model_validate(data)
