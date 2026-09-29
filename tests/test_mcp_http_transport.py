import json

import pytest
from starlette.testclient import TestClient

from mak4i.api import MAK4IEngine
from mak4i.identity import Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.mcp_server import build_http_app, build_server
from mak4i.store.local_json import LocalJSONStore


@pytest.fixture
def control_plane():
    return ControlPlane(InMemoryControlPlaneStore())


@pytest.fixture
def identity(control_plane):
    """(owner, credential, raw_token) for a single principal, so a test
    that needs to revoke the credential doesn't have to re-derive the
    owner from the store."""
    _org, owner = control_plane.onboard_organization(
        organization_name="WD Technology Solutions", owner_display_name="Owner"
    )
    credential, raw = control_plane.issue_credential(actor=owner, principal_id=owner.principal_id)
    return owner, credential, raw


@pytest.fixture
def raw_token(identity):
    return identity[2]


@pytest.fixture
def app(tmp_path, control_plane):
    engine = MAK4IEngine(
        LocalJSONStore(tmp_path / "artifacts"),
        authorizer=Authorizer(control_plane),
        control_plane=control_plane,
    )
    server = build_server(engine)
    return build_http_app(server, control_plane=control_plane)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _initialize_payload():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1"},
        },
    }


def test_health_endpoint_requires_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.text == "ok"


def test_mcp_endpoint_rejects_missing_auth(client):
    response = client.post(
        "/mcp",
        json=_initialize_payload(),
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 401


def test_mcp_endpoint_rejects_wrong_auth(client):
    response = client.post(
        "/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer the-wrong-token",
        },
    )
    assert response.status_code == 401


def test_mcp_endpoint_accepts_a_valid_credential(client, raw_token):
    response = client.post(
        "/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {raw_token}",
        },
    )
    assert response.status_code == 200
    assert "serverInfo" in response.text


def test_mcp_endpoint_rejects_a_revoked_credential(client, control_plane, identity):
    owner, credential, raw_token = identity
    control_plane.revoke_credential(actor=owner, credential_id=credential.credential_id)

    response = client.post(
        "/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {raw_token}",
        },
    )
    assert response.status_code == 401


def test_mcp_endpoint_does_not_421_on_a_non_localhost_host_header(client, raw_token):
    """Regression check for the DNS-rebinding-protection default, which
    would otherwise reject any Host header not on an allowlist (421
    Misdirected Request) — including the real Cloud Run hostname."""
    response = client.post(
        "/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {raw_token}",
            "Host": "mak4i-mcp-server.example.run.app",
        },
    )
    assert response.status_code != 421
    assert response.status_code == 200


# -- loopback-bound app: DNS-rebinding protection enabled (requirements §9/§15) --


@pytest.fixture
def loopback_app(tmp_path, control_plane):
    engine = MAK4IEngine(
        LocalJSONStore(tmp_path / "artifacts"),
        authorizer=Authorizer(control_plane),
        control_plane=control_plane,
    )
    server = build_server(engine)
    return build_http_app(server, control_plane=control_plane, host="127.0.0.1")


@pytest.fixture
def loopback_client(loopback_app):
    with TestClient(loopback_app) as c:
        yield c


def test_loopback_bind_rejects_a_spoofed_host_header(loopback_client, raw_token):
    """The inverse of the test above: a local `mak4i serve --transport
    http` (no --host given, so it binds to 127.0.0.1) is exactly the
    shape DNS-rebinding protection exists to protect — it must stay
    enabled there, unlike the enterprise/container default above."""
    response = loopback_client.post(
        "/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {raw_token}",
            "Host": "evil.example.com",
        },
    )
    assert response.status_code == 421


def test_loopback_bind_accepts_a_localhost_host_header(loopback_client, raw_token):
    """DNS-rebinding protection enabled ≠ broken: a genuine loopback Host
    header must still work, or local HTTP testing (requirements §13)
    would be unusable."""
    response = loopback_client.post(
        "/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {raw_token}",
            "Host": "127.0.0.1:8000",
        },
    )
    assert response.status_code == 200


# -- /ready (requirements §11/§16) --------------------------------------


