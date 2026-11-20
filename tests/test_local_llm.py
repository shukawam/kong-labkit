import shlex
import tomllib

import pytest
import yaml
from pydantic import ValidationError

from generator.context import PortConflictError, build_context
from generator.render import render_all
from generator.schema import EnvSpec
from tests.conftest import AZURE_PROVIDER, ctx_for

CHAT = {
    "name": "gemma4-31b",
    "upstream_url": "http://host.docker.internal:8001/v1/chat/completions",
    "serve": {
        "runtime": "metal",
        "model": "mlx-community/gemma-4-e2b-it-4bit",
        "args": ["--max-model-len", "8192"],
    },
}

EMBED = {
    "name": "harrier-oss-v1-0-6b",
    "model_id": "microsoft/harrier-oss-v1-0.6b",
    "capabilities": ["embeddings"],
    "upstream_url": "http://host.docker.internal:8002/v1/embeddings",
    "serve": {
        "runtime": "metal",
        "args": ["--runner", "pooling", "--hf-overrides", '{"architectures":["Qwen3ForCausalLM"]}'],
    },
}

RERANK = {
    "name": "ruri-v3-reranker-310m",
    "model_id": "cl-nagoya/ruri-v3-reranker-310m",
    "capabilities": ["rerank"],
    "formats": ["passthrough"],
    "upstream_url": "http://host.docker.internal:8003/v1/rerank",
    "serve": {"runtime": "cpu", "args": ["--runner", "pooling"]},
}


def providers(*vllm_models, embed=True):
    out = [{"type": "vllm", "models": list(vllm_models)}]
    if embed:
        out.append({"type": "openai", "auth": "none", "models": [EMBED]})
    return out


def v2(*vllm_models, embed=True, **ai):
    return ctx_for(gateway="ai-gateway-v2", ai={"providers": providers(*vllm_models, embed=embed), **ai})


def spec(*vllm_models, embed=True):
    return EnvSpec.model_validate(
        {"customer": "acme", "gateway": "ai-gateway-v2", "ai": {"providers": providers(*vllm_models, embed=embed)}}
    )


def tasks_of(ctx) -> dict:
    return tomllib.loads(render_all(ctx)["mise.toml"])["tasks"]


def compose_services(ctx) -> dict:
    return yaml.safe_load(render_all(ctx)["compose.yaml"])["services"]


# --- schema ---


def test_serve_needs_a_host_docker_internal_upstream():
    bad = {**CHAT, "upstream_url": "http://vllm.example.com:8001/v1/chat/completions"}
    with pytest.raises(ValidationError, match="host.docker.internal"):
        spec(bad, embed=False)


def test_serve_needs_an_explicit_port():
    bad = {**CHAT, "upstream_url": "http://host.docker.internal/v1/chat/completions"}
    with pytest.raises(ValidationError, match="ポート"):
        spec(bad, embed=False)


def test_serve_needs_an_upstream_url():
    bad = {"name": "m", "capabilities": ["embeddings"], "serve": {"runtime": "metal"}}
    with pytest.raises(ValidationError, match="upstream_url"):
        EnvSpec.model_validate({
            "customer": "acme", "gateway": "ai-gateway-v2",
            "ai": {"providers": [{"type": "openai", "auth": "none", "models": [bad]}]},
        })


def test_served_ports_must_be_unique():
    clash = {**RERANK, "upstream_url": "http://host.docker.internal:8001/v1/rerank"}
    with pytest.raises(ValidationError, match="8001"):
        spec(CHAT, clash, embed=False)


@pytest.mark.parametrize("arg", ["--port", "--host", "--served-model-name", "--port=9000"])
def test_generator_owned_args_are_rejected(arg):
    bad = {**CHAT, "serve": {**CHAT["serve"], "args": [arg, "x"]}}
    with pytest.raises(ValidationError, match="serve.args"):
        spec(bad, embed=False)


def test_served_port_must_not_hit_a_kong_port():
    bad = {**CHAT, "upstream_url": "http://host.docker.internal:8000/v1/chat/completions"}
    with pytest.raises(PortConflictError, match="8000"):
        build_context(spec(bad, embed=False))


# --- host (metal) ---


def test_metal_models_are_started_by_vllm_up_with_pid_and_log_files():
    run = tasks_of(v2(CHAT, RERANK))["vllm:up"]["run"]
    assert "command -v vllm" in run
    assert ".vllm/gemma4-31b.pid" in run
    assert ".vllm/gemma4-31b.log" in run
    assert ".vllm/harrier-oss-v1-0-6b.pid" in run


