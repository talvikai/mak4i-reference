"""Authenticated agent identity and provenance (MAK-0006 §2, §3, §7).

Maps to CONFORMANCE.md I1–I4 and the R2 acceptance criteria: two distinct
agent principals with separate grants write attributable records; a forged
agent_id can't change authorization or recorded authorship; human OAuth use
still works.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mak4i.identity import AgentIdTakenError, Principal
from tests.test_oauth import World


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    with w.client:
        yield w


def _agent(world, agent_id, *, permissions=("read", "write"), display_name=None):
    agent = world.cp.create_principal(
        actor=world.owner,
        organization_id=world.org.organization_id,
        type="agent",
        display_name=display_name or f"Agent {agent_id}",
        agent_id=agent_id,
    )
    if permissions:
        world.cp.grant(
            actor=world.owner,
            principal_id=agent.principal_id,
            project_id=world.project.project_id,
            permissions=list(permissions),
        )
    _cred, raw = world.cp.issue_credential(actor=world.owner, principal_id=agent.principal_id)
    return agent, raw


def _create(world, token, artifact_id, **extra):
    args = {
        "project": world.project.project_id,
        "artifact_id": artifact_id,
        "artifact_type": "release_note",
        "title": "Note",
        "content": "content",
    }
    args.update(extra)
    _http, rpc = world.call_tool(token, "mak4i_create", args)
    return rpc["result"]


# -- I1: the agent_id contract ------------------------------------------------------


def test_agent_principals_require_a_valid_agent_id(world):  # I1
    with pytest.raises(ValidationError):
        world.cp.create_principal(
            actor=world.owner, organization_id=world.org.organization_id, type="agent", display_name="A"
        )
    for bad in ("A", "ab", "Release-Bot", "-bot", "bot-", "bot id", "x" * 65, "bot/1"):
        with pytest.raises(ValidationError):
            world.cp.create_principal(
                actor=world.owner,
                organization_id=world.org.organization_id,
                type="agent",
                display_name="A",
                agent_id=bad,
            )
    for kind in ("human", "service"):
        with pytest.raises(ValidationError):
            world.cp.create_principal(
                actor=world.owner,
                organization_id=world.org.organization_id,
                type=kind,
                display_name="H",
                agent_id="not-allowed",
            )


def test_agent_id_is_unique_per_organization_and_never_reused(world):  # I1
    agent, _ = _agent(world, "release-bot")
    with pytest.raises(AgentIdTakenError):
        _agent(world, "release-bot")
    world.cp.deactivate_principal(actor=world.owner, principal_id=agent.principal_id)
    with pytest.raises(AgentIdTakenError):  # reserved even after deactivation
        _agent(world, "release-bot")
    other_org, other_owner = world.cp.onboard_organization(organization_name="Other", owner_display_name="O")
    elsewhere = world.cp.create_principal(
        actor=other_owner, organization_id=other_org.organization_id, type="agent", display_name="X", agent_id="release-bot"
    )
    assert elsewhere.agent_id == "release-bot"  # unrelated agent in another organization


def test_agent_id_is_immutable_and_rename_changes_only_the_display_name(world):  # I1 / §2.4
    agent, raw = _agent(world, "doc-bot", display_name="Doc bot")
    first = _create(world, raw, "doc-1")["structuredContent"]
    renamed = world.cp.rename_principal(actor=world.owner, principal_id=agent.principal_id, display_name="Docs agent")
    assert (renamed.principal_id, renamed.agent_id) == (agent.principal_id, "doc-bot")
    second = _create(world, raw, "doc-2")["structuredContent"]
    # History keeps the name it was written under; new writes use the new one.
    assert first["provenance"]["display_name"] == "Doc bot"
    assert second["provenance"]["display_name"] == "Docs agent"
    assert first["provenance"]["agent_id"] == second["provenance"]["agent_id"] == "doc-bot"
    assert not hasattr(world.cp, "change_agent_id")


# -- I2 / R2: two agents, separate grants, attributable records ------------------------


def test_two_agents_with_separate_grants_write_attributable_records(world):  # I2
    writer, writer_token = _agent(world, "writer-bot", permissions=("read", "write"))
    reader, reader_token = _agent(world, "reader-bot", permissions=("read",))

    written = _create(world, writer_token, "w-1")
    assert written["isError"] is False
    record = written["structuredContent"]
    assert record["created_by"] == writer.principal_id
    assert record["provenance"]["principal_id"] == writer.principal_id
    assert record["provenance"]["principal_type"] == "agent"
    assert record["provenance"]["agent_id"] == "writer-bot"
    assert record["provenance"]["auth_method"] == "credential"
    assert record["provenance"]["credential_id"]

    denied = _create(world, reader_token, "r-1")
    assert denied["isError"] is True and "access denied" in denied["content"][0]["text"]

    _http, rpc = world.call_tool(reader_token, "mak4i_history", {"project": world.project.project_id, "lineage_id": "w-1"})
    history = rpc["result"]["structuredContent"]["result"]
    assert [v["provenance"]["agent_id"] for v in history] == ["writer-bot"]


def test_agents_can_write_through_oauth_and_are_still_the_author(world):  # I2 / §3.8
    agent, _ = _agent(world, "oauth-bot")
    tokens = world.tokens(principal_id=agent.principal_id)
    record = _create(world, tokens["access_token"], "o-1")["structuredContent"]
    provenance = record["provenance"]
    assert provenance["agent_id"] == "oauth-bot"
    assert provenance["auth_method"] == "oauth"
    assert provenance["oauth_client_id"] == world.public_client["client_id"]
    assert provenance["credential_id"] is None


# -- I3: spoofing --------------------------------------------------------------------


def test_forged_identity_arguments_change_nothing(world):  # I3 / R2 acceptance
    writer, writer_token = _agent(world, "writer-bot", permissions=("read", "write"))
    reader, reader_token = _agent(world, "reader-bot", permissions=("read",))
    forged = {
        "agent_id": "writer-bot",
        "principal_id": writer.principal_id,
        "created_by": writer.principal_id,
        "author": "writer-bot",
        "actor": writer.principal_id,
        "provenance": {"agent_id": "writer-bot"},
    }
    # A read-only agent claiming to be the writer gains no write access.
    denied = _create(world, reader_token, "spoof-1", **forged)
    assert denied["isError"] is True

    # A writer claiming to be someone else is still recorded as itself.
    victim, _ = _agent(world, "victim-bot")
    forged = {"agent_id": "victim-bot", "created_by": victim.principal_id, "principal_id": victim.principal_id}
    record = _create(world, writer_token, "spoof-2", **forged)["structuredContent"]
    assert record["created_by"] == writer.principal_id
    assert record["provenance"]["agent_id"] == "writer-bot"
    assert record["provenance"]["principal_id"] == writer.principal_id


def test_identity_cannot_be_injected_into_the_engine_from_tool_arguments(world, tmp_path):  # I3
    """The published tool schemas don't even declare identity arguments."""
    import anyio

    tools = anyio.run(_list_tool_schemas, world, tmp_path)
    for name in ("mak4i_create", "mak4i_supersede"):
        properties = set(tools[name]["properties"])
        assert not properties & {"agent_id", "principal_id", "created_by", "author", "actor", "provenance", "ctx"}


