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
from pathlib import Path

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


def test_init_output_presents_both_local_transport_choices(capsys):
    """`init`'s Next section offers stdio and Streamable HTTP as two ways
    to serve the *same* local environment — stdio first, as the default."""
    assert _init() == 0
    out = capsys.readouterr().out
    next_section = out[out.index("Next:"):]

    # Both transports use this one initialized environment.
    assert "same" in next_section and "local environment" in next_section

    # Option 1 — stdio, with the Claude Code stdio example.
    assert "Option 1 — stdio (default)" in next_section
    assert "\n  mak4i serve\n" in next_section
    assert "launches MAK4I as its own process" in next_section
    assert "claude mcp add mak4i -- mak4i serve" in next_section

    # Option 2 — Streamable HTTP, with its three local endpoints.
    assert "Option 2 — Streamable HTTP" in next_section
    assert "  mak4i serve --transport http" in next_section
    assert "multiple local MCP clients" in next_section
    assert "MCP:    http://127.0.0.1:8080/mcp" in next_section
    assert "Health: http://127.0.0.1:8080/health" in next_section
    assert "Ready:  http://127.0.0.1:8080/ready" in next_section
    assert str(localconfig.credentials_path()) in next_section

    assert next_section.index("Option 1") < next_section.index("Option 2")
    # The HTTP option points at the credentials file; the token itself is
    # never printed.
    assert localconfig.load_token() not in out


def test_init_http_urls_match_serve_defaults_and_stdio_stays_default():
    """The URLs `init` prints are exactly where `mak4i serve --transport
    http` listens by default, and plain `mak4i serve` is still stdio."""
    from mak4i import mcp_server

    assert cli._LOCAL_HTTP_BASE == f"http://{mcp_server.resolve_host()}:{mcp_server.resolve_port()}"
    assert mcp_server.resolve_transport() == "stdio"


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


# -- ambient local-environment resolution for the granular/operator/
# -- artifact commands (org/project/principal/grant/credential, the
# -- artifact commands, and doctor) -----------------------------------
#
# Regression coverage for the bug where `mak4i serve` correctly resolved
# an initialized `.mak4i/` environment but every other command went
# straight to raw `MAK4I_CONTROL_PLANE_DB` (or the hardcoded SQLite
# default) instead, silently using a different — or nonexistent —
# database. `world`'s fixture (`tests/test_cli.py`) deliberately bypasses
# `mak4i init` entirely via `monkeypatch.setenv`, which is exactly why
# this bug shipped without a failing test.
#
# The fix's precedence for these commands is deliberately the *opposite*
# of `serve`'s: an explicit `MAK4I_CONTROL_PLANE_DB`/`MAK4I_STORE`/
# `MAK4I_LOCAL_STORE_DIR` always wins over an ambient `.mak4i/` — only
# when *none* of those are already set does the ambient local
# environment apply. `_clear_deployment_env` simulates a fresh shell
# that never exported anything: `mak4i init` itself sets those variables
# as a side effect of provisioning, which would otherwise make every
# subsequent call in the same test process look like the "explicit"
# case by accident.


def _clear_deployment_env(monkeypatch):
    for key in localconfig._EXPLICIT_DEPLOYMENT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_org_list_uses_the_initialized_local_environment(capsys, monkeypatch):
    assert _init() == 0
    config = localconfig.load()
    _clear_deployment_env(monkeypatch)
    capsys.readouterr()

    assert _run("org", "list") == 0
    orgs = json.loads(capsys.readouterr().out)
    assert [o["organization_id"] for o in orgs] == [config.organization_id]


def test_project_list_uses_the_initialized_local_environment(capsys, monkeypatch):
    assert _init() == 0
    config = localconfig.load()
    _clear_deployment_env(monkeypatch)
    capsys.readouterr()

    assert (
        _run(
            "project", "list",
            "--actor", config.owner_principal_id,
            "--organization-id", config.organization_id,
        )
        == 0
    )
    projects = json.loads(capsys.readouterr().out)
    assert [p["project_id"] for p in projects] == [config.project_id]


