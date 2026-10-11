"""Free administration without a portal (MAK-0006 §8, requirements R4).

Covers CONFORMANCE.md I6, I8–I10 for administration: owners administer
only their own organization, members (including holders of `resolve`) never
administer, every administrative action — successful or denied — leaves an
attributable event without secrets, lifecycle operations are safe (archive,
suspend, deactivate; no purge), and there is no remote admin endpoint.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine

from mak4i.identity import AccessDeniedError, ControlPlane, CredentialInvalidError
from mak4i.identity.admin_events import SqlAdminEventSink
from mak4i.identity.sql_store import SqlControlPlaneStore


@pytest.fixture
def cp(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cp.db'}", future=True)
    store = SqlControlPlaneStore(engine, create_tables=True)
    return ControlPlane(store, events=SqlAdminEventSink(engine))


@pytest.fixture
def world(cp):
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    other_org, other_owner = cp.onboard_organization(organization_name="Other", owner_display_name="Other owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P")
    member = cp.create_principal(actor=owner, organization_id=org.organization_id, type="human", display_name="M")
    resolver = cp.create_principal(actor=owner, organization_id=org.organization_id, type="human", display_name="R")
    cp.grant(actor=owner, principal_id=resolver.principal_id, project_id=project.project_id,
             permissions=["read", "write", "resolve"])
    return {"org": org, "owner": owner, "other_org": other_org, "other_owner": other_owner,
            "project": project, "member": member, "resolver": resolver}


def _admin_operations(cp, w):
    org = w["org"].organization_id
    target = w["member"].principal_id
    project = w["project"].project_id
    return {
        "project.create": lambda a: cp.create_project(actor=a, organization_id=org, name="x"),
        "principal.create": lambda a: cp.create_principal(actor=a, organization_id=org, type="human", display_name="x"),
        "principal.rename": lambda a: cp.rename_principal(actor=a, principal_id=target, display_name="x"),
        "principal.deactivate": lambda a: cp.deactivate_principal(actor=a, principal_id=target),
        "grant.set": lambda a: cp.grant(actor=a, principal_id=target, project_id=project, permissions=["read"]),
        "grant.revoke": lambda a: cp.revoke_grant(actor=a, principal_id=w["resolver"].principal_id, project_id=project),
        "credential.issue": lambda a: cp.issue_credential(actor=a, principal_id=target),
        "project.archive": lambda a: cp.archive_project(actor=a, project_id=project),
    }


@pytest.mark.parametrize("who", ["member", "resolver", "other_owner"])
def test_only_owners_of_the_organization_administer_it(cp, world, who):  # I8 / R4
    actor = world[who]
    for action, operation in _admin_operations(cp, world).items():
        with pytest.raises(AccessDeniedError):
            operation(actor)
        events = cp.list_admin_events(actor=world["owner"], organization_id=world["org"].organization_id)
        latest = events[0]
        assert (latest.action, latest.outcome, latest.actor_principal_id) == (action, "denied", actor.principal_id)
    # Nothing changed.
    assert cp.get_principal(world["member"].principal_id).status == "active"
    assert cp.show_project(actor=world["owner"], project_id=world["project"].project_id).status == "active"


def test_project_permissions_never_confer_admin_or_instance_rights(cp, world):  # I8
    with pytest.raises(AccessDeniedError):
        cp.list_principals(actor=world["resolver"], organization_id=world["org"].organization_id)
    with pytest.raises(AccessDeniedError):
        cp.list_admin_events(actor=world["resolver"], organization_id=world["org"].organization_id)
    with pytest.raises(AccessDeniedError):
        cp.list_admin_events(actor=world["owner"], organization_id=world["other_org"].organization_id)


def test_every_admin_action_is_attributable_and_secret_free(cp, world):  # I9 / I10
    credential, raw = cp.issue_credential(actor=world["owner"], principal_id=world["member"].principal_id)
    cp.revoke_credential(actor=world["owner"], credential_id=credential.credential_id)
    cp.rename_principal(actor=world["owner"], principal_id=world["member"].principal_id, display_name="Renamed")
    cp.deactivate_principal(actor=world["owner"], principal_id=world["member"].principal_id)
    events = cp.list_admin_events(actor=world["owner"], organization_id=world["org"].organization_id)
    actions = [e.action for e in events]
    for expected in ("organization.create", "project.create", "principal.create", "grant.set",
                     "credential.issue", "credential.revoke", "principal.rename", "principal.deactivate"):
        assert expected in actions
    issued = next(e for e in events if e.action == "credential.issue")
    assert issued.targets["credential_id"] == credential.credential_id
    assert issued.actor_principal_id == world["owner"].principal_id and issued.outcome == "succeeded"
    dump = json.dumps([e.model_dump(mode="json") for e in events])
    assert raw not in dump and credential.token_hash not in dump


def test_events_are_durable_across_restarts(cp, world, tmp_path):  # §8.5
    restarted = ControlPlane(
        SqlControlPlaneStore(f"sqlite:///{tmp_path / 'cp.db'}"),
        events=SqlAdminEventSink(create_engine(f"sqlite:///{tmp_path / 'cp.db'}")),
    )
    assert len(restarted.list_admin_events(actor=world["owner"], organization_id=world["org"].organization_id)) >= 5


def test_archive_is_safe_and_stops_access(cp, world):  # §8.4 / I6
    project = world["project"].project_id
    assert cp.effective_permissions(world["resolver"], project) == ["read", "write", "resolve"]
    archived = cp.archive_project(actor=world["owner"], project_id=project)
    assert archived.status == "archived"
    assert cp.effective_permissions(world["resolver"], project) == []
    assert cp.list_authorized_projects(world["resolver"]) == []
    assert cp.show_project(actor=world["owner"], project_id=project).project_id == project  # not deleted


def test_suspending_an_organization_stops_authentication(cp, world):  # MAK-0006 §1.2
    _cred, raw = cp.issue_credential(actor=world["owner"], principal_id=world["resolver"].principal_id)
    cp.set_organization_status(organization_id=world["org"].organization_id, status="suspended")
    with pytest.raises(CredentialInvalidError):
        cp.authenticate(raw)
    cp.set_organization_status(organization_id=world["org"].organization_id, status="active")
    assert cp.authenticate(raw).principal_id == world["resolver"].principal_id
    events = cp.list_admin_events(actor=world["owner"], organization_id=world["org"].organization_id)
    assert {"organization.suspend", "organization.reactivate"} <= {e.action for e in events}
    assert next(e for e in events if e.action == "organization.suspend").actor_principal_id == "operator"


def test_no_destructive_purge_exists(cp):  # §8.4
    for name in dir(cp):
        assert not name.startswith(("delete_", "purge_", "destroy_")), name


def test_no_remote_administration_endpoint(tmp_path):  # §8.1
    import anyio

    from mak4i.api import MAK4IEngine
    from mak4i.identity import Authorizer
    from mak4i.identity.memory_store import InMemoryControlPlaneStore
    from mak4i.mcp_server import build_http_app, build_server
    from mak4i.store.local_json import LocalJSONStore

    cp = ControlPlane(InMemoryControlPlaneStore())
    server = build_server(
        MAK4IEngine(LocalJSONStore(tmp_path / "a"), authorizer=Authorizer(cp), control_plane=cp)
    )
    tools = anyio.run(server.list_tools)
    assert not any(
        word in t.name for t in tools for word in ("grant", "principal", "credential", "org", "admin", "audit")
    )
    app = build_http_app(server, control_plane=cp)
    paths = {getattr(r, "path", "") for r in app.router.routes}
    assert not any(p.startswith(("/admin", "/api")) for p in paths)


def test_cli_lifecycle_audit_and_inventory(tmp_path, monkeypatch, capsys):  # R4 / issue #21
    from mak4i import cli

    db = tmp_path / "cli.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{db}")
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", "1")
    monkeypatch.setenv("MAK4I_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["org", "create", "--name", "CLI Org", "--owner-display-name", "Owner"]) == 0
    created = json.loads(capsys.readouterr().out)
    org_id, owner_id = created["organization"]["organization_id"], created["owner"]["principal_id"]
    assert cli.main(["project", "create", "--actor", owner_id, "--organization-id", org_id, "--name", "P"]) == 0
    project_id = json.loads(capsys.readouterr().out)["project_id"]
    assert cli.main(["credential", "issue", "--actor", owner_id, "--principal-id", owner_id, "--no-connection-help"]) == 0
    raw = next(line.split("Bearer ")[1] for line in capsys.readouterr().out.splitlines() if "Bearer " in line)
    monkeypatch.setenv("MAK4I_TOKEN", raw)

    assert cli.main(["project", "archive", "--project-id", project_id]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "archived"

    assert cli.main(["audit", "list"]) == 0
    events = json.loads(capsys.readouterr().out)
    archive = next(e for e in events if e["action"] == "project.archive")
    assert archive["actor_auth_method"] == "credential" and archive["actor_principal_id"] == owner_id

    assert cli.main(["admin", "inventory"]) == 0
    out = capsys.readouterr().out
    inventory = json.loads(out)
    (org,) = inventory["organizations"]
    assert org["owners"] == [owner_id] and org["projects"][0]["status"] == "archived"
    assert raw not in out and "token_hash" not in out and "can't be recovered" in inventory["note"]

    assert cli.main(["org", "suspend", "--organization-id", org_id]) == 0
    capsys.readouterr()
    assert cli.main(["audit", "list"]) != 0  # the suspended org's credential no longer authenticates
    assert cli.main(["org", "reactivate", "--organization-id", org_id]) == 0
