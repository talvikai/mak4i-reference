import json
from datetime import datetime, timezone

import pytest

from mak4i import cli
from mak4i.models import Artifact
from mak4i.store.local_json import LocalJSONStore

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def local_store_env(tmp_path, monkeypatch):
    # MAK4I_HOME is isolated to tmp_path defensively, on top of the
    # explicit MAK4I_CONTROL_PLANE_DB/MAK4I_STORE/MAK4I_LOCAL_STORE_DIR
    # below (which already make `localconfig.resolve_ambient_local_
    # environment()` a no-op here — see its docstring): tests in this
    # file must never be able to discover, let alone write to, whatever
    # `.mak4i/` happens to exist in the developer's actual working
    # directory, regardless of how the resolution precedence evolves.
    monkeypatch.setenv("MAK4I_HOME", str(tmp_path / ".mak4i"))
    monkeypatch.setenv("MAK4I_STORE", "local")
    monkeypatch.setenv("MAK4I_LOCAL_STORE_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{tmp_path / 'control-plane.db'}")
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", "1")
    return tmp_path / "artifacts"


@pytest.fixture
def world(capsys):
    """Bootstraps one org/owner/project via the CLI itself and grants the
    owner read+write on it — the minimal authorized world every artifact
    subcommand test starts from. Returns (organization_id, owner_id,
    project_id)."""
    assert cli.main(["org", "create", "--name", "WD Technology Solutions", "--owner-display-name", "Owner"]) == 0
    org_payload = json.loads(capsys.readouterr().out)
    organization_id = org_payload["organization"]["organization_id"]
    owner_id = org_payload["owner"]["principal_id"]

    assert cli.main(
        ["project", "create", "--actor", owner_id, "--organization-id", organization_id, "--name", "schedovia"]
    ) == 0
    project_id = json.loads(capsys.readouterr().out)["project_id"]

    assert cli.main(
        [
            "grant", "create",
            "--actor", owner_id,
            "--principal-id", owner_id,
            "--project-id", project_id,
            "--permissions", "read,write",
        ]
    ) == 0
    capsys.readouterr()

    return organization_id, owner_id, project_id


