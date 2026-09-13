from dataclasses import dataclass

from generator.schema import (
    AuthType,
    CacheType,
    ControlPlane,
    EnvSpec,
    Gateway,
    IdpType,
    ProviderType,
    UpstreamType,
    VectorDbType,
)


class PortConflictError(Exception):
    pass


PREFERRED_PORTS: dict[str, int] = {
    "proxy": 8000,
    "status": 8100,
    "admin": 8001,
    "manager": 8002,
    "grafana": 3000,
    "otlp_grpc": 4317,
    "otlp_http": 4318,
    "keycloak": 8080,
    "httpbin": 8081,
    "cache": 6379,
    "kong_pg": 5432,
    "vector_pg": 5432,
}

# kong_pg より後に置くことで、両方ある構成では vector_pg がずれる
_ALLOCATION_ORDER = [
    "proxy",
    "status",
    "admin",
    "manager",
    "grafana",
    "otlp_grpc",
    "otlp_http",
    "keycloak",
    "httpbin",
    "cache",
    "kong_pg",
    "vector_pg",
]


def _required_keys(spec: EnvSpec) -> set[str]:
    keys = {"proxy", "status"}
    if spec.control_plane is ControlPlane.SELF_MANAGED:
        keys |= {"admin", "manager", "kong_pg"}
    if spec.observability.otel_lgtm:
        keys |= {"grafana", "otlp_grpc", "otlp_http"}
    if spec.idp.type is IdpType.KEYCLOAK:
        keys.add("keycloak")
    if spec.upstream is UpstreamType.HTTPBIN:
        keys.add("httpbin")
    if spec.cache.type is not CacheType.NONE:
        keys.add("cache")
    if spec.vectordb.type is VectorDbType.PGVECTOR:
        keys.add("vector_pg")
    return keys


def allocate_ports(spec: EnvSpec) -> dict[str, int]:
    required = _required_keys(spec)
    assigned: dict[str, int] = {}
    owner_of: dict[int, str] = {}

    for key in _ALLOCATION_ORDER:
        if key not in required:
            continue
        port = PREFERRED_PORTS[key]
        if port in owner_of:
            # Kong のメタストアと pgvector が両方 5432 を希望するのは想定内なので退避させる
            if key == "vector_pg":
                port += 1
            if port in owner_of:
                raise PortConflictError(
                    f"ポート {port} を {owner_of[port]} と {key} が同時に要求しています。"
                    f"generator/context.py の PREFERRED_PORTS で {key} の値を変更してください。"
                )
        assigned[key] = port
        owner_of[port] = key

    return assigned


class UnknownEmbeddingModelError(Exception):
    pass


KONG_IMAGES = {
    Gateway.AI_GATEWAY_V2: "kong/kong-ai-gateway:2.0.1",
    Gateway.AI_GATEWAY_V1: "kong/kong-gateway:3.14",
    Gateway.API_GATEWAY: "kong/kong-gateway:3.14",
}

CACHE_IMAGES = {
    CacheType.REDIS: "redis:8.0.2",
    CacheType.REDIS_STACK: "redis/redis-stack:7.4.0-v3",
}

EMBEDDING_DIMENSIONS = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
    "amazon.titan-embed-text-v2:0": 1024,
}

# Konnect DP の識別に使うラベル。手元の macOS からの接続であることが Konnect 上で見分けられる
DP_LABELS = "type:docker-macOsArmOS"


@dataclass(frozen=True)
class KongCtx:
    image: str
    cp_name: str
    konnect_domain: str | None
    konnect_telemetry_domain: str | None
    dp_labels: str


@dataclass(frozen=True)
class IdpCtx:
    type: IdpType
    enabled: bool
    realm: str | None
    issuer: str | None
    client_id: str | None
    service_name: str | None


@dataclass(frozen=True)
class OtelCtx:
    enabled: bool
    endpoint: str | None
    grpc_endpoint: str | None


@dataclass(frozen=True)
class CacheCtx:
    enabled: bool
    type: CacheType
    image: str | None
    host: str | None
    port: int


@dataclass(frozen=True)
class VectorCtx:
    enabled: bool
    type: VectorDbType
    host: str | None
    port: int
    dimensions: int
    distance_metric: str
    threshold: float
    database: str
    user: str


@dataclass(frozen=True)
class UpstreamCtx:
    enabled: bool
    service_name: str | None
    url: str | None


@dataclass(frozen=True)
class Ctx:
    spec: EnvSpec
    kong: KongCtx
    idp: IdpCtx
    otel: OtelCtx
    cache: CacheCtx
    vector: VectorCtx
    upstream: UpstreamCtx
    ports: dict[str, int]
    services: list[str]
    env_vars: dict[str, str]
    namespace: str


def _build_kong(spec: EnvSpec) -> KongCtx:
    konnect = spec.control_plane is ControlPlane.KONNECT
    suffix = "ai-gateway" if spec.is_ai else "gateway"
    return KongCtx(
        image=KONG_IMAGES[spec.gateway],
        cp_name=f"{spec.customer}-{suffix}",
        konnect_domain=f"{spec.region}.cp.konghq.com" if konnect else None,
        konnect_telemetry_domain=f"{spec.region}.tp.konghq.com" if konnect else None,
        dp_labels=DP_LABELS,
    )


