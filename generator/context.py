from generator.schema import (
    CacheType,
    ControlPlane,
    EnvSpec,
    IdpType,
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