def test_principal_list_uses_the_initialized_local_environment(capsys, monkeypatch):
    assert _init() == 0
    config = localconfig.load()
    _clear_deployment_env(monkeypatch)
    capsys.readouterr()

    assert (
        _run(
            "principal", "list",
            "--actor", config.owner_principal_id,
            "--organization-id", config.organization_id,
        )
        == 0
    )
    principals = json.loads(capsys.readouterr().out)
    assert [p["principal_id"] for p in principals] == [config.owner_principal_id]


def test_credential_list_uses_the_initialized_local_environment(capsys, monkeypatch):
    assert _init() == 0
    config = localconfig.load()
    _clear_deployment_env(monkeypatch)
    capsys.readouterr()

    assert (
        _run(
            "credential", "list",
            "--actor", config.owner_principal_id,
            "--principal-id", config.owner_principal_id,
        )
        == 0
    )
    credentials = json.loads(capsys.readouterr().out)
    assert [c["credential_id"] for c in credentials] == [config.credential_id]


def test_artifact_create_uses_the_initialized_local_environment(capsys, monkeypatch):
    """At least one artifact operation (task 7's explicit minimum) —
    `create` here, using the initialized owner as the operator-mode
    `--principal`."""
    assert _init() == 0
    config = localconfig.load()
    _clear_deployment_env(monkeypatch)
    capsys.readouterr()

    assert (
        _run(
            "create",
            "--principal", config.owner_principal_id,
            "--project", config.project_id,
            "--artifact-id", "regression-001",
            "--artifact-type", "architecture_decision",
            "--title", "Regression coverage",
            "--content", "Written against the initialized local environment.",
        )
        == 0
    )
    artifact = json.loads(capsys.readouterr().out)
    assert artifact["artifact_id"] == "regression-001"
    assert artifact["project"] == config.project_id


def test_org_list_honors_an_explicit_control_plane_db_over_ambient_local_environment(
    capsys, monkeypatch, tmp_path
):
    """The precedence this revision asked for, and the exact regression
    the earlier incident (a real ambient `.mak4i/` getting silently
    written to by commands that had explicitly configured a different
    target) needs covered: an explicit `MAK4I_CONTROL_PLANE_DB` must win
    over an ambient initialized `.mak4i/` — the deliberate inverse of
    `serve`'s own precedence (see `test_serve_ignores_a_stale_exported_
    control_plane_db` above, which is unchanged and still correct for
    `serve` specifically)."""
    # An initialized ambient local environment exists...
    assert _init() == 0
    ambient_db_path = Path(localconfig.load().control_plane_db.removeprefix("sqlite:///"))
    _clear_deployment_env(monkeypatch)  # simulate a fresh shell: nothing exported yet
    ambient_size_before = ambient_db_path.stat().st_size
    ambient_mtime_before = ambient_db_path.stat().st_mtime_ns
    capsys.readouterr()

    # ...but an explicit, separate control-plane database is configured.
    explicit_db_path = tmp_path / "explicit-target.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{explicit_db_path}")
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", "1")

    assert _run("org", "list") == 0
    orgs = json.loads(capsys.readouterr().out)
    assert orgs == []  # the explicit, freshly created, empty database — not the ambient one

    # The explicit target was actually used (its file now exists)...
    assert explicit_db_path.exists()
    # ...and the ambient database was never touched — not even opened for
    # a write that happened to change nothing: size and mtime are both
    # byte-for-byte unchanged.
    assert ambient_db_path.stat().st_size == ambient_size_before
    assert ambient_db_path.stat().st_mtime_ns == ambient_mtime_before


