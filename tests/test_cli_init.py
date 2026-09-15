"""`mak4i init` / `mak4i serve` / `mak4i access provision` — the onboarding
convenience commands. These add no business logic; the tests here confirm
they reuse the control plane correctly, save local config safely, fail
clearly before initialization, keep the raw token out of logs, and don't
weaken authorization.
"""

import json
import os
import stat
import sys

import pytest

from mak4i import cli, localconfig
from mak4i.config import build_control_plane_from_env

_ENV_KEYS = (
    "MAK4I_CONTROL_PLANE_DB",
    "MAK4I_STORE",
    "MAK4I_LOCAL_STORE_DIR",
    "MAK4I_TOKEN",
    "MAK4I_TRANSPORT",
    "MAK4I_HOST",
    "MAK4I_PORT",
    "PORT",
    "MAK4I_CONTROL_PLANE_CREATE_TABLES",
    "MAK4I_PUBLIC_ENDPOINT",
)


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    """A fresh `.mak4i/` per test and a clean backend environment. `init`
    mutates `os.environ` directly (via `setdefault`), so those keys are
    popped on teardown as well."""
    monkeypatch.setenv("MAK4I_HOME", str(tmp_path / ".mak4i"))
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # Under pytest stdin is captured (isatty() is False); make it explicit
    # so the non-interactive path is exercised deterministically.
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    yield
    for key in _ENV_KEYS:
        os.environ.pop(key, None)


def _run(*argv: str) -> int:
    return cli.main(list(argv))


def _init(org="My Org", name="Alex Dev", project="Demo Project", extra=()) -> int:
    return _run("init", "--org-name", org, "--display-name", name, "--project-name", project, *extra)


# -- init -----------------------------------------------------------------


def test_init_first_run_creates_full_environment(capsys):
    assert _init() == 0
    out = capsys.readouterr().out
    assert "MAK4I is ready." in out

    config = localconfig.load()
    assert config is not None
    assert config.organization_id.startswith("org_")
    assert config.owner_principal_id.startswith("prn_")
    assert config.project_id.startswith("prj_")
    assert config.credential_id.startswith("cred_")
    assert config.organization_name == "My Org"
    assert config.project_name == "Demo Project"
    assert config.store == "local"

    token = localconfig.load_token()
    assert token is not None and token.startswith("mak4i_")


def test_init_creates_read_write_grant(capsys):
    assert _init() == 0
    config = localconfig.load()
    control_plane = build_control_plane_from_env()
    owner = control_plane.get_principal(config.owner_principal_id)
    assert owner is not None and owner.role == "owner"
    assert sorted(control_plane.effective_permissions(owner, config.project_id)) == ["read", "write"]


def test_init_credential_authenticates_to_the_owner(capsys):
    assert _init() == 0
    config = localconfig.load()
    token = localconfig.load_token()
    control_plane = build_control_plane_from_env()
    principal = control_plane.authenticate(token)
    assert principal.principal_id == config.owner_principal_id


def test_init_never_writes_raw_token_into_config_json(capsys):
    assert _init() == 0
    token = localconfig.load_token()
    config_text = localconfig.config_path().read_text()
    assert token not in config_text
    assert "token" not in json.loads(config_text)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_init_restricts_local_file_permissions(capsys):
    assert _init() == 0
    cred_mode = stat.S_IMODE(localconfig.credentials_path().stat().st_mode)
    assert cred_mode == 0o600
    dir_mode = stat.S_IMODE(localconfig.home_dir().stat().st_mode)
    assert dir_mode == 0o700


def test_init_does_not_log_the_raw_token(capsys, caplog):
    assert _init() == 0
    captured = capsys.readouterr()
    token = localconfig.load_token()
    assert token not in captured.out
    assert token not in captured.err
    assert token not in caplog.text


def test_repeated_init_is_safe_and_non_destructive(capsys):
    assert _init() == 0
    first = localconfig.load()
    capsys.readouterr()

    assert _init(org="Different Org", name="Someone Else", project="Other Project") == 0
    out = capsys.readouterr().out
    assert "already exists" in out

    second = localconfig.load()
    assert second.organization_id == first.organization_id
    assert second.project_id == first.project_id
    assert second.credential_id == first.credential_id


