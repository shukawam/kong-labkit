from base64 import b64encode
from dataclasses import dataclass

from generator.schema import (
    AuthType,
    CacheType,
    ControlPlane,
    CustomUpstreamSpec,
    EnvSpec,
    Gateway,
    IdpType,
    LdapUser,
    NewRelicRegion,
    NewRelicSpec,
    ObservabilityBackend,
    ProviderType,
    PromptGuardMode,
    SecretsBackend,
    UpstreamType,
    VectorDbType,
)


OTEL_SERVICE: dict[ObservabilityBackend, str] = {
    ObservabilityBackend.OTEL_LGTM: "otel-lgtm",
    ObservabilityBackend.NEWRELIC: "otel-collector",
}

NEWRELIC_OTLP: dict[NewRelicRegion, str] = {
    NewRelicRegion.US: "https://otlp.nr-data.net",
    NewRelicRegion.EU: "https://otlp.eu01.nr-data.net",
}


VAULT_HOST = "vault"
VAULT_MOUNT = "secret"
VAULT_PATH_PREFIX = "kong"
# ラボ固定。乱数にするとゴールデンテストが毎回落ちる
VAULT_ROOT_TOKEN = "local-dev-token"


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
    "ldap": 3890,
    "lldap_web": 17170,
    "vault": 8200,
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
    "ldap",
    "lldap_web",
    "vault",
    "httpbin",
    "cache",
    "kong_pg",
    "vector_pg",
]


def _required_keys(spec: EnvSpec) -> set[str]:
    keys = {"proxy", "status"}
    if spec.control_plane is ControlPlane.SELF_MANAGED:
        keys |= {"admin", "manager", "kong_pg"}
    if spec.observability.backend is not ObservabilityBackend.NONE:
        keys |= {"otlp_grpc", "otlp_http"}
    if spec.observability.backend is ObservabilityBackend.OTEL_LGTM:
        keys.add("grafana")
    if spec.idp.type is IdpType.KEYCLOAK:
        keys.add("keycloak")
    if spec.idp.type is IdpType.LDAP:
        keys |= {"ldap", "lldap_web"}
    if spec.upstream is UpstreamType.HTTPBIN:
        keys.add("httpbin")
    if spec.cache.type is not CacheType.NONE:
        keys.add("cache")
    if spec.vectordb.type is VectorDbType.PGVECTOR:
        keys.add("vector_pg")
    if spec.secrets.backend is SecretsBackend.VAULT:
        keys.add("vault")
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
    Gateway.AI_GATEWAY_V2: "kong/kong-ai-gateway:2.0.3",
    Gateway.AI_GATEWAY_V1: "kong/kong-gateway:3.16",
    Gateway.API_GATEWAY: "kong/kong-gateway:3.16",
}

LLDAP_IMAGE = "lldap/lldap:v0.6.3-alpine"
# Cloudsmith の私有レジストリ。Kong Support からトークンをもらって docker login しないと pull できない
COMPRESSOR_IMAGE = "docker.cloudsmith.io/kong/ai-compress/service:v0.0.5"
# 日本語版は ja_core_news_lg しか同梱しないので、英語 PII を拾うには en モデルを足した自前イメージをビルドする
PII_SANITIZER_BASE_IMAGE = "docker.cloudsmith.io/kong/ai-pii/service:v0.2.0-ja"

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
class LdapUserCtx:
    name: str
    email: str
    password: str
    groups: tuple[str, ...]


@dataclass(frozen=True)
class LdapCtx:
    image: str
    host: str
    port: int
    web_port: int
    base_dn: str
    users_dn: str
    groups_dn: str
    admin_name: str
    bind_dn: str
    bind_password: str
    # kong.yaml が書く形。vault 構成では参照文字列になる
    bind_password_ref: str
    jwt_secret: str
    attribute: str
    users: tuple[LdapUserCtx, ...]
    groups: tuple[str, ...]


@dataclass(frozen=True)
class IdpCtx:
    type: IdpType
    enabled: bool
    # kong.yaml に出す認証プラグイン名。enabled だけでは OIDC と LDAP を分けられない
    auth_plugin: str | None
    ldap: LdapCtx | None
    realm: str | None
    issuer: str | None
    client_id: str | None
    # decK は ${VAR} を展開しないため、client secret の参照方法は idp の種類ごとに context が決める
    client_secret: str | None
    client_secret_ref: str | None
    service_name: str | None
    public_client_id: str | None
    token_endpoint: str | None
    test_username: str | None
    test_password: str | None

    @property
    def test_credentials_base64(self) -> str:
        credentials = f"{self.test_username}:{self.test_password}"
        return b64encode(credentials.encode()).decode("ascii")