def test_ready_endpoint_requires_no_auth_and_reports_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.text == "ready"


def test_ready_endpoint_reports_not_ready_when_the_backend_is_unreachable(client, control_plane, monkeypatch):
    monkeypatch.setattr(control_plane, "is_reachable", lambda: False)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.text == "not ready"


def test_ready_endpoint_exposes_no_configuration_or_secrets(client, raw_token):
    """§11/§16: readiness must not leak the database URL, credentials, or
    any other configuration — bare status text only."""
    response = client.get("/ready")
    assert raw_token not in response.text
    assert "://" not in response.text  # no connection string of any kind
    assert response.text in ("ready", "not ready")


# -- MCP discovery and an authorized tool call over real HTTP (requirements §14) --


def _parse_rpc_message(response) -> dict:
    """Pull the one JSON-RPC message out of a Streamable HTTP response
    body (`event: message\\ndata: {...}`)."""
    for line in response.text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no JSON-RPC message found in response body: {response.text!r}")


def _initialize_session(client, raw_token: str) -> str:
    """Full initialize handshake (initialize + notifications/initialized)
    against a real HTTP client, returning the negotiated session id every
    subsequent call in the test must carry."""
    response = client.post(
        "/mcp",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {raw_token}",
        },
    )
    assert response.status_code == 200, response.text
    session_id = response.headers["mcp-session-id"]

    ack = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {raw_token}",
            "mcp-session-id": session_id,
        },
    )
    assert ack.status_code == 202, ack.text
    return session_id


def _rpc(client, raw_token: str, session_id: str, method: str, params: dict, *, id: int = 2) -> dict:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": id, "method": method, "params": params},
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {raw_token}",
            "mcp-session-id": session_id,
        },
    )
    assert response.status_code == 200, response.text
    return _parse_rpc_message(response)


def _call_tool(client, raw_token: str, session_id: str, name: str, arguments: dict, *, id: int = 2) -> dict:
    """`tools/call` result. A denied call is still HTTP 200 with
    `isError: true` in the JSON-RPC result (never a bare exception) —
    callers assert on this shape, not on a raised error."""
    return _rpc(client, raw_token, session_id, "tools/call", {"name": name, "arguments": arguments}, id=id)


def test_tools_list_over_http_returns_the_registered_mak4i_tools(client, raw_token):
    session_id = _initialize_session(client, raw_token)
    result = _rpc(client, raw_token, session_id, "tools/list", {})
    names = {tool["name"] for tool in result["result"]["tools"]}
    assert names == {
        "mak4i_list_projects",
        "mak4i_whoami",
        "mak4i_search",
        "mak4i_get_current",
        "mak4i_create",
        "mak4i_supersede",
        "mak4i_history",
    }


@pytest.fixture
def two_org_world(control_plane):
    """Two organizations, each with one principal holding a credential and
    a grant only within its own org — the same isolation shape
    `test_security_dev_preview.py` uses at the engine layer, exercised
    here over real HTTP. Two distinct artifact types
    (`architecture_decision`, `documentation_fact`) are used across the
    fixture so cross-project/cross-org coverage isn't tied to one demo
    technology (CLAUDE.md's testing discipline)."""
    wd_org, wd_owner = control_plane.onboard_organization(
        organization_name="WD Technology Solutions", owner_display_name="WD Owner"
    )
    talvik_org, talvik_owner = control_plane.onboard_organization(
        organization_name="Talvik", owner_display_name="Talvik Owner"
    )

    schedovia = control_plane.create_project(
        actor=wd_owner, organization_id=wd_org.organization_id, name="Schedovia"
    )
    sheetcraft = control_plane.create_project(
        actor=wd_owner, organization_id=wd_org.organization_id, name="SheetCraft"
    )
    mak4i_project = control_plane.create_project(
        actor=talvik_owner, organization_id=talvik_org.organization_id, name="ProtocolSpec"
    )

    principal_wd = control_plane.create_principal(
        actor=wd_owner, organization_id=wd_org.organization_id, type="human", display_name="Principal WD"
    )
    principal_talvik = control_plane.create_principal(
        actor=talvik_owner,
        organization_id=talvik_org.organization_id,
        type="human",
        display_name="Principal Talvik",
    )

    # principal_wd is granted Schedovia only — SheetCraft (same org) and
    # MAK4I (a different org entirely) are both unauthorized for it.
    control_plane.grant(
        actor=wd_owner,
        principal_id=principal_wd.principal_id,
        project_id=schedovia.project_id,
        permissions=["read", "write"],
    )
    control_plane.grant(
        actor=talvik_owner,
        principal_id=principal_talvik.principal_id,
        project_id=mak4i_project.project_id,
        permissions=["read", "write"],
    )

    _, raw_wd = control_plane.issue_credential(actor=wd_owner, principal_id=principal_wd.principal_id)
    _, raw_talvik = control_plane.issue_credential(
        actor=talvik_owner, principal_id=principal_talvik.principal_id
    )

    return {
        "schedovia": schedovia.project_id,
        "sheetcraft": sheetcraft.project_id,
        "mak4i_project": mak4i_project.project_id,
        "raw_wd": raw_wd,
        "raw_talvik": raw_talvik,
    }