def test_init_force_reprovisions_without_deleting_existing_data(capsys):
    assert _init() == 0
    first = localconfig.load()
    capsys.readouterr()

    assert _init(extra=("--force",)) == 0
    second = localconfig.load()
    assert second.organization_id != first.organization_id
    assert second.project_id != first.project_id

    # The original organization row is still present — --force adds, never deletes.
    control_plane = build_control_plane_from_env()
    assert control_plane._store.get_organization(first.organization_id) is not None


def test_init_non_interactive_missing_value_fails_clearly(capsys):
    exit_code = _run("init")  # no flags, stdin not a tty
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "required" in err and "--flag" in err
    assert not localconfig.exists()


def test_init_interactive_prompts(capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["Prompted Org", "Prompted Dev", "Prompted Project"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert _run("init") == 0
    config = localconfig.load()
    assert config.organization_name == "Prompted Org"
    assert config.project_name == "Prompted Project"


# -- serve --------------------------------------------------------------------


def test_serve_before_init_fails_clearly(capsys, monkeypatch):
    called = []
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: called.append(True))
    exit_code = _run("serve")
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "has not been initialized" in err
    assert "mak4i init" in err
    assert called == []  # never silently initializes


def test_serve_reads_local_config_and_starts_the_existing_server(capsys, monkeypatch):
    assert _init() == 0
    config = localconfig.load()
    token = localconfig.load_token()
    capsys.readouterr()

    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve") == 0
    assert seen_env["MAK4I_CONTROL_PLANE_DB"] == config.control_plane_db
    assert seen_env["MAK4I_STORE"] == "local"
    assert seen_env["MAK4I_TOKEN"] == token
    assert seen_env["MAK4I_TRANSPORT"] == "stdio"

    err = capsys.readouterr().err
    assert "MAK4I local server starting" in err
    assert config.project_name in err
    assert token not in err  # banner must not leak the credential


def test_serve_respects_an_explicit_transport_override(capsys, monkeypatch):
    assert _init() == 0
    capsys.readouterr()
    monkeypatch.setenv("MAK4I_TRANSPORT", "streamable-http")
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))
    assert _run("serve") == 0
    assert seen_env["MAK4I_TRANSPORT"] == "streamable-http"


def test_serve_ignores_a_stale_exported_token(capsys, caplog, monkeypatch):
    """`mak4i init && mak4i serve` must use the credential from that init,
    even if a stale MAK4I_TOKEN is exported in the shell."""
    assert _init() == 0
    real_token = localconfig.load_token()
    capsys.readouterr()

    monkeypatch.setenv("MAK4I_TOKEN", "mak4i_STALE0000000000000000000000000000000000")
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve") == 0
    assert seen_env["MAK4I_TOKEN"] == real_token  # local wins, deterministically

    captured = capsys.readouterr()
    assert "ignoring MAK4I_TOKEN" in captured.err  # heads-up, without the value
    assert real_token not in captured.err
    assert "mak4i_STALE" not in captured.err
    assert real_token not in caplog.text


def test_serve_ignores_a_stale_exported_control_plane_db(capsys, monkeypatch, tmp_path):
    assert _init() == 0
    config = localconfig.load()
    capsys.readouterr()

    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{tmp_path / 'somewhere-else.db'}")
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve") == 0
    assert seen_env["MAK4I_CONTROL_PLANE_DB"] == config.control_plane_db


# -- serve --transport/--host/--port (requirements §6/§8/§9) --------------


def test_serve_transport_flag_selects_http(capsys, monkeypatch):
    assert _init() == 0
    capsys.readouterr()
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve", "--transport", "http") == 0
    assert seen_env["MAK4I_TRANSPORT"] == "streamable-http"

    err = capsys.readouterr().err
    assert "Transport: Streamable HTTP" in err


def test_serve_transport_flag_accepts_the_streamable_http_spelling_too(capsys, monkeypatch):
    """'http' and 'streamable-http' must be equivalent — the latter is
    kept for compatibility with the value already deployed via
    $MAK4I_TRANSPORT (docs/DEPLOYMENT.md)."""
    assert _init() == 0
    capsys.readouterr()
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve", "--transport", "streamable-http") == 0
    assert seen_env["MAK4I_TRANSPORT"] == "streamable-http"


