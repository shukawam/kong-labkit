"""Custom upstream contract; all addresses and diagnostic sentinels are synthetic."""
import subprocess
import sys
import tomllib

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from generator.context import build_context
from generator.gen import main
from generator.render import render_all
from generator.schema import CustomUpstreamSpec, EnvSpec, UpstreamType, load_spec

SENTINEL = "REJECTED_SYNTHETIC_SECRET"
BASE = {"customer": "fixture", "gateway": "api-gateway", "control_plane": "self-managed",
        "observability": {"backend": "none"}}


def custom(upstream=None, **overrides):
    return EnvSpec.model_validate({**BASE, "upstream": upstream if upstream is not None else
                                  {"url": "http://fixture-api:8080/v1"}, **overrides})


@pytest.mark.parametrize("url,expected", [
    ("http://fixture-api:8080/v1/", "http://fixture-api:8080/v1"),
    ("https://api.example.com/", "https://api.example.com"),
    ("http://192.0.2.1:1", "http://192.0.2.1:1"),
    ("https://[2001:db8::1]:65535/v1", "https://[2001:db8::1]:65535/v1"),
    ("http://api.example.com/v-1/a_b/~x.json", "http://api.example.com/v-1/a_b/~x.json"),
])
def test_url_normalization_and_defaults(url, expected):
    spec = custom({"url": url})
    assert isinstance(spec.upstream, CustomUpstreamSpec)
    assert spec.upstream.url == expected
    assert spec.upstream.route_path == spec.upstream.smoke.path == "/upstream"
    assert spec.upstream.strip_path is True
    assert spec.upstream.smoke.expected_status == 200


@pytest.mark.parametrize("url", [
    "", "ftp://fixture-api/v1", "fixture-api/v1", "//fixture-api/v1", "http:///v1",
    "http://fixture-api:0", "http://fixture-api:65536", "http://fixture-api:-1",
    "http://fixture-api:", "http://fixture-api:abc", "http://fixture-api:1.5",
    "http://user:password@fixture-api", "http://@fixture-api", "http://fixture-api?",
    "http://fixture-api?token=x", "http://fixture-api#", "http://fixture-api#x",
    "http://fixture-api/a b", "http://fixture-api/\t", "http://fixture-api/\n",
    "http://fixture-api/é", "http://fixture-api/%2e%2e", "http://fixture-api/a/../b",
    "http://fixture-api/./b", "http://fixture-api/a//b", "http://fixture-api//",
    "http://fixture-api/a\\b", "http://fixture-api/${TOKEN}", "http://$API/v1",
    "http://fixture-api/$(id)", "http://fixture-api/;id", "http://fixture-api/|id",
    "http://localhost", "http://LOCALHOST.", "http://api.localhost", "http://127.0.0.1",
    "http://127.1", "http://0x7f.0x0.0x0.0x1", "http://0x7f.1", "http://2130706433", "http://0x7f000001", "http://0177.0.0.1",
    "http://[::1]", "http://[::ffff:127.0.0.1]", "http://[::2]junk",
    "http://[2001:db8::1", "http://[fe80::1%eth0]", "http://-api.example.com",
    "http://api_.example.com", "http://api..example.com",
])
def test_invalid_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        custom({"url": url})


@pytest.mark.parametrize("mapping", [
    {}, {"url": None}, {"url": 123}, {"url": True}, {"url": []},
    {"url": "http://fixture-api", "unknown": SENTINEL},
    {"url": "http://fixture-api", SENTINEL: "unknown key"},
    {"url": "http://fixture-api", "strip_path": "false"},
    {"url": "http://fixture-api", "strip_path": 1},
    {"url": "http://fixture-api", "route_path": None},
    {"url": "http://fixture-api", "smoke": None},
    {"url": "http://fixture-api", "smoke": {"path": None}},
    *[{"url": "http://fixture-api", "smoke": {"expected_status": value}}
      for value in (True, "200", 200.0, 199, 300, None)],
    {"url": "http://fixture-api", "smoke": {"unknown": SENTINEL}},
])
def test_strict_mapping_and_union_error_redaction(mapping):
    with pytest.raises(ValidationError) as error:
        custom(mapping)
    assert SENTINEL not in str(error.value)
    assert "input_value" not in str(error.value)


@pytest.mark.parametrize("route,smoke", [
    ("api", "/api"), ("/api/", "/api/a"), ("/api", "/apix"),
    ("/api", "/api-v2"), ("/api", "/other/api"), ("/api", "/api/../x"),
    ("/api", "/api//x"), ("/api", "/api/%41"), ("/~regex$", "/api"),
    ("/api", "/api?x"), ("/api", "/api/é"), ("/api", "/api/a b"),
])
def test_invalid_route_and_smoke_paths(route, smoke):
    with pytest.raises(ValidationError):
        custom({"url": "http://fixture-api", "route_path": route, "smoke": {"path": smoke}})