def test_authorized_tool_call_succeeds_over_http(client, two_org_world):
    """A real authorized MAK4I operation end-to-end over HTTP: create,
    then read back the same artifact via a second tool call — both
    within the credential's granted project."""
    raw_wd = two_org_world["raw_wd"]
    session_id = _initialize_session(client, raw_wd)

    created = _call_tool(
        client,
        raw_wd,
        session_id,
        "mak4i_create",
        {
            "project": two_org_world["schedovia"],
            "artifact_id": "decision-cache-001",
            "artifact_type": "architecture_decision",
            "title": "Cache backend",
            "content": "Use an in-memory cache for the session store.",
        },
        id=3,
    )
    assert created["result"]["isError"] is False

    found = _call_tool(
        client,
        raw_wd,
        session_id,
        "mak4i_get_current",
        {"project": two_org_world["schedovia"]},
        id=4,
    )
    assert found["result"]["isError"] is False
    structured = found["result"]["structuredContent"]
    assert [a["artifact_id"] for a in structured["artifacts"]] == ["decision-cache-001"]


def test_cross_project_access_is_denied_over_http(client, two_org_world):
    """Same organization, unauthorized project — must be denied, not
    silently scoped to an empty result."""
    raw_wd = two_org_world["raw_wd"]
    session_id = _initialize_session(client, raw_wd)

    denied = _call_tool(
        client,
        raw_wd,
        session_id,
        "mak4i_search",
        {"project": two_org_world["sheetcraft"]},
        id=3,
    )
    assert denied["result"]["isError"] is True
    assert "access denied" in denied["result"]["content"][0]["text"]


def test_cross_organization_access_is_denied_over_http(client, two_org_world):
    """A principal from one organization must be denied access to a
    project in a wholly different organization — the artifact under test
    (`mak4i_create`'d by the Talvik principal, `documentation_fact`) is a
    different artifact type than the one used in the same-org test above,
    per CLAUDE.md's requirement to exercise at least two artifact types
    through the same code path."""
    raw_talvik = two_org_world["raw_talvik"]
    talvik_session = _initialize_session(client, raw_talvik)
    _call_tool(
        client,
        raw_talvik,
        talvik_session,
        "mak4i_create",
        {
            "project": two_org_world["mak4i_project"],
            "artifact_id": "doc-onboarding-001",
            "artifact_type": "documentation_fact",
            "title": "Onboarding",
            "content": "New principals are onboarded via `mak4i init`.",
        },
        id=3,
    )

    raw_wd = two_org_world["raw_wd"]
    wd_session = _initialize_session(client, raw_wd)
    denied = _call_tool(
        client,
        raw_wd,
        wd_session,
        "mak4i_get_current",
        {"project": two_org_world["mak4i_project"]},
        id=3,
    )
    assert denied["result"]["isError"] is True
    message = denied["result"]["content"][0]["text"]
    assert "access denied" in message
    # No cross-organization enumeration leakage through the denial text.
    assert "Talvik" not in message
    assert "ProtocolSpec" not in message
    assert two_org_world["mak4i_project"] not in message