def test_init_ignores_a_stale_exported_control_plane_db_on_first_run(capsys, monkeypatch, tmp_path):
    """The related issue folded into this fix: a genuinely first-run
    `mak4i init` must provision into its own local default, never
    whatever a stray `MAK4I_CONTROL_PLANE_DB` happens to already point
    at — that could otherwise silently create a new local organization
    inside a real (e.g. Enterprise Self-Hosted) database."""
    stray_db = tmp_path / "stray-enterprise-looking.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{stray_db}")

    assert _init() == 0
    err = capsys.readouterr().err
    assert "ignoring MAK4I_CONTROL_PLANE_DB" in err

    config = localconfig.load()
    assert config.control_plane_db == localconfig.default_control_plane_db()
    assert str(stray_db) not in config.control_plane_db
    # The stray database was never created, let alone written to.
    assert not stray_db.exists()

    # And a subsequent `org list` reads back what `init` actually wrote.
    orgs = json.loads(_run_and_capture_stdout(capsys, "org", "list"))
    assert [o["organization_id"] for o in orgs] == [config.organization_id]


def test_clean_error_for_an_unreachable_control_plane_database(capsys, monkeypatch, tmp_path):
    """Task 8: a database/config failure must be one clear line on
    stderr, exit code 1 — never a raw SQLAlchemy traceback. No local
    `.mak4i/` environment here, so this exercises the plain
    MAK4I_CONTROL_PLANE_DB path directly."""
    monkeypatch.setenv(
        "MAK4I_CONTROL_PLANE_DB",
        "postgresql+psycopg://realuser:realpassword@127.0.0.1:1/nonexistent",
    )

    exit_code = _run("org", "list")
    assert exit_code == 1

    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "OperationalError" in err  # the useful part is kept
    # No secret leakage (task 7's explicit requirement): the real
    # credentials embedded in the URL must never appear in the message.
    assert "realuser" not in err
    assert "realpassword" not in err
    assert "***:***" in err  # redacted, not just silently dropped


def _run_and_capture_stdout(capsys, *argv: str) -> str:
    capsys.readouterr()
    assert _run(*argv) == 0
    return capsys.readouterr().out


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