def test_serve_transport_flag_overrides_the_environment_variable(capsys, monkeypatch):
    assert _init() == 0
    capsys.readouterr()
    monkeypatch.setenv("MAK4I_TRANSPORT", "stdio")
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve", "--transport", "http") == 0
    assert seen_env["MAK4I_TRANSPORT"] == "streamable-http"


def test_serve_http_defaults_to_loopback_host_and_shows_the_endpoint(capsys, monkeypatch):
    """Requirements §9: local HTTP testing must bind safely by default."""
    assert _init() == 0
    capsys.readouterr()
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve", "--transport", "http") == 0
    err = capsys.readouterr().err
    assert "Host: 127.0.0.1" in err
    assert "Port: 8080" in err
    assert "MCP endpoint: http://127.0.0.1:8080/mcp" in err


def test_serve_host_flag_overrides_the_loopback_default(capsys, monkeypatch):
    assert _init() == 0
    capsys.readouterr()
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve", "--transport", "http", "--host", "0.0.0.0") == 0
    assert seen_env["MAK4I_HOST"] == "0.0.0.0"
    err = capsys.readouterr().err
    assert "Host: 0.0.0.0" in err


def test_serve_port_flag_overrides_default(capsys, monkeypatch):
    assert _init() == 0
    capsys.readouterr()
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve", "--transport", "http", "--port", "9000") == 0
    assert seen_env["MAK4I_PORT"] == "9000"
    err = capsys.readouterr().err
    assert "Port: 9000" in err
    assert "MCP endpoint: http://127.0.0.1:9000/mcp" in err


def test_serve_port_flag_overrides_mak4i_port_and_port_env_vars(capsys, monkeypatch):
    """Precedence per requirements §8: --port > $MAK4I_PORT > $PORT >
    default."""
    assert _init() == 0
    capsys.readouterr()
    monkeypatch.setenv("PORT", "7000")
    monkeypatch.setenv("MAK4I_PORT", "7100")
    seen_env = {}
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: seen_env.update(os.environ))

    assert _run("serve", "--transport", "http", "--port", "7200") == 0
    assert seen_env["MAK4I_PORT"] == "7200"


def test_serve_mak4i_port_env_var_overrides_the_existing_port_env_var(capsys, monkeypatch):
    """$PORT (the existing Cloud Run/container convention `config.py` and
    the Dockerfile already depend on) must keep working unmodified when
    $MAK4I_PORT isn't set — but $MAK4I_PORT, when present, wins."""
    from mak4i import mcp_server

    monkeypatch.setenv("PORT", "7000")
    monkeypatch.setenv("MAK4I_PORT", "7100")
    assert mcp_server.resolve_port() == 7100

    monkeypatch.delenv("MAK4I_PORT")
    assert mcp_server.resolve_port() == 7000


def test_serve_stdio_banner_is_unchanged_by_the_new_flags(capsys, monkeypatch):
    """Backward compatibility (requirements §7): the stdio banner must not
    grow a Host/Port/endpoint section it never had."""
    assert _init() == 0
    capsys.readouterr()
    monkeypatch.setattr("mak4i.mcp_server.main", lambda: None)

    assert _run("serve") == 0
    err = capsys.readouterr().err
    assert "Transport: stdio" in err
    assert "Host:" not in err
    assert "Port:" not in err
    assert "MCP endpoint:" not in err


# -- access provision ------------------------------------------------------


@pytest.fixture
def hosted_env(tmp_path, monkeypatch):
    """A throwaway control-plane DB + a configured endpoint, standing in for
    an operator's hosted environment."""
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{tmp_path / 'hosted.db'}")
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", "1")
    monkeypatch.setenv("MAK4I_PUBLIC_ENDPOINT", "https://hosted.example/mcp")


def _provision(*extra, display="Angie Carel", org="Example Consulting", project="MAK4I Evaluation"):
    return _run(
        "access", "provision",
        "--display-name", display, "--org-name", org, "--project-name", project, *extra,
    )


