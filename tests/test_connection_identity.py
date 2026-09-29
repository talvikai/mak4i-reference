"""Issues #8/#9: target identity and cross-connection write safety.

The server can't observe or block what an AI client does on a *different*
MCP connection. What it can do — and what these tests pin — is identify
itself precisely (mak4i_whoami, mak4i_list_projects), state in every
denial/failure which connection refused and that the refusal must not be
retried elsewhere, carry the same rule in the write tools' descriptions and
the server instructions, and never itself redirect a write anywhere.

Two MAK4I "connections" are modelled as two independent engines (separate
control planes and stores), each with a project named "Schedovia" — the
RC3 incident's shape. Denials are exercised on two artifact types
(an architecture decision and a documentation fact) through the same path.
"""

from __future__ import annotations

import pytest
from mcp.client import Client

from mak4i.api import MAK4IEngine
from mak4i.identity import Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.mcp_server import (
    _NO_CROSS_CONNECTION_FALLBACK,
    ConnectionIdentity,
    build_server,
    current_principal,
    resolve_connection_identity,
)
from mak4i.store.local_json import LocalJSONStore

ARTIFACTS = [
    {
        "artifact_id": "decision-cache-001",
        "artifact_type": "architecture_decision",
        "title": "Application caching technology",
        "content": "Use an in-process LRU cache.",
    },
    {
        "artifact_id": "doc-onboarding-001",
        "artifact_type": "documentation_fact",
        "title": "Onboarding",
        "content": "New developers start with the local setup guide.",
    },
]


def _connection(tmp_path, *, name, environment, org_name, permissions):
    control_plane = ControlPlane(InMemoryControlPlaneStore())
    org, owner = control_plane.onboard_organization(
        organization_name=org_name, owner_display_name="Owner"
    )
    project = control_plane.create_project(
        actor=owner, organization_id=org.organization_id, name="Schedovia"
    )
    user = control_plane.create_principal(
        actor=owner, organization_id=org.organization_id, type="human", display_name="Dev"
    )
    control_plane.grant(
        actor=owner,
        principal_id=user.principal_id,
        project_id=project.project_id,
        permissions=permissions,
    )
    store = LocalJSONStore(tmp_path / name / "artifacts")
    engine = MAK4IEngine(store, authorizer=Authorizer(control_plane), control_plane=control_plane)
    identity = ConnectionIdentity(
        instance_name=name, environment=environment, server_version="test"
    )
    return {
        "server": build_server(engine, connection=identity),
        "engine": engine,
        "store": store,
        "org": org,
        "project": project.project_id,
        "user": user,
        "owner": owner,
        "identity": identity,
    }


@pytest.fixture
def enterprise(tmp_path):
    """Read-only on its Schedovia — the connection that denies."""
    return _connection(
        tmp_path, name="MAK4I-Enterprise", environment="enterprise",
        org_name="Acme Enterprise", permissions=["read"],
    )


@pytest.fixture
def local(tmp_path):
    """Read/write on a *different* Schedovia — the tempting fallback."""
    return _connection(
        tmp_path, name="MAK4I local", environment="local",
        org_name="Acme Local", permissions=["read", "write"],
    )


def _acting_as(principal):
    return current_principal.set(principal)


async def test_whoami_identifies_the_connection_organization_and_principal(enterprise):
    token = _acting_as(enterprise["user"])
    try:
        async with Client(server=enterprise["server"]) as client:
            result = await client.call_tool("mak4i_whoami", {})
    finally:
        current_principal.reset(token)
    assert not result.is_error
    who = result.structured_content
    assert who["connection"]["instance_name"] == "MAK4I-Enterprise"
    assert who["connection"]["environment"] == "enterprise"
    assert who["organization"] == {
        "organization_id": enterprise["org"].organization_id,
        "name": "Acme Enterprise",
    }
    assert who["principal"]["principal_id"] == enterprise["user"].principal_id
    assert who["principal"]["role"] == "member"


async def test_list_projects_carries_connection_environment_and_organization(enterprise, local):
    for conn in (enterprise, local):
        token = _acting_as(conn["user"])
        try:
            async with Client(server=conn["server"]) as client:
                result = await client.call_tool("mak4i_list_projects", {})
        finally:
            current_principal.reset(token)
        (project,) = result.structured_content["result"]
        assert project["name"] == "Schedovia"
        assert project["project_id"] == conn["project"]
        assert project["organization_id"] == conn["org"].organization_id
        assert project["organization_name"] == conn["org"].name
        assert project["connection"] == conn["identity"].instance_name
        assert project["environment"] == conn["identity"].environment
    # Same name, different identity: the fields that tell them apart differ.
    assert enterprise["project"] != local["project"]