def test_create_prints_artifact_json_and_returns_zero(world, capsys):
    _organization_id, owner_id, project_id = world
    exit_code = cli.main(
        [
            "create",
            "--principal", owner_id,
            "--project", project_id,
            "--artifact-id", "decision-cache-001",
            "--artifact-type", "architecture_decision",
            "--title", "Application caching technology",
            "--content", "Use Redis for application caching.",
            "--rationale", "Fast shared caching.",
            "--tags", "caching,redis,architecture",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["artifact_id"] == "decision-cache-001"
    assert payload["status"] == "active"
    assert payload["tags"] == ["caching", "redis", "architecture"]
    assert payload["created_by"] == owner_id


def test_create_duplicate_id_errors_with_nonzero_exit(world, capsys):
    _organization_id, owner_id, project_id = world
    args = [
        "create",
        "--principal", owner_id,
        "--project", project_id,
        "--artifact-id", "decision-cache-001",
        "--artifact-type", "architecture_decision",
        "--title", "Application caching technology",
        "--content", "Use Redis for application caching.",
    ]
    assert cli.main(args) == 0
    exit_code = cli.main(args)
    assert exit_code == 1
    assert "already exists" in capsys.readouterr().err


def test_create_without_a_grant_is_denied(world, capsys):
    organization_id, owner_id, project_id = world
    assert cli.main(
        [
            "principal", "create",
            "--actor", owner_id,
            "--organization-id", organization_id,
            "--type", "human",
            "--display-name", "No Grant",
        ]
    ) == 0
    ungranted_id = json.loads(capsys.readouterr().out)["principal_id"]

    exit_code = cli.main(
        [
            "create",
            "--principal", ungranted_id,
            "--project", project_id,
            "--artifact-id", "decision-cache-001",
            "--artifact-type", "architecture_decision",
            "--title", "Application caching technology",
            "--content", "Use Redis for application caching.",
        ]
    )
    assert exit_code == 1
    assert "access denied" in capsys.readouterr().err


def test_supersede_prints_new_artifact_and_bumps_lineage(world, capsys):
    _organization_id, owner_id, project_id = world
    cli.main(
        [
            "create",
            "--principal", owner_id,
            "--project", project_id,
            "--artifact-id", "decision-db-001",
            "--artifact-type", "architecture_decision",
            "--title", "Primary database technology",
            "--content", "Use PostgreSQL.",
        ]
    )
    capsys.readouterr()  # discard create output

    exit_code = cli.main(
        [
            "supersede",
            "--principal", owner_id,
            "--project", project_id,
            "--old-id", "decision-db-001",
            "--content", "Use MySQL.",
            "--reason", "Team familiarity and managed-hosting availability.",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["artifact_id"] == "decision-db-002"
    assert payload["supersedes"] == "decision-db-001"


def test_supersede_nonexistent_old_id_errors(world, capsys):
    _organization_id, owner_id, project_id = world
    exit_code = cli.main(
        [
            "supersede",
            "--principal", owner_id,
            "--project", project_id,
            "--old-id", "does-not-exist",
            "--content", "x",
            "--reason", "x",
        ]
    )
    assert exit_code == 1
    assert "not found" in capsys.readouterr().err


def test_search_and_get_current_and_history(world, capsys):
    _organization_id, owner_id, project_id = world
    cli.main(
        [
            "create",
            "--principal", owner_id,
            "--project", project_id,
            "--artifact-id", "decision-cache-001",
            "--artifact-type", "architecture_decision",
            "--title", "Application caching technology",
            "--content", "Use Redis for application caching.",
            "--tags", "caching,redis",
        ]
    )
    capsys.readouterr()

    assert cli.main(
        ["search", "--principal", owner_id, "--project", project_id, "--tags", "caching"]
    ) == 0
    search_payload = json.loads(capsys.readouterr().out)
    assert [a["artifact_id"] for a in search_payload] == ["decision-cache-001"]

    assert cli.main(
        ["get-current", "--principal", owner_id, "--project", project_id, "--tags", "caching"]
    ) == 0
    package_payload = json.loads(capsys.readouterr().out)
    assert [a["artifact_id"] for a in package_payload["artifacts"]] == ["decision-cache-001"]
    assert package_payload["conflicts"] == []

    assert cli.main(
        [
            "history",
            "--principal", owner_id,
            "--project", project_id,
            "--lineage-id", "decision-cache-001",
        ]
    ) == 0
    history_payload = json.loads(capsys.readouterr().out)
    assert [a["artifact_id"] for a in history_payload] == ["decision-cache-001"]


def test_doctor_reports_ok_for_healthy_project(world, capsys):
    _organization_id, owner_id, project_id = world
    cli.main(
        [
            "create",
            "--principal", owner_id,
            "--project", project_id,
            "--artifact-id", "decision-cache-001",
            "--artifact-type", "architecture_decision",
            "--title", "Application caching technology",
            "--content", "Use Redis for application caching.",
        ]
    )
    capsys.readouterr()

    exit_code = cli.main(["doctor", "--principal", owner_id, "--project", project_id])
    assert exit_code == 0
    assert "OK" in capsys.readouterr().out


def test_doctor_reports_failure_for_zero_active_lineage(world, local_store_env, capsys):
    organization_id, owner_id, project_id = world
    store = LocalJSONStore(local_store_env)
    store.put_new(
        Artifact(
            artifact_id="decision-cache-001",
            artifact_type="architecture_decision",
            organization_id=organization_id,
            project=project_id,
            title="Application caching technology",
            content="Use Redis for application caching.",
            rationale=None,
            status="superseded",
            version="1.0",
            created_by=owner_id,
            created_at=NOW,
            updated_at=NOW,
            lineage_id="decision-cache-001",
            supersedes=None,
            superseded_by="decision-cache-002",
            tags=["caching"],
        )
    )

    exit_code = cli.main(["doctor", "--principal", owner_id, "--project", project_id])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "zero_active" in err


def test_unknown_principal_reports_clear_error_not_traceback(world, capsys):
    _organization_id, _owner_id, project_id = world
    exit_code = cli.main(
        ["doctor", "--principal", "prn_does-not-exist", "--project", project_id]
    )
    assert exit_code == 1
    assert "not found" in capsys.readouterr().err


def test_missing_gcs_env_var_reports_clear_error_not_traceback(world, monkeypatch, capsys):
    _organization_id, owner_id, project_id = world
    monkeypatch.setenv("MAK4I_STORE", "gcs")
    monkeypatch.delenv("MAK4I_GCS_BUCKET", raising=False)

    exit_code = cli.main(["doctor", "--principal", owner_id, "--project", project_id])
    assert exit_code == 1
    assert "MAK4I_GCS_BUCKET" in capsys.readouterr().err


def test_control_plane_bootstrap_flow_org_project_principal_grant_credential(capsys):
    """Exercises every control-plane subcommand end to end — the exact
    operator workflow docs/DEMO.md's §10 acceptance flow starts from."""
    assert cli.main(
        ["org", "create", "--name", "Talvik", "--owner-display-name", "Owner B"]
    ) == 0
    org_payload = json.loads(capsys.readouterr().out)
    organization_id = org_payload["organization"]["organization_id"]
    owner_id = org_payload["owner"]["principal_id"]

    assert cli.main(
        ["project", "create", "--actor", owner_id, "--organization-id", organization_id, "--name", "Talvik Website"]
    ) == 0
    project_id = json.loads(capsys.readouterr().out)["project_id"]

    assert cli.main(
        [
            "principal", "create",
            "--actor", owner_id,
            "--organization-id", organization_id,
            "--type", "human",
            "--display-name", "Dev B",
        ]
    ) == 0
    dev_id = json.loads(capsys.readouterr().out)["principal_id"]

    assert cli.main(
        [
            "grant", "create",
            "--actor", owner_id,
            "--principal-id", dev_id,
            "--project-id", project_id,
            "--permissions", "read,write",
        ]
    ) == 0
    grant_payload = json.loads(capsys.readouterr().out)
    assert set(grant_payload["permissions"]) == {"read", "write"}

    assert cli.main(
        [
            "credential", "issue",
            "--actor", owner_id,
            "--principal-id", dev_id,
            "--display-name", "Dev B's laptop",
        ]
    ) == 0
    issue_output = capsys.readouterr().out
    assert "credential_id:" in issue_output
    assert "Authorization: Bearer " in issue_output
    assert "will not be shown again" in issue_output
    credential_id = next(
        line.split(": ", 1)[1] for line in issue_output.splitlines() if line.startswith("credential_id:")
    )

    assert cli.main(
        ["grant", "revoke", "--actor", owner_id, "--principal-id", dev_id, "--project-id", project_id]
    ) == 0
    capsys.readouterr()

    # dev_id no longer has a grant, so it can no longer read the project.
    exit_code = cli.main(["search", "--principal", dev_id, "--project", project_id])
    assert exit_code == 1
    assert "access denied" in capsys.readouterr().err

    assert cli.main(
        ["credential", "revoke", "--actor", owner_id, "--credential-id", credential_id]
    ) == 0
    revoked_payload = json.loads(capsys.readouterr().out)
    assert revoked_payload["status"] == "revoked"
    assert "token_hash" not in revoked_payload and "token" not in revoked_payload


# -- self-service list/show subcommands (enterprise self-hosted, §4-§8) ------


def test_org_list_and_show(world, capsys):
    organization_id, _owner_id, _project_id = world
    assert cli.main(["org", "list"]) == 0
    orgs = json.loads(capsys.readouterr().out)
    assert organization_id in {o["organization_id"] for o in orgs}

    assert cli.main(["org", "show", "--organization-id", organization_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["organization_id"] == organization_id

    exit_code = cli.main(["org", "show", "--organization-id", "org_does-not-exist"])
    assert exit_code == 1
    assert "no such organization" in capsys.readouterr().err


def test_project_list_and_show(world, capsys):
    organization_id, owner_id, project_id = world
    assert cli.main(["project", "list", "--actor", owner_id, "--organization-id", organization_id]) == 0
    projects = json.loads(capsys.readouterr().out)
    assert project_id in {p["project_id"] for p in projects}

    assert cli.main(["project", "show", "--actor", owner_id, "--project-id", project_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["project_id"] == project_id


def test_project_create_duplicate_name_errors_cleanly(world, capsys):
    """`world` already created a project named 'schedovia' — creating it
    again in the same organization must be a clean CLI error, not a raw
    Python traceback (regression test for the duplicate-project bug)."""
    organization_id, owner_id, _project_id = world
    exit_code = cli.main(
        ["project", "create", "--actor", owner_id, "--organization-id", organization_id, "--name", "schedovia"]
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "an active project named 'schedovia' already exists in this organization" in err
    assert "Traceback" not in err


def test_grant_create_cross_organization_errors_cleanly(capsys):
    """Granting a principal access to another organization's project must
    be a clean CLI error, not a raw Python traceback."""
    assert cli.main(["org", "create", "--name", "Org A", "--owner-display-name", "Owner A"]) == 0
    org_a_payload = json.loads(capsys.readouterr().out)
    organization_a = org_a_payload["organization"]["organization_id"]
    owner_a = org_a_payload["owner"]["principal_id"]

    assert cli.main(
        ["project", "create", "--actor", owner_a, "--organization-id", organization_a, "--name", "Project A"]
    ) == 0
    project_a = json.loads(capsys.readouterr().out)["project_id"]

    assert cli.main(["org", "create", "--name", "Org B", "--owner-display-name", "Owner B"]) == 0
    owner_b = json.loads(capsys.readouterr().out)["owner"]["principal_id"]

    exit_code = cli.main(
        [
            "grant", "create",
            "--actor", owner_b,
            "--principal-id", owner_b,
            "--project-id", project_a,
            "--permissions", "read",
        ]
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "cannot grant a principal access to another organization's project" in err
    assert "Traceback" not in err


def test_principal_list_and_show(world, capsys):
    organization_id, owner_id, _project_id = world
    assert cli.main(
        ["principal", "create", "--actor", owner_id, "--organization-id", organization_id, "--type", "human", "--display-name", "Dev"]
    ) == 0
    dev_id = json.loads(capsys.readouterr().out)["principal_id"]

    assert cli.main(["principal", "list", "--actor", owner_id, "--organization-id", organization_id]) == 0
    listed = {p["principal_id"] for p in json.loads(capsys.readouterr().out)}
    assert {owner_id, dev_id} == listed

    assert cli.main(["principal", "show", "--actor", owner_id, "--principal-id", dev_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["display_name"] == "Dev"


def test_grant_list(world, capsys):
    organization_id, owner_id, project_id = world
    assert cli.main(
        ["principal", "create", "--actor", owner_id, "--organization-id", organization_id, "--type", "human", "--display-name", "Dev"]
    ) == 0
    dev_id = json.loads(capsys.readouterr().out)["principal_id"]
    assert cli.main(
        ["grant", "create", "--actor", owner_id, "--principal-id", dev_id, "--project-id", project_id, "--permissions", "read"]
    ) == 0
    capsys.readouterr()

    assert cli.main(["grant", "list", "--actor", owner_id, "--principal-id", dev_id]) == 0
    grants = json.loads(capsys.readouterr().out)
    assert [g["project_id"] for g in grants] == [project_id]


def test_credential_list_never_prints_a_token_or_hash(world, capsys):
    organization_id, owner_id, project_id = world
    assert cli.main(
        ["credential", "issue", "--actor", owner_id, "--principal-id", owner_id, "--display-name", "Claude Code", "--no-connection-help"]
    ) == 0
    issue_output = capsys.readouterr().out
    assert "Connect an AI client" not in issue_output  # --no-connection-help honored
    raw_token = next(
        line.split("Authorization: Bearer ", 1)[1]
        for line in issue_output.splitlines()
        if line.startswith("Authorization: Bearer ")
    )

    assert cli.main(["credential", "list", "--actor", owner_id, "--principal-id", owner_id]) == 0
    list_output = capsys.readouterr().out
    assert raw_token not in list_output
    listed = json.loads(list_output)
    assert all("token_hash" not in c and "token" not in c for c in listed)
    assert any(c["display_name"] == "Claude Code" for c in listed)


def test_credential_issue_prints_generic_not_talvik_specific_connection_instructions(world, capsys, monkeypatch):
    monkeypatch.delenv("MAK4I_PUBLIC_ENDPOINT", raising=False)
    _organization_id, owner_id, _project_id = world
    assert cli.main(
        ["credential", "issue", "--actor", owner_id, "--principal-id", owner_id, "--display-name", "Claude Code"]
    ) == 0
    output = capsys.readouterr().out
    assert "claude mcp add mak4i" in output
    assert "<your-mak4i-endpoint>/mcp" in output
    assert "mak4i serve" in output
    assert "talvik" not in output.lower()


def test_credential_issue_uses_public_endpoint_and_omits_stdio_guidance(world, capsys, monkeypatch):
    """An Enterprise Self-Hosted operator sets MAK4I_PUBLIC_ENDPOINT (the
    full client-facing MCP URL); the connect command must use it verbatim
    and must not suggest the local stdio `mak4i serve` path."""
    monkeypatch.setenv("MAK4I_PUBLIC_ENDPOINT", "https://mak4i.example.com/mcp")
    _organization_id, owner_id, _project_id = world
    assert cli.main(
        ["credential", "issue", "--actor", owner_id, "--principal-id", owner_id, "--display-name", "Claude Code"]
    ) == 0
    output = capsys.readouterr().out
    raw_token = next(
        line.split("Authorization: Bearer ", 1)[1]
        for line in output.splitlines()
        if line.startswith("Authorization: Bearer ")
    )
    assert "claude mcp add mak4i --transport http https://mak4i.example.com/mcp" in output
    assert f'--header "Authorization: Bearer {raw_token}"' in output
    assert "<your-mak4i-endpoint>" not in output
    assert "mak4i serve" not in output
    assert "stdio" not in output


def test_admin_list_show_commands_deny_cross_organization_access(capsys):
    """§16: unauthorized admin actions / cross-org isolation, exercised at
    the CLI layer (not just ControlPlane directly)."""
    assert cli.main(["org", "create", "--name", "Org A", "--owner-display-name", "A"]) == 0
    owner_a = json.loads(capsys.readouterr().out)["owner"]["principal_id"]

    assert cli.main(["org", "create", "--name", "Org B", "--owner-display-name", "B"]) == 0
    org_b_payload = json.loads(capsys.readouterr().out)
    organization_b = org_b_payload["organization"]["organization_id"]

    exit_code = cli.main(["principal", "list", "--actor", owner_a, "--organization-id", organization_b])
    assert exit_code == 1
    assert "access denied" in capsys.readouterr().err

    exit_code = cli.main(["project", "list", "--actor", owner_a, "--organization-id", organization_b])
    assert exit_code == 1
    assert "access denied" in capsys.readouterr().err


def test_subject_key_create_conflict_and_release_via_cli(world, capsys):
    """Issue #5 through the operator CLI: --subject-key on create, a clean
    error for a key change, and --release-subject-key to resolve."""
    _organization_id, owner_id, project_id = world

    def create(artifact_id: str) -> None:
        assert cli.main([
            "create", "--principal", owner_id, "--project", project_id,
            "--artifact-id", artifact_id, "--artifact-type", "architecture_decision",
            "--title", artifact_id, "--content", artifact_id, "--tags", "caching,architecture",
            "--subject-key", "session-cache",
        ]) == 0
        assert json.loads(capsys.readouterr().out)["subject_key"] == "session-cache"

    create("use-redis")
    create("use-memcached")
    assert cli.main(["get-current", "--principal", owner_id, "--project", project_id]) == 0
    assert len(json.loads(capsys.readouterr().out)["conflicts"]) == 1

    assert cli.main([
        "supersede", "--principal", owner_id, "--project", project_id, "--old-id", "use-memcached",
        "--content", "x", "--reason", "r", "--subject-key", "database",
    ]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "Traceback" not in err

    # v2: release mid-conflict is rejected; `conflict resolve` settles it
    # (a human resolver through the operator CLI).
    assert cli.main([
        "supersede", "--principal", owner_id, "--project", project_id, "--old-id", "use-memcached",
        "--content", "Memcached rejected", "--reason", "resolved", "--release-subject-key",
    ]) == 1
    assert "open conflict" in capsys.readouterr().err

    assert cli.main(["conflict", "list", "--principal", owner_id, "--project", project_id]) == 0
    conflict = json.loads(capsys.readouterr().out)[0]
    candidates = ",".join(c["artifact_id"] for c in conflict["candidates"])
    resolve = [
        "conflict", "resolve", "--principal", owner_id, "--project", project_id,
        "--conflict-id", conflict["conflict_id"], "--candidates", candidates,
        "--action", "select_winner", "--winner", "use-redis", "--reason", "Redis chosen",
    ]
    assert cli.main(resolve) == 1  # no `resolve` permission yet
    assert "access denied" in capsys.readouterr().err
    assert cli.main([
        "grant", "create", "--actor", owner_id, "--principal-id", owner_id,
        "--project-id", project_id, "--permissions", "read,write,resolve",
    ]) == 0
    capsys.readouterr()
    assert cli.main(resolve) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["state"] == "completed" and record["actor"]["auth_method"] == "operator"

    assert cli.main(["get-current", "--principal", owner_id, "--project", project_id]) == 0
    assert json.loads(capsys.readouterr().out)["conflicts"] == []
    assert cli.main(["conflict", "list", "--principal", owner_id, "--project", project_id, "--state", "resolved"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["state"] == "resolved"


def test_version_flag_reports_the_installed_package_version(capsys):
    from mak4i import __version__

    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"mak4i {__version__}"
    # pyproject.toml is the one authoritative version source.
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]
