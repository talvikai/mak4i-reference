import json
from datetime import datetime, timezone

import pytest

from mak4i import cli
from mak4i.models import Artifact
from mak4i.store.local_json import LocalJSONStore

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def local_store_env(tmp_path, monkeypatch):
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
    assert "token (shown once" in issue_output
    credential_id = issue_output.splitlines()[0].split(": ", 1)[1]

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
