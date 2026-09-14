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
    konnect_api_url: str | None
    dp_labels: str


@dataclass(frozen=True)
class IdpCtx:
    type: IdpType
    enabled: bool
    realm: str | None
    issuer: str | None
    client_id: str | None
    # decK は ${VAR} を展開しないため、client secret の参照方法は idp の種類ごとに context が決める
    client_secret: str | None
    service_name: str | None
    public_client_id: str | None
    token_endpoint: str | None
    test_username: str | None
    test_password: str | None


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
    password: str


@dataclass(frozen=True)
class SemanticCtx:
    cache: bool
    routing: bool

    @property
    def enabled(self) -> bool:
        return self.cache or self.routing


@dataclass(frozen=True)
class CertsCtx:
    dir: str
    basename: str
    common_name: str
    days: int

    @property
    def crt_path(self) -> str:
        return f"{self.dir}/{self.basename}.crt"

    @property
    def key_path(self) -> str:
        return f"{self.dir}/{self.basename}.key"


@dataclass(frozen=True)
class DeckCtx:
    # DECK_ 付きの環境変数名 -> .env に入っている元の変数名
    env_aliases: dict[str, str]
    provider_auth: dict[str, dict[str, str]]
    embeddings_provider: str | None
    embeddings_auth: dict[str, str] | None

    @property
    def env_prefix(self) -> str:
        """deck の呼び出しに前置する環境変数の割り当て。decK は DECK_ 付きの名前しか見ない。"""
        if not self.env_aliases:
            return ""
        pairs = " ".join(
            f'{alias}="${source}"' for alias, source in sorted(self.env_aliases.items())
        )
        return pairs + " "


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
    semantic: SemanticCtx
    certs: CertsCtx
    deck: DeckCtx
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
        konnect_api_url=f"https://{spec.region}.api.konghq.com" if konnect else None,
        dp_labels=DP_LABELS,
    )


DECK_ENV_PREFIX = "DECK_"

KEYCLOAK_TEST_USER = "tester"
KEYCLOAK_TEST_PASSWORD = "tester"
# ローカル検証用の固定値。乱数にするとゴールデンテストが毎回落ちる
KEYCLOAK_CLIENT_SECRET = "local-dev-secret"
PGVECTOR_PASSWORD = "kong"