@dataclass(frozen=True)
class OtelCtx:
    enabled: bool
    backend: ObservabilityBackend
    endpoint: str | None
    grpc_endpoint: str | None
    # backend: newrelic のときだけ入る、Collector から先の送信先
    nr_endpoint: str | None = None
    metrics_push_interval: int = 15


# v1 と v2 の両方の設定に同じ一覧を描画する。片側だけ直る形を作らないため 1 箇所に置く
PROMPT_GUARD_DENY_PATTERNS: tuple[str, ...] = (
    # 目に見えない文字で deny パターンを迂回されるのを防ぐ。ゼロ幅・双方向制御・タグ文字と BOM
    r"(\xE2\x80[\x8B-\x8D]|\xEF\xBB\xBF)",
    r"\xE2\x80[\xAA-\xAE]",
    r"\xE2\x81[\xA0-\xAF]",
    r"\xF3\xA0\x80[\xA0-\xBF]|\xF3\xA0\x81[\x80-\xBF]",
    r"(?i).*ignore (all|previous|the above).*(instructions|context|policy).*",
    r"(?i).*(override|forget) (all|your) (previous )?(instructions|prompt).*",
    r"(?i).*(system prompt|developer message).*(reveal|show|change|ignore).*",
    r"(?i)\b(reveal|repeat|print|output|disclose|dump)\b.{0,40}\b(system prompt|system message|initial prompt|developer message|your instructions|(the )?instructions above|everything above)\b|\bwhat (are|were) your (original |initial |system )?(instructions|rules|prompt)\b",
    r"(?i)\b(ignore|disregard|bypass|circumvent|turn off|disable)\b.{0,30}\b(safety|guardrails?|filters?|restrictions?|guidelines?|content polic\w+)\b|\b(dan mode|do anything now|developer mode enabled|jailbreak mode)\b|\bno longer bound by\b.{0,30}\b(rules|policy|policies|instructions)\b",
    r"(?i)\b(important|urgent|new|updated)\s+(instructions?|directives?)\s+for\s+(the\s+)?(ai|assistant|llm|language model|agent)\b|\bif\s+you(\S{0,2}re|\s+are)\s+(an?\s+)?(ai|llm|language model|assistant|agent)\b.{0,40}\b(you must|then you|ignore|do not|instead)\b|\b(ai|llm)\s+(assistant|agent)s?\b.{0,20}\breading this\b",
)


@dataclass(frozen=True)
class PromptGuardCtx:
    enabled: bool
    continue_on_detection: bool
    rejection_mode: str
    llm_format: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class PromptCompressorCtx:
    enabled: bool
    image: str
    host: str
    port: int
    # (min_tokens, max_tokens, 残す割合)
    ranges: tuple[tuple[int, int, float], ...]
    timeout_ms: int
    keepalive_timeout_ms: int


@dataclass(frozen=True)
class PiiSanitizerCtx:
    enabled: bool
    base_image: str
    build_dir: str
    host: str
    port: int
    # (lang_code, spaCy モデル名)
    languages: tuple[tuple[str, str], ...]


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
    custom: bool = False
    managed_service_name: str | None = None
    route_path: str | None = None
    strip_path: bool = True
    smoke_path: str | None = None
    expected_status: int = 200


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
    prompt_guard: PromptGuardCtx
    prompt_compressor: PromptCompressorCtx
    pii_sanitizer: PiiSanitizerCtx
    certs: CertsCtx
    deck: DeckCtx
    secrets: "SecretsCtx"
    ports: dict[str, int]
    services: list[str]
    env_vars: dict[str, str]
    namespace: str


