import dataclasses

import pytest
import yaml

from generator.context import Ctx, build_context
from generator.schema import EnvSpec

AZURE_PROVIDER = {
    "type": "azure",
    "instance": "acme-foundry",
    "auth": "api-key",
    "models": [
        {"name": "gpt-5-6", "deployment_id": "gpt-5.6", "api_version": "2024-12-01-preview"}
    ],
}


def ctx_for(**overrides) -> Ctx:
    data = {"customer": "acme", "gateway": "api-gateway", **overrides}
    return build_context(EnvSpec.model_validate(data))


def only_services(ctx: Ctx, *names: str) -> Ctx:
    """まだ書いていないパーシャルを外して骨格だけ確認するためのヘルパ。"""
    return dataclasses.replace(ctx, services=list(names))


@pytest.fixture
def compose_of():
    def _compose_of(ctx: Ctx) -> dict:
        from generator.render import render_all

        return yaml.safe_load(render_all(ctx)["compose.yaml"])

    return _compose_of