@pytest.mark.parametrize("route,smoke", [("/", "/"), ("/", "/products"),
                                         ("/api", "/api"), ("/api", "/api/products/")])
@pytest.mark.parametrize("strip", [True, False])
def test_route_and_smoke_serialization(route, smoke, strip):
    ctx = build_context(custom({"url": "http://fixture-api/v1/", "route_path": route,
                                "strip_path": strip, "smoke": {"path": smoke}}))
    files = render_all(ctx)
    service, = yaml.safe_load(files["config/kong/kong.yaml"])["services"]
    assert service["name"] == "upstream"
    assert service["url"] == "http://fixture-api/v1"
    generated, = service["routes"]
    assert generated == {"name": "upstream", "paths": [route], "strip_path": strip,
                         "path_handling": "v0", "protocols": ["http", "https"]}
    assert f"http://localhost:{ctx.ports['proxy']}{smoke}" in files["mise.toml"]


@pytest.mark.parametrize("overrides", [
    {"control_plane": "konnect"}, {"gateway": "ai-gateway-v1"},
    {"gateway": "ai-gateway-v2", "control_plane": "konnect"},
    *[{"target": target} for target in ("kubernetes", "cloud-run", "aca")],
    *[{"idp": {"type": idp, "realm": "fixture"}} for idp in ("keycloak", "ldap", "entra-id")],
])
def test_unsupported_scope_rejected(overrides):
    with pytest.raises(ValidationError, match="custom upstream"):
        custom(**overrides)


@pytest.mark.parametrize("gateway,cp", [("api-gateway", "konnect"), ("api-gateway", "self-managed"),
    ("ai-gateway-v1", "konnect"), ("ai-gateway-v1", "self-managed"), ("ai-gateway-v2", "konnect")])
@pytest.mark.parametrize("value", ["omitted", None, "httpbin", "none"])
def test_legacy_default_and_scalar_acceptance(gateway, cp, value):
    data = {**BASE, "gateway": gateway, "control_plane": cp}
    if value != "omitted":
        data["upstream"] = value
    spec = EnvSpec.model_validate(data)
    expected = value if value not in (None, "omitted") else ("httpbin" if gateway == "api-gateway" else "none")
    assert spec.upstream is UpstreamType(expected)


def test_external_api_is_not_a_managed_service_or_port():
    ctx = build_context(custom())
    files = render_all(ctx)
    none = render_all(build_context(EnvSpec.model_validate({**BASE, "upstream": "none"})))
    assert ctx.upstream.enabled and ctx.upstream.custom
    assert ctx.upstream.managed_service_name is None
    assert "httpbin" not in ctx.ports and "httpbin" not in ctx.services
    assert "upstream" not in yaml.safe_load(files["compose.yaml"])["services"]
    assert files["compose.yaml"] == none["compose.yaml"]
    assert files[".env.example"] == none[".env.example"]
    assert set(files) == set(none)
    assert "httpbin（直接）" not in files["README.md"]
    for task in ("setup", "sync", "diff"):
        assert tomllib.loads(files["mise.toml"])["tasks"][task] == tomllib.loads(none["mise.toml"])["tasks"][task]


@pytest.mark.parametrize("body", [
    f"upstream: [ {SENTINEL}\n",
    f"upstream: !!invalid {SENTINEL}\n",
    yaml.safe_dump({**BASE, "upstream": {"url": f"http://user:{SENTINEL}@fixture-api"}}),
    yaml.safe_dump({**BASE, "upstream": {"url": "http://fixture-api", SENTINEL: "extra"}}),
    yaml.safe_dump({**BASE, "control_plane": "konnect", "upstream": {"url": f"http://fixture-api/{SENTINEL}"}}),
    yaml.safe_dump({**BASE, "upstream": [SENTINEL]}),
])
def test_cli_refusal_is_redacted_and_never_reaches_context_writer_or_certs(tmp_path, monkeypatch, body):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid input reached a generation side effect")
    for name in ("build_context", "render_all", "write_files", "generate_cluster_cert"):
        monkeypatch.setattr(f"generator.gen.{name}", forbidden)
    source = tmp_path / "input.yaml"
    source.write_text(body)
    out = tmp_path / "out"
    result = CliRunner().invoke(main, [str(source), "-o", str(out)])
    assert result.exit_code == 1
    assert SENTINEL not in result.output
    assert "Traceback" not in result.output
    assert not out.exists()
    with pytest.raises((ValueError, ValidationError)) as error:
        load_spec(source)
    assert SENTINEL not in str(error.value)


