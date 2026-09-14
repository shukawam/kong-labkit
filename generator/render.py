from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from generator.context import Ctx

TEMPLATE_DIR = Path(__file__).parent / "templates"


def build_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        # 未定義変数が空文字になると、一見正しい形の壊れた設定が黙って生成される
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def template_vars(ctx: Ctx) -> dict:
    return {
        "ctx": ctx,
        "spec": ctx.spec,
        "kong": ctx.kong,
        "idp": ctx.idp,
        "otel": ctx.otel,
        "cache": ctx.cache,
        "vector": ctx.vector,
        "upstream": ctx.upstream,
        "semantic": ctx.semantic,
        "certs": ctx.certs,
        "deck": ctx.deck,
        "ports": ctx.ports,
        "services": ctx.services,
        "env_vars": ctx.env_vars,
        "namespace": ctx.namespace,
    }


def render_all(ctx: Ctx) -> dict[str, str]:
    env = build_env()
    variables = template_vars(ctx)
    files = {
        "compose.yaml": env.get_template("compose.yaml.j2").render(**variables),
        "mise.toml": env.get_template("mise.toml.j2").render(**variables),
        "README.md": env.get_template("README.md.j2").render(**variables),
        ".env": env.get_template("env.j2").render(blank=False, **variables),
        ".env.example": env.get_template("env.j2").render(blank=True, **variables),
        ".gitignore": env.get_template("gitignore.j2").render(**variables),
        "docs/.gitkeep": "",
        "config/kongctl.yaml": env.get_template("config/kongctl.yaml.j2").render(
            **variables
        ),
    }
    if ctx.vector.enabled and ctx.vector.type.value == "pgvector":
        files["config/pgvector/init.sql"] = "CREATE EXTENSION IF NOT EXISTS vector;\n"
    if ctx.idp.type.value == "keycloak":
        files["config/keycloak/realm-export.json"] = env.get_template(
            "config/realm-export.json.j2"
        ).render(**variables)
    if ctx.idp.type.value == "ldap":
        files["config/lldap/group-configs/groups.json"] = env.get_template(
            "config/lldap-groups.json.j2"
        ).render(**variables)
        files["config/lldap/user-configs/users.json"] = env.get_template(
            "config/lldap-users.json.j2"
        ).render(**variables)
    if ctx.spec.gateway.value in ("ai-gateway-v1", "api-gateway"):
        files["config/kong/kong.yaml"] = env.get_template("config/kong.yaml.j2").render(
            **variables
        )
    return files