def _build_idp(spec: EnvSpec) -> IdpCtx:
    if spec.idp.type is IdpType.KEYCLOAK:
        return IdpCtx(
            type=IdpType.KEYCLOAK,
            enabled=True,
            realm=spec.idp.realm,
            issuer=f"http://keycloak:8080/realms/{spec.idp.realm}",
            client_id=f"{spec.customer}-client",
            service_name="keycloak",
        )
    if spec.idp.type is IdpType.ENTRA_ID:
        # テナント ID は生成時に判明しないため compose の変数参照のまま Kong に渡す
        return IdpCtx(
            type=IdpType.ENTRA_ID,
            enabled=True,
            realm=None,
            issuer="https://login.microsoftonline.com/${AZURE_TENANT_ID}/v2.0",
            client_id="${AZURE_CLIENT_ID}",
            service_name=None,
        )
    return IdpCtx(
        type=IdpType.NONE, enabled=False, realm=None, issuer=None, client_id=None, service_name=None
    )


def _build_otel(spec: EnvSpec) -> OtelCtx:
    if not spec.observability.otel_lgtm:
        return OtelCtx(enabled=False, endpoint=None, grpc_endpoint=None)
    return OtelCtx(
        enabled=True,
        endpoint="http://otel-lgtm:4318",
        grpc_endpoint="http://otel-lgtm:4317",
    )


def _build_cache(spec: EnvSpec) -> CacheCtx:
    if spec.cache.type is CacheType.NONE:
        return CacheCtx(enabled=False, type=CacheType.NONE, image=None, host=None, port=6379)
    return CacheCtx(
        enabled=True,
        type=spec.cache.type,
        image=CACHE_IMAGES[spec.cache.type],
        host="redis",
        port=6379,
    )


def _build_vector(spec: EnvSpec) -> VectorCtx:
    if spec.vectordb.type is VectorDbType.NONE:
        return VectorCtx(
            enabled=False,
            type=VectorDbType.NONE,
            host=None,
            port=0,
            dimensions=0,
            distance_metric="cosine",
            threshold=0.7,
            database="",
            user="",
        )

    model = spec.ai.embedding_model
    if model not in EMBEDDING_DIMENSIONS:
        raise UnknownEmbeddingModelError(
            f"埋め込みモデル {model} の次元数が分かりません。"
            "generator/context.py の EMBEDDING_DIMENSIONS にモデル名と次元数を追加してください。"
        )

    if spec.vectordb.type is VectorDbType.REDIS_STACK:
        host, port, database, user = "redis", 6379, "", ""
    else:
        host, port, database, user = "pgvector", 5432, "vectors", "kong"

    return VectorCtx(
        enabled=True,
        type=spec.vectordb.type,
        host=host,
        # コンテナ内のポート。ホスト側の退避（5433）は ports 側だけの話
        port=port,
        dimensions=EMBEDDING_DIMENSIONS[model],
        distance_metric="cosine",
        threshold=0.7,
        database=database,
        user=user,
    )


def _build_upstream(spec: EnvSpec) -> UpstreamCtx:
    if spec.upstream is UpstreamType.HTTPBIN:
        return UpstreamCtx(enabled=True, service_name="httpbin", url="http://httpbin:80")
    return UpstreamCtx(enabled=False, service_name=None, url=None)


def _build_services(spec: EnvSpec) -> list[str]:
    services: list[str] = []
    if spec.gateway is Gateway.AI_GATEWAY_V2:
        services.append("kong-aigw-v2")
    elif spec.control_plane is ControlPlane.SELF_MANAGED:
        services.append("kong-self-managed")
    else:
        services.append("kong-dp")

    if spec.upstream is UpstreamType.HTTPBIN:
        services.append("httpbin")
    if spec.cache.type is not CacheType.NONE:
        services.append("redis")
    if spec.vectordb.type is VectorDbType.PGVECTOR:
        services.append("pgvector")
    if spec.idp.type is IdpType.KEYCLOAK:
        services.append("keycloak")
    if spec.observability.otel_lgtm:
        services.append("otel-lgtm")
    return services


_PROVIDER_ENV = {
    ProviderType.OPENAI: ["OPENAI_API_KEY"],
    ProviderType.ANTHROPIC: ["ANTHROPIC_API_KEY"],
    ProviderType.VERTEX: ["GOOGLE_APPLICATION_CREDENTIALS"],
    ProviderType.BEDROCK: ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
}


def _build_env_vars(spec: EnvSpec) -> dict[str, str]:
    env: dict[str, str] = {}

    if spec.control_plane is ControlPlane.KONNECT:
        env["CONTROL_PLANE_ID"] = ""
    else:
        env["KONG_LICENSE_DATA"] = ""

    for provider in spec.ai.providers:
        if provider.type is ProviderType.AZURE:
            if provider.auth is AuthType.API_KEY:
                env["AZURE_OPENAI_API_KEY"] = ""
            else:
                env["AZURE_TENANT_ID"] = ""
                env["AZURE_CLIENT_ID"] = ""
        else:
            for key in _PROVIDER_ENV.get(provider.type, []):
                env[key] = ""

    if spec.idp.type is IdpType.KEYCLOAK:
        env["KEYCLOAK_ADMIN"] = "admin"
        env["KEYCLOAK_ADMIN_PASSWORD"] = "admin"
        # ローカル検証用の固定値。乱数にするとゴールデンテストが毎回落ちる
        env["KEYCLOAK_CLIENT_SECRET"] = "local-dev-secret"
    elif spec.idp.type is IdpType.ENTRA_ID:
        env["AZURE_TENANT_ID"] = ""
        env["AZURE_CLIENT_ID"] = ""
        env["AZURE_CLIENT_SECRET"] = ""

    return env


def build_context(spec: EnvSpec) -> Ctx:
    return Ctx(
        spec=spec,
        kong=_build_kong(spec),
        idp=_build_idp(spec),
        otel=_build_otel(spec),
        cache=_build_cache(spec),
        vector=_build_vector(spec),
        upstream=_build_upstream(spec),
        ports=allocate_ports(spec),
        services=_build_services(spec),
        env_vars=_build_env_vars(spec),
        namespace=spec.customer,
    )