async def _list_tool_schemas(world, tmp_path):
    from mak4i.api import MAK4IEngine
    from mak4i.identity import Authorizer
    from mak4i.mcp_server import build_server
    from mak4i.store.local_json import LocalJSONStore

    server = build_server(
        MAK4IEngine(LocalJSONStore(tmp_path / "schemas"), authorizer=Authorizer(world.cp), control_plane=world.cp)
    )
    return {t.name: t.input_schema for t in await server.list_tools()}


# -- I4: client names are unverified metadata ------------------------------------------


def test_client_names_are_recorded_only_as_unverified_metadata(world):  # I4 / §7.3
    agent, raw = _agent(world, "meta-bot")
    record = _create(world, raw, "m-1")["structuredContent"]
    assert record["provenance"]["client"] == {"name": "pytest", "version": "1", "verified": False}
    assert record["provenance"]["agent_id"] == "meta-bot"  # not the client name


def test_human_oauth_use_needs_no_agent_and_creates_none(world):  # R2 / §3.5
    before = {p.principal_id for p in world.cp.list_principals(actor=world.owner, organization_id=world.org.organization_id)}
    tokens = world.tokens()  # the human owner signs in through an OAuth client
    record = _create(world, tokens["access_token"], "h-1")["structuredContent"]
    assert record["provenance"]["principal_type"] == "human"
    assert record["provenance"]["agent_id"] is None
    _http, rpc = world.call_tool(tokens["access_token"], "mak4i_whoami")
    assert rpc["result"]["structuredContent"]["principal"]["agent_id"] is None
    after = {p.principal_id for p in world.cp.list_principals(actor=world.owner, organization_id=world.org.organization_id)}
    assert after == before  # no implicit agent principal for the client


