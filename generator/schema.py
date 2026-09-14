from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ProviderType(StrEnum):
    AZURE = "azure"
    BEDROCK = "bedrock"
    VERTEX = "vertex"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class AuthType(StrEnum):
    API_KEY = "api-key"
    MANAGED_IDENTITY = "managed-identity"


class Strict(BaseModel):
    # 未知のキーを黙って捨てると、綴り間違いが「設定したのに効かない」として表れる
    model_config = ConfigDict(extra="forbid")


class Model(Strict):
    name: str
    deployment_id: str | None = None
    api_version: str | None = None


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


class ObservabilitySpec(Strict):
    otel_lgtm: bool = True


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
    upstream: UpstreamType | None = None

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
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return EnvSpec.model_validate(data)