class _DeckEnv:
    """decK から参照する環境変数の名前を決め、必要な別名を集める。

    decK が展開するのは ${{ env "DECK_..." }} だけで、裸の ${VAR} はただの文字列になる。
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.aliases: dict[str, str] = {}

    def ref(self, source: str) -> str:
        alias = DECK_ENV_PREFIX + source
        if self.enabled:
            self.aliases[alias] = source
        return '${{ env "' + alias + '" }}'


def _yaml_scalar(value: str) -> str:
    """decK 設定にそのまま置ける YAML スカラにする。${{ }} を裸で書くと flow mapping に見える。"""
    return "'" + value + "'"


def _build_idp(spec: EnvSpec, ports: dict[str, int], deck_env: _DeckEnv) -> IdpCtx:
    if spec.idp.type is IdpType.KEYCLOAK:
        realm = spec.idp.realm
        return IdpCtx(
            type=IdpType.KEYCLOAK,
            enabled=True,
            realm=realm,
            issuer=f"http://keycloak:8080/realms/{realm}",
            client_id=f"{spec.customer}-client",
            # ローカル固定値なので decK の env 参照にせず実値を埋める。realm-export と出所を 1 つにする
            client_secret=KEYCLOAK_CLIENT_SECRET,
            service_name="keycloak",
            public_client_id=f"{spec.customer}-public",
            token_endpoint=(
                f"http://localhost:{ports['keycloak']}"
                f"/realms/{realm}/protocol/openid-connect/token"
            ),
            test_username=KEYCLOAK_TEST_USER,
            test_password=KEYCLOAK_TEST_PASSWORD,
        )
    if spec.idp.type is IdpType.ENTRA_ID:
        # テナント ID は生成時に判明しないため decK の env 参照として渡す
        tenant = deck_env.ref("AZURE_TENANT_ID")
        return IdpCtx(
            type=IdpType.ENTRA_ID,
            enabled=True,
            realm=None,
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
            client_id=deck_env.ref("AZURE_CLIENT_ID"),
            client_secret=deck_env.ref("AZURE_CLIENT_SECRET"),
            service_name=None,
            public_client_id=None,
            # 無人でトークンを取得する経路が無いため、スモークは応答コードの確認に落とす
            token_endpoint=None,
            test_username=None,
            test_password=None,
        )
    return IdpCtx(
        type=IdpType.NONE,
        enabled=False,
        realm=None,
        issuer=None,
        client_id=None,
        client_secret=None,
        service_name=None,
        public_client_id=None,
        token_endpoint=None,
        test_username=None,
        test_password=None,
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
            password="",
        )

    model = spec.ai.embedding_model
    if model not in EMBEDDING_DIMENSIONS:
        raise UnknownEmbeddingModelError(
            f"埋め込みモデル {model} の次元数が分かりません。"
            "generator/context.py の EMBEDDING_DIMENSIONS にモデル名と次元数を追加してください。"
        )

    if spec.vectordb.type is VectorDbType.REDIS_STACK:
        host, port, database, user, password = "redis", 6379, "", "", ""
    else:
        host, port, database, user, password = (
            "pgvector",
            5432,
            "vectors",
            "kong",
            PGVECTOR_PASSWORD,
        )

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
        password=password,
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
        env["KONNECT_PAT"] = ""
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
        env["KEYCLOAK_CLIENT_SECRET"] = KEYCLOAK_CLIENT_SECRET
    elif spec.idp.type is IdpType.ENTRA_ID:
        env["AZURE_TENANT_ID"] = ""
        env["AZURE_CLIENT_ID"] = ""
        env["AZURE_CLIENT_SECRET"] = ""

    return env


def _build_certs(spec: EnvSpec, kong: KongCtx) -> CertsCtx:
    if spec.control_plane is ControlPlane.KONNECT:
        return CertsCtx(dir=".certs", basename="cluster", common_name=kong.cp_name, days=1095)
    return CertsCtx(
        dir="config/kong/certs",
        basename="tls",
        # shared mTLS では Kong が KONG_CLUSTER_SERVER_NAME ではなく固定リテラル kong_clustering と照合する
        common_name="kong_clustering",
        days=1095,
    )


def _build_provider_auth(provider, deck_env: _DeckEnv) -> dict[str, str]:
    if provider.type is ProviderType.AZURE:
        if provider.auth is AuthType.MANAGED_IDENTITY:
            return {
                "azure_use_managed_identity": "true",
                "azure_client_id": _yaml_scalar(deck_env.ref("AZURE_CLIENT_ID")),
                "azure_tenant_id": _yaml_scalar(deck_env.ref("AZURE_TENANT_ID")),
            }
        return {
            "header_name": "api-key",
            "header_value": _yaml_scalar(deck_env.ref("AZURE_OPENAI_API_KEY")),
        }
    if provider.type is ProviderType.OPENAI:
        return {
            "header_name": "Authorization",
            "header_value": _yaml_scalar("Bearer " + deck_env.ref("OPENAI_API_KEY")),
        }
    if provider.type is ProviderType.ANTHROPIC:
        return {
            "header_name": "x-api-key",
            "header_value": _yaml_scalar(deck_env.ref("ANTHROPIC_API_KEY")),
        }
    if provider.type is ProviderType.BEDROCK:
        return {
            "aws_access_key_id": _yaml_scalar(deck_env.ref("AWS_ACCESS_KEY_ID")),
            "aws_secret_access_key": _yaml_scalar(deck_env.ref("AWS_SECRET_ACCESS_KEY")),
        }
    return {
        "gcp_use_service_account": "true",
        # パスではなくサービスアカウント JSON の中身そのものを渡す必要がある
        "gcp_service_account_json": _yaml_scalar(
            deck_env.ref("GOOGLE_APPLICATION_CREDENTIALS")
        ),
    }


def _build_deck(spec: EnvSpec, deck_env: _DeckEnv) -> DeckCtx:
    provider_auth = [_build_provider_auth(p, deck_env) for p in spec.ai.providers]
    first = spec.ai.providers[0] if spec.ai.providers else None
    return DeckCtx(
        env_aliases=deck_env.aliases,
        provider_auth=provider_auth,
        # 埋め込みモデルは先頭のプロバイダに属するものとして扱う
        embeddings_provider=first.type.value if first else None,
        embeddings_auth=provider_auth[0] if provider_auth else None,
    )


def _build_semantic(spec: EnvSpec) -> SemanticCtx:
    enabled = spec.vectordb.type is not VectorDbType.NONE
    return SemanticCtx(
        cache=spec.ai.semantic_cache and enabled,
        routing=spec.ai.semantic_routing and enabled,
    )


def build_context(spec: EnvSpec) -> Ctx:
    ports = allocate_ports(spec)
    kong = _build_kong(spec)
    # ai-gateway-v2 は kongctl 経路なので decK の別名は要らない
    deck_env = _DeckEnv(enabled=spec.gateway is not Gateway.AI_GATEWAY_V2)
    idp = _build_idp(spec, ports, deck_env)
    return Ctx(
        spec=spec,
        kong=kong,
        idp=idp,
        otel=_build_otel(spec),
        cache=_build_cache(spec),
        vector=_build_vector(spec),
        upstream=_build_upstream(spec),
        semantic=_build_semantic(spec),
        certs=_build_certs(spec, kong),
        deck=_build_deck(spec, deck_env),
        ports=ports,
        services=_build_services(spec),
        env_vars=_build_env_vars(spec),
        namespace=spec.customer,
    )