def test_metal_command_binds_loopback_and_serves_the_target_name():
    ctx = v2(CHAT, RERANK)
    [chat] = [m for m in ctx.local_llm.metal if m.name == "gemma4-31b"]
    argv = shlex.split(chat.command)
    assert argv[:3] == ["vllm", "serve", "mlx-community/gemma-4-e2b-it-4bit"]
    # LAN に認証なしの LLM を晒さない。Docker Desktop は loopback 待ち受けにも host.docker.internal で届く
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "8001"
    assert argv[argv.index("--served-model-name") + 1] == "gemma4-31b"
    assert argv[-2:] == ["--max-model-len", "8192"]


def test_serve_model_defaults_to_the_target_name_and_quotes_json_args():
    ctx = v2(CHAT, RERANK)
    [embed] = [m for m in ctx.local_llm.metal if m.name == "harrier-oss-v1-0-6b"]
    argv = shlex.split(embed.command)
    assert argv[2] == "microsoft/harrier-oss-v1-0.6b"
    assert argv[argv.index("--served-model-name") + 1] == "microsoft/harrier-oss-v1-0.6b"
    assert argv[argv.index("--hf-overrides") + 1] == '{"architectures":["Qwen3ForCausalLM"]}'
    assert embed.command in tasks_of(ctx)["vllm:up"]["run"]


def test_vllm_down_stops_host_processes_and_containers():
    run = tasks_of(v2(CHAT, RERANK))["vllm:down"]["run"]
    assert ".vllm/gemma4-31b.pid" in run
    assert "kill" in run
    assert "docker compose stop vllm-ruri-v3-reranker-310m" in run


def test_vllm_status_and_logs_cover_every_served_model():
    tasks = tasks_of(v2(CHAT, RERANK))
    for name in ("gemma4-31b", "harrier-oss-v1-0-6b", "ruri-v3-reranker-310m"):
        assert name in tasks["vllm:status"]["run"]
        assert name in tasks["vllm:logs"]["run"]


def test_state_dir_is_ignored_by_git():
    assert ".vllm/" in render_all(v2(CHAT, RERANK))[".gitignore"].split()


def test_up_and_down_drive_the_local_models_too():
    tasks = tasks_of(v2(CHAT, RERANK))
    assert "mise run vllm:up" in tasks["up"]["run"]
    assert "mise run vllm:down" in tasks["down"]["run"]
    setup = tasks["setup"]["run"]
    assert setup.index("docker compose up -d") < setup.index("mise run vllm:up")


# --- container (cpu) ---


def test_cpu_model_runs_in_compose_on_a_loopback_port():
    service = compose_services(v2(CHAT, RERANK))["vllm-ruri-v3-reranker-310m"]
    assert service["image"].startswith("vllm/vllm-openai-cpu:")
    assert service["image"].endswith("-arm64")
    assert service["ports"] == ["127.0.0.1:8003:8000"]
    # 既定の 64MB では EngineCore が共有メモリを確保できずに起動しない
    assert service["shm_size"] == "1g"
    assert service["command"][:3] == ["cl-nagoya/ruri-v3-reranker-310m", "--served-model-name", "cl-nagoya/ruri-v3-reranker-310m"]
    assert service["command"][3:] == ["--runner", "pooling"]
    assert any(v.endswith(":/root/.cache/huggingface") for v in service["volumes"])
    assert "healthcheck" in service


def test_data_plane_does_not_wait_for_the_model_containers():
    kong = compose_services(v2(CHAT, RERANK))["kong"]
    assert "vllm-ruri-v3-reranker-310m" not in (kong.get("depends_on") or {})


def test_readme_explains_local_models():
    readme = render_all(v2(CHAT, RERANK))["README.md"]
    assert "## ローカル LLM（vLLM）" in readme
    assert "brew tap vllm-project/vllm-metal" in readme
    assert "mise run vllm:up" in readme
    assert "| ruri-v3-reranker-310m | vLLM CPU コンテナ |" in readme


# --- off ---


def test_nothing_local_without_serve():
    ctx = ctx_for(gateway="ai-gateway-v2", ai={"providers": [AZURE_PROVIDER]})
    tasks = tasks_of(ctx)
    assert not any(name.startswith("vllm:") for name in tasks)
    assert ".vllm/" not in render_all(ctx)[".gitignore"]
    assert "vllm" not in tasks["up"]["run"]
