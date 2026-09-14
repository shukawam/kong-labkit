# Repository Guidelines

## Project Structure & Module Organization

`kong-labkit` generates reproducible Kong environments from YAML using Python 3.12+, Pydantic, Jinja2, and Click. Docker Compose is the currently supported target.

- `generator/`: `schema.py` validates inputs, `context.py` derives configuration, `render.py` renders templates, `gen.py` handles the CLI and writes files, and `certs.py` creates certificates.
- `generator/templates/`: Jinja `.j2` assets, with service fragments in `services/` and gateway/identity configuration in `config/`.
- `examples/`: representative YAML inputs; `tests/`: pytest suites and generated snapshots under `tests/golden/<example-stem>/`.
- `scripts/update_golden.py`: snapshot regeneration; `docs/superpowers/`: design notes, plans, and known gaps.
- `customer/`: ignored, customer-specific input files.

## Build, Test, and Development Commands

Run these from the repository root:

- `uv sync --dev`: install runtime and development dependencies.
- `mise run generate acme`: read `customer/acme.yaml` and generate `~/customer/acme`; first copy an example and set its `customer` field to `acme`.
- `uv run generator/gen.py examples/aigw-v2-konnect-redis-stack.yaml -o /tmp/kong-labkit-demo`: generate directly from an example.
- `uv run pytest`: run the default suite, excluding `slow` tests.
- `uv run pytest -m slow`: run container smoke tests; requires Docker Compose, decK, curl, OpenSSL, and `KONG_LICENSE_DATA`.
- `uv run python scripts/update_golden.py`: regenerate snapshots after intentional output changes; review the diff.

There is no separate build step. Follow the generated README for configuration and startup with `mise run up` inside the output directory.

## Coding Style & Naming Conventions

Use four-space Python indentation, type annotations, `snake_case` functions/modules, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Use two-space YAML indentation and retain `.j2` template suffixes. Keep validation in `schema.py` and derived configuration in `context.py`; preserve Jinja `StrictUndefined`. No formatter or linter is configured; match surrounding code.

## Testing Guidelines

Name tests `tests/test_<feature>.py` with `test_<behavior>` functions. Add regression tests for behavior changes and extend configuration-matrix coverage when adding combinations. Mark Docker-dependent tests `docker` and container-starting tests `slow`. Commit reviewed golden updates alongside template changes. No numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

Follow the history's Conventional Commit style: `feat:`, `fix:`, `docs:`, or `chore:`, with optional scopes such as `fix(templates): ...`. Japanese summaries are common. In PRs, explain the behavior change, affected configurations, test results, and relevant issues; include representative generated diffs for template changes.

## Security & Configuration

Keep customer inputs, credentials, licenses, and private keys out of commits. Preserve the generator's protection of `.env`, `.certs/`, and `docs/` during `--force` regeneration and its rejection of dirty Git output directories.