@pytest.mark.parametrize("artifact", ARTIFACTS, ids=lambda a: a["artifact_type"])
async def test_denied_write_names_the_connection_and_forbids_fallback(enterprise, local, artifact):
    token = _acting_as(enterprise["user"])
    try:
        async with Client(server=enterprise["server"]) as client:
            result = await client.call_tool(
                "mak4i_create", {"project": enterprise["project"], **artifact}
            )
    finally:
        current_principal.reset(token)

    assert result.is_error
    message = result.content[0].text
    assert "access denied" in message
    assert "no write permission" in message
    assert "'MAK4I-Enterprise'" in message and "environment: enterprise" in message
    assert "Do not retry this operation on another MAK4I connection" in message
    # Never echoes the project (no existence/enumeration leak).
    assert enterprise["project"] not in message
    assert "Schedovia" not in message

    # Nothing was written on the denying connection...
    assert enterprise["store"].all(
        organization_id=enterprise["org"].organization_id, project=enterprise["project"]
    ) == []
    # ...and the server never redirected the write to the other connection.
    assert local["store"].all(
        organization_id=local["org"].organization_id, project=local["project"]
    ) == []


async def test_denied_supersede_is_final_too(enterprise):
    # Seed an artifact as the owner (write via a temporary owner grant).
    cp = enterprise["engine"]._control_plane
    cp.grant(
        actor=enterprise["owner"],
        principal_id=enterprise["owner"].principal_id,
        project_id=enterprise["project"],
        permissions=["read", "write"],
    )
    enterprise["engine"].create_artifact(
        principal=enterprise["owner"], project=enterprise["project"], **ARTIFACTS[0]
    )
    token = _acting_as(enterprise["user"])
    try:
        async with Client(server=enterprise["server"]) as client:
            result = await client.call_tool(
                "mak4i_supersede",
                {
                    "project": enterprise["project"],
                    "old_id": ARTIFACTS[0]["artifact_id"],
                    "content": "Use Redis.",
                    "reason": "attempted by a read-only principal",
                },
            )
    finally:
        current_principal.reset(token)
    assert result.is_error
    assert "This denial is final for this connection" in result.content[0].text
    history = enterprise["store"].all(
        organization_id=enterprise["org"].organization_id, project=enterprise["project"]
    )
    assert [a.status for a in history] == ["active"]


async def test_failed_write_reports_its_connection_and_that_nothing_was_written(local):
    token = _acting_as(local["user"])
    try:
        async with Client(server=local["server"]) as client:
            payload = {"project": local["project"], **ARTIFACTS[1]}
            assert not (await client.call_tool("mak4i_create", payload)).is_error
            result = await client.call_tool("mak4i_create", payload)
    finally:
        current_principal.reset(token)
    assert result.is_error
    message = result.content[0].text
    assert "already exists" in message
    assert "'MAK4I local'" in message and "nothing was written" in message
    assert "Do not retry this write on another MAK4I connection" in message


async def test_write_tools_and_server_instructions_carry_the_no_fallback_rule(enterprise):
    token = _acting_as(enterprise["user"])
    try:
        async with Client(server=enterprise["server"]) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            instructions = client.instructions
    finally:
        current_principal.reset(token)
    assert _NO_CROSS_CONNECTION_FALLBACK in tools["mak4i_create"].description
    assert _NO_CROSS_CONNECTION_FALLBACK in tools["mak4i_supersede"].description
    assert "mak4i_whoami" in tools
    assert "never by name alone" in instructions
    assert "Never retry a denied or failed write on a different MAK4I connection" in instructions


async def test_read_denial_names_the_connection(enterprise, local):
    # The local principal asks the enterprise server about a project it has
    # no grant on — the same denial wording, still without echoing it.
    token = _acting_as(local["user"])
    try:
        async with Client(server=enterprise["server"]) as client:
            result = await client.call_tool("mak4i_get_current", {"project": enterprise["project"]})
    finally:
        current_principal.reset(token)
    assert result.is_error
    message = result.content[0].text
    assert "access denied" in message and "'MAK4I-Enterprise'" in message
    assert enterprise["project"] not in message


def test_connection_identity_comes_from_operator_configuration(monkeypatch):
    monkeypatch.setenv("MAK4I_INSTANCE_NAME", "Acme MAK4I")
    monkeypatch.setenv("MAK4I_ENVIRONMENT", "production")
    monkeypatch.setenv("MAK4I_PUBLIC_ENDPOINT", "https://mak4i.example.com/mcp")
    identity = resolve_connection_identity()
    assert identity.instance_name == "Acme MAK4I"
    assert identity.environment == "production"
    assert identity.public_endpoint == "https://mak4i.example.com/mcp"

    for key in ("MAK4I_INSTANCE_NAME", "MAK4I_ENVIRONMENT", "MAK4I_PUBLIC_ENDPOINT"):
        monkeypatch.delenv(key)
    identity = resolve_connection_identity()
    assert (identity.instance_name, identity.environment, identity.public_endpoint) == (
        "MAK4I",
        "unspecified",
        None,
    )
