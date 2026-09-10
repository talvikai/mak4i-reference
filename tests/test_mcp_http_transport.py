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