@pytest.mark.parametrize("expected", [200, 201, 204])
@pytest.mark.parametrize("status,exit_code", [("200", 0), ("201", 0), ("204", 0), ("299", 0),
    ("301", 0), ("401", 0), ("404", 0), ("500", 0), ("502", 0), ("000", 0),
    ("000", 7), ("000", 28), ("000", 6), (SENTINEL, 0)])
def test_generated_smoke_exact_status_and_safe_failure(tmp_path, expected, status, exit_code):
    files = render_all(build_context(custom({"url": "http://fixture-api/v1", "route_path": "/api",
        "smoke": {"path": "/api/health", "expected_status": expected}})))
    task = tomllib.loads(files["mise.toml"])["tasks"]["smoke"]["run"]
    stub = tmp_path / "curl"
    stub.write_text(f"#!{sys.executable}\n" + f"""
import sys
args = sys.argv[1:]
assert args[0] == '--disable'
assert args[args.index('--request') + 1] == 'GET'
assert '--connect-timeout' in args and '--max-time' in args
assert '--max-redirs' in args and args[args.index('--max-redirs') + 1] == '0'
assert args[args.index('--output') + 1] == '/dev/null'
assert not any(a in args for a in ('-L', '--location', '--show-error', '-v', '--verbose'))
assert args[-1] == 'http://localhost:8000/api/health'
print({SENTINEL!r}, file=sys.stderr)
print({status!r}, end='')
sys.exit({exit_code})
""")
    stub.chmod(0o755)
    result = subprocess.run(["/bin/bash", "-c", task], cwd=tmp_path,
                            env={"PATH": str(tmp_path)}, capture_output=True, text=True)
    success = status == str(expected) and exit_code == 0
    assert (result.returncode == 0) == success
    assert ("smoke: OK" in result.stdout) == success
    assert SENTINEL not in result.stdout + result.stderr
    assert "http://" not in result.stdout + result.stderr


def test_default_smoke_probes_route_not_httpbin_status():
    task = tomllib.loads(render_all(build_context(custom()))["mise.toml"])["tasks"]["smoke"]["run"]
    assert "'http://localhost:8000/upstream'" in task
    assert "/status/200" not in task


def test_smoke_schema_does_not_advertise_null():
    path = EnvSpec.model_json_schema()["$defs"]["UpstreamSmokeSpec"]["properties"]["path"]
    assert path["type"] == "string"
    assert "anyOf" not in path and "default" not in path


def test_cli_rejects_explicit_null_smoke_path(tmp_path):
    source = tmp_path / "input.yaml"
    source.write_text(yaml.safe_dump({**BASE, "upstream": {
        "url": "http://fixture-api", "smoke": {"path": None}}}))
    out = tmp_path / "out"
    result = CliRunner().invoke(main, [str(source), "-o", str(out)])
    assert result.exit_code == 1 and not out.exists()


def test_custom_cli_copies_input_and_regenerates_via_existing_writer(tmp_path, monkeypatch):
    def fake_cert(directory, *, basename, **kwargs):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{basename}.crt").write_text("synthetic certificate stand-in")
    monkeypatch.setattr("generator.gen.generate_cluster_cert", fake_cert)
    source = tmp_path / "input.yaml"
    body = "# synthetic custom input\n" + yaml.safe_dump({**BASE, "upstream": {
        "url": "http://fixture-api:8080/v1/", "route_path": "/api"}})
    source.write_text(body)
    out = tmp_path / "out"
    runner = CliRunner()
    assert runner.invoke(main, [str(source), "-o", str(out)]).exit_code == 0
    assert (out / "env.yaml").read_text() == body
    service, = yaml.safe_load((out / "config/kong/kong.yaml").read_text())["services"]
    assert service["url"] == "http://fixture-api:8080/v1"
    (out / ".env").write_text("SYNTHETIC_OWNER_VALUE=keep\n")
    (out / ".certs").mkdir()
    (out / ".certs/owner.txt").write_text("keep certificate material")
    (out / "docs/owner.md").write_text("keep owner notes")
    source.write_text(yaml.safe_dump({**BASE, "upstream": "none"}))
    assert runner.invoke(main, [str(source), "-o", str(out), "--force"]).exit_code == 0
    assert yaml.safe_load((out / "config/kong/kong.yaml").read_text())["services"] == []
    assert (out / ".env").read_text() == "SYNTHETIC_OWNER_VALUE=keep\n"
    assert (out / ".certs/owner.txt").read_text() == "keep certificate material"
    assert (out / "docs/owner.md").read_text() == "keep owner notes"