def _parse_access(stdout: str) -> dict:
    fields = {}
    for line in stdout.splitlines():
        for key in ("Endpoint", "Project", "Token"):
            if line.startswith(f"{key}:"):
                fields[key.lower()] = line.split(":", 1)[1].strip()
    return fields


def test_access_provision_creates_a_correctly_scoped_member(hosted_env, capsys):
    assert _provision("--endpoint", "https://hosted.example/mcp") == 0
    access = _parse_access(capsys.readouterr().out)
    assert access["endpoint"] == "https://hosted.example/mcp"
    assert access["project"].startswith("prj_")
    assert access["token"].startswith("mak4i_")

    control_plane = build_control_plane_from_env()
    principal = control_plane.authenticate(access["token"])
    assert principal.role == "member"  # never an owner
    authorized = control_plane.list_authorized_projects(principal)
    assert [ap.project.project_id for ap in authorized] == [access["project"]]
    assert sorted(authorized[0].permissions) == ["read", "write"]


def test_access_provision_keeps_collaborators_isolated_across_organizations(hosted_env, capsys):
    assert _provision(display="Collaborator A", org="Org A", project="Proj A") == 0
    a = _parse_access(capsys.readouterr().out)
    assert _provision(display="Collaborator B", org="Org B", project="Proj B") == 0
    b = _parse_access(capsys.readouterr().out)

    control_plane = build_control_plane_from_env()
    principal_a = control_plane.authenticate(a["token"])
    # A has no visibility of, and no permissions on, B's project.
    assert control_plane.effective_permissions(principal_a, b["project"]) == []
    assert [ap.project.project_id for ap in control_plane.list_authorized_projects(principal_a)] == [a["project"]]


def test_access_provision_output_file_is_minimal_and_secret(hosted_env, capsys, tmp_path):
    out_path = tmp_path / "collaborator-access.txt"
    assert _provision("--endpoint", "https://hosted.example/mcp", "--output", str(out_path)) == 0
    access = _parse_access(capsys.readouterr().out)

    text = out_path.read_text()
    assert "https://hosted.example/mcp" in text
    assert access["token"] in text
    assert access["project"] in text
    assert "Documentation:" in text

    lowered = text.lower()
    for forbidden in ("sql", "service account", "serviceaccount", "password", "secret manager", "gcloud", "cloudsql", "psycopg"):
        assert forbidden not in lowered

    if os.name != "nt":
        assert stat.S_IMODE(out_path.stat().st_mode) == 0o600


def test_access_provision_never_logs_the_raw_token(hosted_env, capsys, caplog):
    assert _provision("--endpoint", "https://hosted.example/mcp") == 0
    captured = capsys.readouterr()
    token = _parse_access(captured.out)["token"]
    assert token not in captured.err  # only stdout shows it, once
    assert token not in caplog.text


def test_access_provision_without_endpoint_fails_closed_and_provisions_nothing(hosted_env, capsys, monkeypatch):
    monkeypatch.delenv("MAK4I_PUBLIC_ENDPOINT", raising=False)
    exit_code = _provision()  # no --endpoint, no MAK4I_PUBLIC_ENDPOINT
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "hosted endpoint is not configured" in captured.err
    assert "MAK4I_PUBLIC_ENDPOINT" in captured.err and "--endpoint" in captured.err
    assert "Nothing was provisioned." in captured.err
    assert "Token:" not in captured.out  # no credential printed

    # No organization / principal / project / grant / credential was created.
    control_plane = build_control_plane_from_env()
    assert control_plane._store.list_organizations() == []


def test_access_provision_reads_endpoint_from_the_environment(hosted_env, capsys, monkeypatch):
    monkeypatch.setenv("MAK4I_PUBLIC_ENDPOINT", "https://env.example/mcp")
    assert _provision() == 0
    assert _parse_access(capsys.readouterr().out)["endpoint"] == "https://env.example/mcp"


# -- the granular commands still work --------------------------------------


def test_existing_granular_control_plane_commands_still_work(hosted_env, capsys):
    assert _run("org", "create", "--name", "Acme", "--owner-display-name", "Owner") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["organization"]["organization_id"].startswith("org_")
    assert payload["owner"]["role"] == "owner"