def test_access_file_documentation_link_targets_an_existing_heading():
    """The docs URL written into a collaborator's access file must point at
    a heading that actually exists in this repository (a previous anchor,
    `#hosted--server-deployments`, matched nothing)."""
    import re
    from pathlib import Path

    from mak4i import cli

    match = re.fullmatch(
        r"https://github\.com/talvikai/mak4i-reference/blob/main/(?P<path>[^#]+)#(?P<anchor>.+)",
        cli._ACCESS_DOCS_URL,
    )
    assert match, cli._ACCESS_DOCS_URL
    doc = Path(__file__).resolve().parent.parent / match["path"]
    headings = [
        re.sub(r"[^\w\- ]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
        for line in doc.read_text().splitlines()
        if line.startswith("#")
    ]
    assert match["anchor"] in headings


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


# -- actor auto-derivation: credential -> authentication -> principal -> actor --
#
# The reported bug: even after a successful `mak4i init`, `mak4i principal
# list` (and project/grant/credential) hard-failed at the argparse layer
# demanding `--actor`, before any code that could read the already-saved
# `.mak4i/config.json` ever ran — a circular discovery problem ("I need my
# principal id to list principals, so I can discover my principal id").
#
# `--actor`, when given, is unchanged (a bare id lookup, kept for backward
# compatibility). When omitted, the actor is now derived by *authenticating*
# a bearer credential (MAK4I_TOKEN if already set, otherwise the local
# `mak4i init` credential) via the exact same `ControlPlane.authenticate()`
# the MCP hosted path already uses — never by trusting a cached id. A
# missing, invalid, expired, or revoked credential is a clear CLI error,
# never a silent fallback to something weaker.


def _parse_issued_credential(stdout: str) -> tuple[str, str]:
    """`(credential_id, raw_token)` from `credential issue`'s plain-text
    output (not JSON — see `_cmd_credential_issue`)."""
    credential_id = None
    token = None
    for line in stdout.splitlines():
        if line.startswith("credential_id: "):
            credential_id = line.removeprefix("credential_id: ").strip()
        elif line.startswith("Authorization: Bearer "):
            token = line.removeprefix("Authorization: Bearer ").strip()
    assert credential_id and token, f"could not parse credential issue output: {stdout!r}"
    return credential_id, token


# -- Local: derivation from the `mak4i init` credential --------------------


def test_principal_list_without_actor_derives_from_the_local_credential(capsys):
    """The exact reported bug — now fixed."""
    assert _init() == 0
    config = localconfig.load()
    capsys.readouterr()

    assert _run("principal", "list") == 0
    principals = json.loads(capsys.readouterr().out)
    assert [p["principal_id"] for p in principals] == [config.owner_principal_id]


def test_project_list_without_actor_derives_from_the_local_credential(capsys):
    assert _init() == 0
    config = localconfig.load()
    capsys.readouterr()

    assert _run("project", "list") == 0
    projects = json.loads(capsys.readouterr().out)
    assert [p["project_id"] for p in projects] == [config.project_id]


def test_principal_show_derives_actor_but_still_requires_an_explicit_principal_id(capsys):
    """Decision 3: SHOW keeps its explicit target id — only the *actor*
    performing the lookup is derived."""
    assert _init() == 0
    config = localconfig.load()
    capsys.readouterr()

    assert _run("principal", "show", "--principal-id", config.owner_principal_id) == 0
    principal = json.loads(capsys.readouterr().out)
    assert principal["principal_id"] == config.owner_principal_id

    # Omitting --principal-id entirely is still a hard argparse error —
    # SHOW never guesses a target.
    with pytest.raises(SystemExit) as exc_info:
        _run("principal", "show")
    assert exc_info.value.code == 2


# -- Enterprise-style: derivation from an explicit MAK4I_TOKEN -------------


def test_granular_command_derives_actor_from_explicit_mak4i_token(hosted_env, capsys, monkeypatch):
    """No `.mak4i/` involved at all — an operator who exported their own
    MAK4I_TOKEN, exactly as they would for any other MCP client."""
    assert _run("org", "create", "--name", "Acme", "--owner-display-name", "Owner") == 0
    payload = json.loads(capsys.readouterr().out)
    organization_id = payload["organization"]["organization_id"]
    owner_id = payload["owner"]["principal_id"]

    assert _run(
        "credential", "issue", "--actor", owner_id, "--principal-id", owner_id,
        "--no-connection-help",
    ) == 0
    _credential_id, token = _parse_issued_credential(capsys.readouterr().out)

    monkeypatch.setenv("MAK4I_TOKEN", token)
    assert _run("principal", "list") == 0
    principals = json.loads(capsys.readouterr().out)
    assert [p["principal_id"] for p in principals] == [owner_id]
    assert [p["organization_id"] for p in principals] == [organization_id]


# -- credential failure modes: clear errors, never a silent fallback ------


def test_actor_derivation_fails_clearly_with_no_credential_available(hosted_env, capsys):
    """No `.mak4i/`, no MAK4I_TOKEN, no --actor: a clear error, not a bare
    argparse "required" message and not a traceback."""
    exit_code = _run("principal", "list", "--organization-id", "org_does-not-matter")
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "no --actor given and no credential available" in err
    assert "MAK4I_TOKEN" in err and "mak4i init" in err


def test_actor_derivation_fails_clearly_with_an_invalid_credential(hosted_env, capsys, monkeypatch):
    monkeypatch.setenv("MAK4I_TOKEN", "mak4i_totally-not-a-real-token")
    exit_code = _run("principal", "list", "--organization-id", "org_does-not-matter")
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "invalid or expired credential" in err
    assert "--actor" in err
    # `mak4i init --force` does not repair or rotate a credential — it
    # provisions a brand-new, disconnected organization/project
    # alongside the existing one (verified directly against a real
    # environment: every row count doubles). Recommending it here would
    # be actively harmful advice, so it must never appear in this error.
    assert "init --force" not in err
    # A bare `mak4i credential issue` (no --actor) is not a standalone
    # fix either — it would hit this exact same derivation failure — so
    # any mention of it here must show --actor alongside it.
    if "credential issue" in err:
        assert "--actor" in err.split("credential issue", 1)[1][:40]


def test_actor_derivation_fails_clearly_with_a_revoked_credential(hosted_env, capsys, monkeypatch):
    assert _run("org", "create", "--name", "Acme", "--owner-display-name", "Owner") == 0
    payload = json.loads(capsys.readouterr().out)
    owner_id = payload["owner"]["principal_id"]

    assert _run(
        "credential", "issue", "--actor", owner_id, "--principal-id", owner_id,
        "--no-connection-help",
    ) == 0
    credential_id, token = _parse_issued_credential(capsys.readouterr().out)

    assert _run("credential", "revoke", "--actor", owner_id, "--credential-id", credential_id) == 0
    capsys.readouterr()

    monkeypatch.setenv("MAK4I_TOKEN", token)
    exit_code = _run("principal", "list")
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "invalid or expired credential" in err
    assert "init --force" not in err  # see the audit note above `_resolve_actor`


def test_actor_derivation_fails_clearly_with_an_expired_credential(hosted_env, capsys, monkeypatch):
    assert _run("org", "create", "--name", "Acme", "--owner-display-name", "Owner") == 0
    payload = json.loads(capsys.readouterr().out)
    owner_id = payload["owner"]["principal_id"]

    assert _run(
        "credential", "issue", "--actor", owner_id, "--principal-id", owner_id,
        "--expires-at", "2020-01-01T00:00:00+00:00",  # already in the past
        "--no-connection-help",
    ) == 0
    _credential_id, token = _parse_issued_credential(capsys.readouterr().out)
    capsys.readouterr()

    monkeypatch.setenv("MAK4I_TOKEN", token)
    exit_code = _run("principal", "list")
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "invalid or expired credential" in err
    assert "init --force" not in err  # see the audit note above `_resolve_actor`


def test_local_credential_missing_does_not_fall_back_to_config_json(capsys):
    """Decision 1's explicit requirement: if `.mak4i/config.json` exists
    but `credentials.json` doesn't, derivation must fail clearly — never
    silently trust the cached `owner_principal_id`."""
    assert _init() == 0
    capsys.readouterr()

    localconfig.credentials_path().unlink()

    exit_code = _run("principal", "list")
    assert exit_code == 1
    assert "no --actor given and no credential available" in capsys.readouterr().err


# -- backward compatibility: explicit --actor is unchanged and wins -------


def test_explicit_actor_overrides_derivation(capsys):
    """The clearest possible proof explicit `--actor` isn't just "still
    accepted" but actually *used* in preference to derivation: the local
    credential authenticates to org 1's owner, but an explicit `--actor`
    for org 2's owner correctly scopes to org 2, not org 1."""
    assert _init(org="Org One") == 0
    capsys.readouterr()

    assert _run("org", "create", "--name", "Org Two", "--owner-display-name", "Owner Two") == 0
    org2 = json.loads(capsys.readouterr().out)
    org2_owner_id = org2["owner"]["principal_id"]
    org2_id = org2["organization"]["organization_id"]

    assert _run("principal", "list", "--actor", org2_owner_id) == 0
    principals = json.loads(capsys.readouterr().out)
    assert [p["principal_id"] for p in principals] == [org2_owner_id]
    assert [p["organization_id"] for p in principals] == [org2_id]


def test_existing_explicit_syntax_still_works_unchanged(capsys):
    """Today's exact pre-existing syntax — fully explicit --actor and
    --organization-id together — must keep working byte-for-byte."""
    assert _init() == 0
    config = localconfig.load()
    capsys.readouterr()

    assert (
        _run(
            "project", "list",
            "--actor", config.owner_principal_id,
            "--organization-id", config.organization_id,
        )
        == 0
    )
    projects = json.loads(capsys.readouterr().out)
    assert [p["project_id"] for p in projects] == [config.project_id]


# -- authorization is unchanged: derivation never weakens what's checked --


def test_non_owner_derived_actor_is_still_denied(capsys, monkeypatch):
    """A member (non-owner) principal's credential, once derived, must be
    denied by `_require_owner` exactly as an explicit `--actor` for that
    same member already is today — derivation changes *where* the actor
    id comes from, never *what* gets authorized."""
    assert _init() == 0
    config = localconfig.load()
    capsys.readouterr()

    assert _run(
        "principal", "create",
        "--actor", config.owner_principal_id,
        "--organization-id", config.organization_id,
        "--type", "human", "--display-name", "Member", "--role", "member",
    ) == 0
    member = json.loads(capsys.readouterr().out)
    member_id = member["principal_id"]

    assert _run(
        "credential", "issue",
        "--actor", config.owner_principal_id, "--principal-id", member_id,
        "--no-connection-help",
    ) == 0
    _credential_id, member_token = _parse_issued_credential(capsys.readouterr().out)
    capsys.readouterr()

    monkeypatch.setenv("MAK4I_TOKEN", member_token)
    exit_code = _run("principal", "list")
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "access denied" in err
    assert member_id not in err  # no leakage of who/what was denied


def test_cross_org_actor_cannot_list_another_organizations_principals(capsys):
    """Explicit `--actor` from org 1 targeting org 2's `--organization-id`
    — denied exactly as today, unaffected by this change."""
    assert _init(org="Org One") == 0
    config = localconfig.load()
    capsys.readouterr()

    assert _run("org", "create", "--name", "Org Two", "--owner-display-name", "Owner Two") == 0
    org2 = json.loads(capsys.readouterr().out)
    org2_id = org2["organization"]["organization_id"]
    capsys.readouterr()

    exit_code = _run(
        "principal", "list",
        "--actor", config.owner_principal_id,
        "--organization-id", org2_id,
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "access denied" in err
    assert "Org Two" not in err  # no cross-org leakage


# -- --help reflects the new precedence ------------------------------------


def _help_text(*argv: str, capsys) -> str:
    """argparse's `--help` prints and calls `sys.exit(0)` — it never
    returns normally from `parser.parse_args()`, so `_run()` isn't usable
    here directly."""
    with pytest.raises(SystemExit) as exc_info:
        _run(*argv)
    assert exc_info.value.code == 0
    return capsys.readouterr().out


def test_help_text_explains_actor_and_organization_id_derivation(capsys):
    for argv in (
        ("project", "create", "--help"),
        ("project", "list", "--help"),
        ("project", "show", "--help"),
        ("principal", "create", "--help"),
        ("principal", "list", "--help"),
        ("principal", "show", "--help"),
        ("grant", "create", "--help"),
        ("grant", "list", "--help"),
        ("grant", "revoke", "--help"),
        ("credential", "issue", "--help"),
        ("credential", "list", "--help"),
        ("credential", "revoke", "--help"),
    ):
        help_text = _help_text(*argv, capsys=capsys)
        assert "--actor" in help_text
        assert "authenticated credential" in help_text or "derived" in help_text

    for argv in (("project", "list", "--help"), ("principal", "list", "--help")):
        help_text = _help_text(*argv, capsys=capsys)
        assert "--organization-id" in help_text
        assert "default" in help_text.lower()

    # SHOW / destructive commands still show their target id as required
    # (no "[--...]" brackets in argparse's own usage line).
    for argv, required_flag in (
        (("org", "show", "--help"), "--organization-id"),
        (("project", "show", "--help"), "--project-id"),
        (("principal", "show", "--help"), "--principal-id"),
        (("grant", "revoke", "--help"), "--project-id"),
        (("credential", "revoke", "--help"), "--credential-id"),
    ):
        usage_line = _help_text(*argv, capsys=capsys).splitlines()[0]
        assert f"[{required_flag}" not in usage_line  # required, not optional
