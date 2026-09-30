"""Real end-to-end Streamable HTTP test (requirements §36).

`test_mcp_http_transport.py` covers the HTTP layer thoroughly, but
through Starlette's in-process `TestClient` — no real socket, no real
subprocess. This file runs the actual `python -m mak4i.mcp_server` entry
point as a real OS process, listening on a real loopback port, and drives
it with nothing but the standard library's `urllib` — deliberately no new
test dependency for this.

Fully local (SQLite control plane, `LocalJSONStore`, loopback bind) — no
external infrastructure, so unlike `test_gcs_store_integration.py` this
runs in the normal suite with no opt-in env var required.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mak4i.identity import ControlPlane
from mak4i.identity.sql_store import SqlControlPlaneStore

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_reachable(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            return
        except Exception as exc:  # noqa: BLE001 - this is a readiness poll, any failure just retries
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"server at {url} never became reachable: {last_error}")


def _request(url: str, *, method: str = "GET", token: str | None = None, body: dict | None = None,
             session_id: str | None = None) -> tuple[int, dict[str, str], str]:
    """A minimal real HTTP request. Returns (status, headers, text) —
    `urllib` raises `HTTPError` for non-2xx, so both paths are folded into
    one return shape here rather than forcing every caller to catch it."""
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read().decode()


def _rpc_message(text: str) -> dict:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no JSON-RPC message found in body: {text!r}")


@pytest.fixture
def e2e_server(tmp_path):
    """Bootstraps a real control-plane database and starts the real
    server entry point against it as a subprocess, exactly as a container
    (or a developer running `mak4i serve --transport http`) would."""
    db_url = f"sqlite:///{tmp_path / 'control-plane.db'}"
    store_dir = tmp_path / "artifacts"
    port = _free_port()

    # Bootstrapped through the real ControlPlane API — not a shortcut
    # around it — against the same SQLite file the subprocess will open.
    control_plane = ControlPlane(SqlControlPlaneStore(db_url, create_tables=True))
    org, owner = control_plane.onboard_organization(
        organization_name="E2E Org", owner_display_name="E2E Owner"
    )
    project = control_plane.create_project(
        actor=owner, organization_id=org.organization_id, name="E2E Project"
    )
    control_plane.grant(
        actor=owner,
        principal_id=owner.principal_id,
        project_id=project.project_id,
        permissions=["read", "write"],
    )
    credential, raw_token = control_plane.issue_credential(actor=owner, principal_id=owner.principal_id)

    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env.update(
        {
            # MAK4I_TRANSPORT=http set directly via the environment, with
            # no CLI involved — the container/§6 shape, not `mak4i serve`.
            "MAK4I_TRANSPORT": "http",
            "MAK4I_HOST": "127.0.0.1",
            "MAK4I_PORT": str(port),
            "MAK4I_STORE": "local",
            "MAK4I_LOCAL_STORE_DIR": str(store_dir),
            "MAK4I_CONTROL_PLANE_DB": db_url,
        }
    )

    proc = subprocess.Popen(
        [sys.executable, "-m", "mak4i.mcp_server"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_reachable(f"{base_url}/health")
        yield {
            "base_url": base_url,
            "project": project.project_id,
            "raw_token": raw_token,
            "control_plane": control_plane,
            "owner": owner,
            "credential_id": credential.credential_id,
            "proc": proc,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_real_streamable_http_process_end_to_end(e2e_server):
    """The full required flow (requirements §36): start -> /health ->
    /ready -> authenticate -> tools/list -> authorized operation ->
    revoke credential -> retry -> verify rejection — against a real
    process over a real socket, not an in-process test client."""
    base_url = e2e_server["base_url"]
    raw_token = e2e_server["raw_token"]
    project = e2e_server["project"]

    status, _, body = _request(f"{base_url}/health")
    assert (status, body) == (200, "ok")

    status, _, body = _request(f"{base_url}/ready")
    assert (status, body) == (200, "ready")

    # authenticate: initialize handshake with the real issued credential
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "0.1"},
        },
    }
    status, headers, body = _request(f"{base_url}/mcp", method="POST", token=raw_token, body=init_payload)
    assert status == 200, body
    session_id = headers["mcp-session-id"]

    status, _, _ = _request(
        f"{base_url}/mcp",
        method="POST",
        token=raw_token,
        session_id=session_id,
        body={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert status == 202

    # tools/list: MCP discovery over the real transport
    status, _, body = _request(
        f"{base_url}/mcp",
        method="POST",
        token=raw_token,
        session_id=session_id,
        body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert status == 200, body
    tool_names = {tool["name"] for tool in _rpc_message(body)["result"]["tools"]}
    assert tool_names == {
        "mak4i_list_projects",
        "mak4i_whoami",
        "mak4i_search",
        "mak4i_get_current",
        "mak4i_create",
        "mak4i_supersede",
        "mak4i_history",
    }

    # an authorized MAK4I operation: create, then read it back
    status, _, body = _request(
        f"{base_url}/mcp",
        method="POST",
        token=raw_token,
        session_id=session_id,
        body={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "mak4i_create",
                "arguments": {
                    "project": project,
                    "artifact_id": "decision-e2e-001",
                    "artifact_type": "architecture_decision",
                    "title": "E2E decision",
                    "content": "Created by the real end-to-end HTTP test.",
                },
            },
        },
    )
    assert status == 200, body
    assert _rpc_message(body)["result"]["isError"] is False

    status, _, body = _request(
        f"{base_url}/mcp",
        method="POST",
        token=raw_token,
        session_id=session_id,
        body={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "mak4i_get_current", "arguments": {"project": project}},
        },
    )
    assert status == 200, body
    result = _rpc_message(body)["result"]
    assert result["isError"] is False
    assert [a["artifact_id"] for a in result["structuredContent"]["artifacts"]] == ["decision-e2e-001"]

    # revoke the credential directly against the same control-plane DB
    e2e_server["control_plane"].revoke_credential(
        actor=e2e_server["owner"], credential_id=e2e_server["credential_id"]
    )

    # retry with the now-revoked token -> must be rejected
    status, _, body = _request(f"{base_url}/mcp", method="POST", token=raw_token, body=init_payload)
    assert status == 401, body

    # the server process's own stdout/stderr must never have printed the
    # raw credential (requirements §12)
    proc = e2e_server["proc"]
    proc.terminate()
    output = proc.communicate(timeout=5)[0] or ""
    assert raw_token not in output
