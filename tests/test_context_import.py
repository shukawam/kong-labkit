"""Import compatibility, including Python 3.12's eager annotation evaluation."""
from pathlib import Path
import subprocess
import sys


def test_fresh_context_and_cli_import_resolves_secrets_annotation():
    result = subprocess.run(
        [sys.executable, "-B", "-c", """
from typing import get_type_hints
import generator.context as context
import generator.gen

assert get_type_hints(context.Ctx)["secrets"] is context.SecretsCtx
"""],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