def test_whoami_reports_the_stored_agent_identity(world):
    agent, raw = _agent(world, "who-bot")
    _http, rpc = world.call_tool(raw, "mak4i_whoami")
    principal = rpc["result"]["structuredContent"]["principal"]
    assert principal == {
        "principal_id": agent.principal_id,
        "display_name": "Agent who-bot",
        "principal_type": "agent",
        "type": "agent",
        "role": "member",
        "agent_id": "who-bot",
    }


# -- lifecycle -----------------------------------------------------------------------


def test_deactivated_agent_keeps_its_history_and_loses_access(world):  # §2.3
    agent, raw = _agent(world, "retired-bot")
    _create(world, raw, "ret-1")
    tokens = world.tokens(principal_id=agent.principal_id)
    world.cp.deactivate_principal(actor=world.owner, principal_id=agent.principal_id)
    assert world.mcp(raw, "initialize", {}).status_code == 401
    assert world.mcp(tokens["access_token"], "initialize", {}).status_code == 401
    _cred, owner_token = world.cp.issue_credential(actor=world.owner, principal_id=world.owner.principal_id)
    _http, rpc = world.call_tool(owner_token, "mak4i_history", {"project": world.project.project_id, "lineage_id": "ret-1"})
    assert rpc["result"]["structuredContent"]["result"][0]["provenance"]["agent_id"] == "retired-bot"


def test_legacy_records_without_provenance_stay_readable(world, tmp_path):  # §7.5
    import json

    from mak4i.models import Artifact

    legacy = {
        "artifact_id": "legacy-1", "artifact_type": "t", "organization_id": "o", "project": "p",
        "title": "t", "content": "c", "status": "active", "version": "1.0", "created_by": "prn_x",
        "created_at": "2026-09-01T00:00:00+00:00", "updated_at": "2026-09-01T00:00:00+00:00",
        "lineage_id": "legacy-1", "tags": [],
    }
    artifact = Artifact.model_validate(legacy)
    assert artifact.provenance is None
    from mak4i.models import dump_for_storage

    assert "provenance" not in json.loads(dump_for_storage(artifact))


def test_principal_model_exposes_principal_type():
    p = Principal(organization_id="org", type="agent", display_name="A", agent_id="a-1")
    assert p.principal_type == "agent"


def test_cli_principal_agent_lifecycle(tmp_path, monkeypatch, capsys):
    import json

    from mak4i import cli
    from mak4i.identity import ControlPlane
    from mak4i.identity.sql_store import SqlControlPlaneStore

    db = tmp_path / "cli.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{db}")
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", "1")
    monkeypatch.setenv("MAK4I_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    cp = ControlPlane(SqlControlPlaneStore(f"sqlite:///{db}", create_tables=True))
    org, owner = cp.onboard_organization(organization_name="CLI", owner_display_name="Owner")
    _cred, raw = cp.issue_credential(actor=owner, principal_id=owner.principal_id)
    monkeypatch.setenv("MAK4I_TOKEN", raw)
    base = ["principal", "create", "--organization-id", org.organization_id, "--type", "agent", "--display-name", "Bot"]

    assert cli.main(base) != 0
    assert "agent_id" in capsys.readouterr().err
    assert cli.main(base + ["--agent-id", "ci-bot"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["principal_type"] == "agent" and created["agent_id"] == "ci-bot"
    assert cli.main(base + ["--agent-id", "ci-bot"]) != 0
    assert "already used" in capsys.readouterr().err

    pid = created["principal_id"]
    assert cli.main(["principal", "rename", "--principal-id", pid, "--display-name", "CI bot"]) == 0
    assert json.loads(capsys.readouterr().out)["agent_id"] == "ci-bot"
    assert cli.main(["principal", "deactivate", "--principal-id", pid]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["principal"]["status"] == "deactivated" and out["oauth_authorizations_revoked"] == 0