def _build_kong(spec: EnvSpec) -> KongCtx:
    konnect = spec.control_plane is ControlPlane.KONNECT
    suffix = "ai-gateway" if spec.is_ai else "gateway"
    return KongCtx(
        image=KONG_IMAGES[spec.gateway],
        cp_name=f"{spec.konnect_name or spec.customer}-{suffix}",
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

LLDAP_ADMIN_NAME = "admin"
# lldap は admin パスワードに 8 文字以上を要求する
LLDAP_ADMIN_PASSWORD = "local-dev-password"
LLDAP_JWT_SECRET = "local-dev-jwt-secret"
LLDAP_TEST_USER = "tester"


@dataclass(frozen=True)
class SecretSeed:
    """vault-bootstrap が KV に投入する 1 項目。"""

    path: str
    key: str
    env_var: str | None
    prefix: str
    literal: str | None

    @property
    def shell_value(self) -> str:
        """bootstrap の sh から見た値。環境変数由来なら展開式、固定値ならそのまま。"""
        if self.env_var:
            return self.prefix + "${" + self.env_var + "}"
        return self.prefix + (self.literal or "")


@dataclass(frozen=True)
class SecretsCtx:
    enabled: bool
    host: str
    mount: str
    prefix: str
    token: str
    seeds: tuple[SecretSeed, ...]

    @property
    def seeds_by_path(self) -> dict[str, tuple[SecretSeed, ...]]:
        """vault kv put は 1 パスに複数キーをまとめて書けるので、パス単位に畳む。

        同種のプロバイダを複数並べると同じ (path, key) が seeds に重複して積まれるため、
        1 パスの中では key で一意化する（先勝ち）。
        """
        grouped: dict[str, dict[str, SecretSeed]] = {}
        for seed in self.seeds:
            grouped.setdefault(seed.path, {}).setdefault(seed.key, seed)
        return {path: tuple(seeds.values()) for path, seeds in grouped.items()}

    @property
    def env_sources(self) -> tuple[str, ...]:
        # dict.fromkeys で宣言順を保ったまま重複を潰す
        return tuple(dict.fromkeys(s.env_var for s in self.seeds if s.env_var))


class _SecretRefs:
    """設定に書く参照文字列を決め、必要な別名と Vault への投入項目を集める。

    decK が展開するのは ${{ env "DECK_..." }} だけで、裸の ${VAR} はただの文字列になる。
    vault 構成では参照文字列がそのまま設定に残り、解決はデータプレーンが行う。
    """

    def __init__(self, backend: SecretsBackend, deck_enabled: bool) -> None:
        self.backend = backend
        self.deck_enabled = deck_enabled
        self.aliases: dict[str, str] = {}
        self.seeds: list[SecretSeed] = []

    def env(self, source: str) -> str:
        """秘密ではない識別子。backend によらず decK の env 参照のままにする。"""
        alias = DECK_ENV_PREFIX + source
        if self.deck_enabled:
            self.aliases[alias] = source
        return '${{ env "' + alias + '" }}'

    def secret_from_env(self, source: str, *, path: str, key: str, prefix: str = "") -> str:
        if self.backend is SecretsBackend.VAULT:
            self.seeds.append(
                SecretSeed(path=path, key=key, env_var=source, prefix=prefix, literal=None)
            )
            return self._vault_ref(path, key)
        return prefix + self.env(source)

    def secret_from_literal(self, value: str, *, path: str, key: str) -> str:
        if self.backend is SecretsBackend.VAULT:
            self.seeds.append(
                SecretSeed(path=path, key=key, env_var=None, prefix="", literal=value)
            )
            return self._vault_ref(path, key)
        return value

    @staticmethod
    def _vault_ref(path: str, key: str) -> str:
        return "{vault://hcv/" + f"{VAULT_PATH_PREFIX}/{path}/{key}" + "}"


def _yaml_scalar(value: str) -> str:
    """decK 設定にそのまま置ける YAML スカラにする。${{ }} を裸で書くと flow mapping に見える。"""
    return "'" + value + "'"


def _build_ldap_users(spec: EnvSpec) -> tuple[LdapUserCtx, ...]:
    declared = spec.idp.users or [LdapUser(name=LLDAP_TEST_USER, groups=[f"{spec.customer}-ai-users"])]
    return tuple(
        LdapUserCtx(
            name=user.name,
            email=user.email or f"{user.name}@example.com",
            # lldap は 8 文字以上を要求するので、名前から導く既定値も十分な長さになる
            password=user.password or f"{user.name}-password",
            groups=tuple(user.groups),
        )
        for user in declared
    )


def _build_ldap(spec: EnvSpec, ports: dict[str, int], refs: _SecretRefs) -> LdapCtx:
    base_dn = f"dc={spec.customer},dc=local"
    users_dn = f"ou=people,{base_dn}"
    users = _build_ldap_users(spec)
    # dict.fromkeys で宣言順を保ったまま重複を潰す
    groups = tuple(dict.fromkeys(group for user in users for group in user.groups))
    return LdapCtx(
        image=LLDAP_IMAGE,
        host="lldap",
        port=ports["ldap"],
        web_port=ports["lldap_web"],
        base_dn=base_dn,
        users_dn=users_dn,
        groups_dn=f"ou=groups,{base_dn}",
        admin_name=LLDAP_ADMIN_NAME,
        # lldap は ldap_user_dn に素の名前を受け取り、cn=<name>,ou=people,<base> を組み立てる
        bind_dn=f"cn={LLDAP_ADMIN_NAME},{users_dn}",
        bind_password=LLDAP_ADMIN_PASSWORD,
        bind_password_ref=refs.secret_from_literal(
            LLDAP_ADMIN_PASSWORD, path="ldap", key="ldap_password"
        ),
        jwt_secret=LLDAP_JWT_SECRET,
        attribute="uid",
        users=users,
        groups=groups,
    )


def _build_idp(spec: EnvSpec, ports: dict[str, int], refs: _SecretRefs) -> IdpCtx:
    if spec.idp.type is IdpType.KEYCLOAK:
        realm = spec.idp.realm
        return IdpCtx(
            type=IdpType.KEYCLOAK,
            enabled=True,
            auth_plugin="openid-connect",
            ldap=None,
            realm=realm,
            issuer=f"http://keycloak:8080/realms/{realm}",
            client_id=f"{spec.customer}-client",
            # ローカル固定値なので decK の env 参照にせず実値を埋める。realm-export と出所を 1 つにする
            client_secret=KEYCLOAK_CLIENT_SECRET,
            client_secret_ref=refs.secret_from_literal(
                KEYCLOAK_CLIENT_SECRET, path="oidc", key="client_secret"
            ),
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
        tenant = refs.env("AZURE_TENANT_ID")
        return IdpCtx(
            type=IdpType.ENTRA_ID,
            enabled=True,
            auth_plugin="openid-connect",
            ldap=None,
            realm=None,
            issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
            client_id=refs.env("AZURE_CLIENT_ID"),
            client_secret=None,
            client_secret_ref=refs.secret_from_env(
                "AZURE_CLIENT_SECRET", path="oidc", key="client_secret"
            ),
            service_name=None,
            public_client_id=None,
            # 無人でトークンを取得する経路が無いため、スモークは応答コードの確認に落とす
            token_endpoint=None,
            test_username=None,
            test_password=None,
        )
    if spec.idp.type is IdpType.LDAP:
        ldap = _build_ldap(spec, ports, refs)
        return IdpCtx(
            type=IdpType.LDAP,
            enabled=True,
            auth_plugin="ldap-auth-advanced",
            ldap=ldap,
            realm=None,
            issuer=None,
            client_id=None,
            client_secret=None,
            client_secret_ref=None,
            service_name="lldap",
            public_client_id=None,
            token_endpoint=None,
            # スモークは先頭のユーザーで通す
            test_username=ldap.users[0].name,
            test_password=ldap.users[0].password,
        )
    return IdpCtx(
        type=IdpType.NONE,
        enabled=False,
        auth_plugin=None,
        ldap=None,
        realm=None,
        issuer=None,
        client_id=None,
        client_secret=None,
        client_secret_ref=None,
        service_name=None,
        public_client_id=None,
        token_endpoint=None,
        test_username=None,
        test_password=None,
    )


def _build_otel(spec: EnvSpec) -> OtelCtx:
    backend = spec.observability.backend
    if backend is ObservabilityBackend.NONE:
        return OtelCtx(
            enabled=False, backend=backend, endpoint=None, grpc_endpoint=None
        )
    host = OTEL_SERVICE[backend]
    nr_endpoint = None
    if backend is ObservabilityBackend.NEWRELIC:
        newrelic = spec.observability.newrelic or NewRelicSpec()
        nr_endpoint = NEWRELIC_OTLP[newrelic.region]
    return OtelCtx(
        enabled=True,
        backend=backend,
        endpoint=f"http://{host}:4318",
        grpc_endpoint=f"http://{host}:4317",
        nr_endpoint=nr_endpoint,
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
        return UpstreamCtx(enabled=True, service_name="httpbin", url="http://httpbin:80",
                           managed_service_name="httpbin")
    if isinstance(spec.upstream, CustomUpstreamSpec):
        upstream = spec.upstream
        return UpstreamCtx(enabled=True, service_name="upstream", url=upstream.url,
                           custom=True, route_path=upstream.route_path,
                           strip_path=upstream.strip_path, smoke_path=upstream.smoke.path,
                           expected_status=upstream.smoke.expected_status)
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
    if spec.ai.prompt_compressor:
        services.append("ai-compressor")
    if spec.ai.pii_sanitizer:
        services.append("pii-sanitizer")
    if spec.idp.type is IdpType.KEYCLOAK:
        services.append("keycloak")
    if spec.idp.type is IdpType.LDAP:
        services += ["lldap", "lldap-bootstrap"]
    if spec.secrets.backend is SecretsBackend.VAULT:
        services += ["vault", "vault-bootstrap"]
    if spec.observability.backend is not ObservabilityBackend.NONE:
        services.append(OTEL_SERVICE[spec.observability.backend])
    return services


_PROVIDER_ENV = {
    ProviderType.OPENAI: ["OPENAI_API_KEY"],
    ProviderType.ANTHROPIC: ["ANTHROPIC_API_KEY"],
    ProviderType.VERTEX: ["GOOGLE_APPLICATION_CREDENTIALS"],
    ProviderType.BEDROCK: ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
}


def _build_env_vars(spec: EnvSpec) -> dict[str, str]:
    env: dict[str, str] = {}

    if spec.observability.backend is ObservabilityBackend.NEWRELIC:
        env["NEW_RELIC_LICENSE_KEY"] = ""

    if spec.secrets.backend is SecretsBackend.VAULT:
        env["VAULT_TOKEN"] = VAULT_ROOT_TOKEN

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
            if provider.type is ProviderType.BEDROCK and provider.auth is AuthType.SESSION_TOKEN:
                env["AWS_SESSION_TOKEN"] = ""

    if spec.idp.type is IdpType.KEYCLOAK:
        env["KEYCLOAK_ADMIN"] = "admin"
        env["KEYCLOAK_ADMIN_PASSWORD"] = "admin"
        env["KEYCLOAK_CLIENT_SECRET"] = KEYCLOAK_CLIENT_SECRET
    elif spec.idp.type is IdpType.LDAP:
        env["LLDAP_ADMIN_USERNAME"] = LLDAP_ADMIN_NAME
        env["LLDAP_ADMIN_PASSWORD"] = LLDAP_ADMIN_PASSWORD
        env["LLDAP_JWT_SECRET"] = LLDAP_JWT_SECRET
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


def _build_provider_auth(provider, secrets: _SecretRefs) -> dict[str, str]:
    path = f"ai-{provider.type.value}"
    if provider.type is ProviderType.AZURE:
        if provider.auth is AuthType.MANAGED_IDENTITY:
            return {
                "azure_use_managed_identity": "true",
                "azure_client_id": _yaml_scalar(secrets.env("AZURE_CLIENT_ID")),
                "azure_tenant_id": _yaml_scalar(secrets.env("AZURE_TENANT_ID")),
            }
        return {
            "header_name": "api-key",
            "header_value": _yaml_scalar(
                secrets.secret_from_env("AZURE_OPENAI_API_KEY", path=path, key="header_value")
            ),
        }
    if provider.type is ProviderType.OPENAI:
        return {
            "header_name": "Authorization",
            "header_value": _yaml_scalar(
                secrets.secret_from_env(
                    "OPENAI_API_KEY", path=path, key="header_value", prefix="Bearer "
                )
            ),
        }
    if provider.type is ProviderType.ANTHROPIC:
        return {
            "header_name": "x-api-key",
            "header_value": _yaml_scalar(
                secrets.secret_from_env("ANTHROPIC_API_KEY", path=path, key="header_value")
            ),
        }
    if provider.type is ProviderType.BEDROCK:
        return {
            "aws_access_key_id": _yaml_scalar(
                secrets.secret_from_env("AWS_ACCESS_KEY_ID", path=path, key="aws_access_key_id")
            ),
            "aws_secret_access_key": _yaml_scalar(
                secrets.secret_from_env(
                    "AWS_SECRET_ACCESS_KEY", path=path, key="aws_secret_access_key"
                )
            ),
        }
    return {
        "gcp_use_service_account": "true",
        # パスではなくサービスアカウント JSON の中身そのものを渡す必要がある
        "gcp_service_account_json": _yaml_scalar(
            secrets.secret_from_env(
                "GOOGLE_APPLICATION_CREDENTIALS", path=path, key="gcp_service_account_json"
            )
        ),
    }


def _build_deck(spec: EnvSpec, secrets: _SecretRefs) -> DeckCtx:
    provider_auth = [_build_provider_auth(p, secrets) for p in spec.ai.providers]
    first = spec.ai.providers[0] if spec.ai.providers else None
    return DeckCtx(
        env_aliases=secrets.aliases,
        provider_auth=provider_auth,
        # 埋め込みモデルは先頭のプロバイダに属するものとして扱う
        embeddings_provider=first.type.value if first else None,
        embeddings_auth=provider_auth[0] if provider_auth else None,
    )


def _build_secrets(spec: EnvSpec, refs: _SecretRefs) -> SecretsCtx:
    enabled = spec.secrets.backend is SecretsBackend.VAULT
    return SecretsCtx(
        enabled=enabled,
        host=VAULT_HOST,
        mount=VAULT_MOUNT,
        prefix=VAULT_PATH_PREFIX,
        token=VAULT_ROOT_TOKEN,
        seeds=tuple(refs.seeds) if enabled else (),
    )


def _build_semantic(spec: EnvSpec) -> SemanticCtx:
    enabled = spec.vectordb.type is not VectorDbType.NONE
    return SemanticCtx(
        cache=spec.ai.semantic_cache and enabled,
        routing=spec.ai.semantic_routing and enabled,
    )


def _build_prompt_guard(spec: EnvSpec) -> PromptGuardCtx:
    mode = spec.ai.prompt_guard
    return PromptGuardCtx(
        enabled=mode is not PromptGuardMode.NONE,
        continue_on_detection=mode is PromptGuardMode.OBSERVE,
        # none だと検知しても本文に何も出ず、ラボで弾かれた理由を追えない
        rejection_mode="verbose",
        # v2 の model は formats: openai で出しているので、検査する形もそれに合わせる
        llm_format="openai",
        patterns=PROMPT_GUARD_DENY_PATTERNS,
    )


def _build_prompt_compressor(spec: EnvSpec) -> PromptCompressorCtx:
    return PromptCompressorCtx(
        enabled=spec.ai.prompt_compressor,
        image=COMPRESSOR_IMAGE,
        host="ai-compressor",
        port=8080,
        # 短いプロンプトは圧縮すると意味が崩れやすいので触らず、長いほど強く圧縮する
        ranges=((100, 1000, 0.8), (1000, 100000, 0.5)),
        # CPU 推論は長いプロンプトで既定の 10 秒を超える
        timeout_ms=30000,
        keepalive_timeout_ms=60000,
    )


def _build_pii_sanitizer(spec: EnvSpec) -> PiiSanitizerCtx:
    return PiiSanitizerCtx(
        enabled=spec.ai.pii_sanitizer,
        base_image=PII_SANITIZER_BASE_IMAGE,
        build_dir="build/pii-sanitizer",
        host="pii-sanitizer",
        port=8080,
        languages=(("ja", "ja_core_news_lg"), ("en", "en_core_web_lg")),
    )


def build_context(spec: EnvSpec) -> Ctx:
    ports = allocate_ports(spec)
    kong = _build_kong(spec)
    # ai-gateway-v2 は kongctl 経路なので decK の別名は要らない
    refs = _SecretRefs(spec.secrets.backend, deck_enabled=spec.gateway is not Gateway.AI_GATEWAY_V2)
    idp = _build_idp(spec, ports, refs)
    deck = _build_deck(spec, refs)
    return Ctx(
        spec=spec,
        kong=kong,
        idp=idp,
        otel=_build_otel(spec),
        cache=_build_cache(spec),
        vector=_build_vector(spec),
        upstream=_build_upstream(spec),
        semantic=_build_semantic(spec),
        prompt_guard=_build_prompt_guard(spec),
        prompt_compressor=_build_prompt_compressor(spec),
        pii_sanitizer=_build_pii_sanitizer(spec),
        certs=_build_certs(spec, kong),
        deck=deck,
        secrets=_build_secrets(spec, refs),
        ports=ports,
        services=_build_services(spec),
        env_vars=_build_env_vars(spec),
        namespace=spec.customer,
    )
