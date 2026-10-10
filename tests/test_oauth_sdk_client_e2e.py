"""End-to-end: the MCP Python SDK's own OAuth client against a real MAK4I
server process with OAuth enabled.

This exercises discovery from the 401 challenge, protected-resource and
authorization-server metadata, registration (dynamic and pre-registered),
PKCE, the RFC 9207 `iss` check, token exchange, authenticated MCP tool
calls and refresh — over real HTTP, as SDK-based MCP clients do. The
browser steps (sign-in and consent) are driven programmatically.

It is a protocol smoke test, not product-client interoperability evidence
(MAK-0008 conformance M16 needs real clients against a reachable HTTPS
deployment).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import pytest

from mak4i.identity import ControlPlane
from mak4i.identity.sql_store import SqlControlPlaneStore
from mak4i.oauth import OAuthService, OAuthSettings
from mak4i.oauth.store import SqlOAuthStore

_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
REDIRECT = "http://127.0.0.1:47123/callback"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(url)


@pytest.fixture
def server(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'cp.db'}"
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    cp = ControlPlane(SqlControlPlaneStore(db_url, create_tables=True))
    org, owner = cp.onboard_organization(organization_name="SDK Org", owner_display_name="SDK Owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P")
    cp.grant(actor=owner, principal_id=owner.principal_id, project_id=project.project_id, permissions=["read", "write"])
    settings = OAuthSettings(resource=base + "/mcp", issuer=base, enable_dcr=True)
    oauth = OAuthService(settings=settings, store=SqlOAuthStore(cp._store.engine), control_plane=cp)

    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env.update(
        {
            "MAK4I_TRANSPORT": "http",
            "MAK4I_HOST": "127.0.0.1",
            "MAK4I_PORT": str(port),
            "MAK4I_STORE": "local",
            "MAK4I_LOCAL_STORE_DIR": str(tmp_path / "artifacts"),
            "MAK4I_CONTROL_PLANE_DB": db_url,
            "MAK4I_PUBLIC_ENDPOINT": base + "/mcp",
            "MAK4I_OAUTH_ENABLED": "1",
            "MAK4I_OAUTH_DCR": "1",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "mak4i.mcp_server"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        _wait(base + "/health")
        yield {"base": base, "oauth": oauth, "owner": owner, "org": org, "project": project, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


class _MemoryStorage:
    def __init__(self, client_info=None):
        self.tokens = None
        self.client_info = client_info

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


async def _run_flow(server, storage) -> dict:
    from mcp import ClientSession
    from mcp.client.auth.oauth2 import OAuthClientProvider
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

    captured: dict = {}

    async def redirect_handler(authorization_url: str) -> None:
        # Play the user's browser: open the page, sign in, approve.
        async with httpx2.AsyncClient(base_url=server["base"], follow_redirects=False) as browser:
            page = await browser.get(authorization_url)
            assert page.status_code == 200, page.text
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
            code, _ = server["oauth"].issue_sign_in_code(
                actor=server["owner"], principal_id=server["owner"].principal_id
            )
            consent = await browser.post("/oauth/sign-in", data={"code": code, "csrf": csrf})
            assert consent.status_code == 200, consent.text
            done = await browser.post("/oauth/consent", data={"csrf": csrf, "decision": "approve"})
            assert done.status_code == 302
            captured["query"] = parse_qs(urlsplit(done.headers["location"]).query)

    async def callback_handler() -> AuthorizationCodeResult:
        q = captured["query"]
        return AuthorizationCodeResult(code=q["code"][0], state=q.get("state", [None])[0], iss=q["iss"][0])

    provider = OAuthClientProvider(
        server_url=server["base"] + "/mcp",
        client_metadata=OAuthClientMetadata(
            client_name="SDK e2e",
            redirect_uris=[REDIRECT],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope="mak4i:read mak4i:write",
        ),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    results: dict = {}
    async with httpx2.AsyncClient(auth=provider, timeout=30) as http:
        async with streamable_http_client(server["base"] + "/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                who = await session.call_tool("mak4i_whoami", {})
                results["whoami"] = who.structured_content
                first_access = storage.tokens.access_token
                # Make the client consider its access token expired: the next
                # request must go through a real refresh at the token endpoint.
                provider.context.token_expiry_time = time.time() - 1
                created = await session.call_tool(
                    "mak4i_create",
                    {
                        "project": server["project"].project_id,
                        "artifact_id": "sdk-e2e-1",
                        "artifact_type": "caching_decision",
                        "title": "Cache",
                        "content": "Use RED",
                    },
                )
                results["created"] = created
                results["rotated"] = storage.tokens.access_token != first_access
    return results


def _assert_results(results, server):
    assert results["whoami"]["auth_method"] == "oauth"
    assert results["whoami"]["principal"]["principal_id"] == server["owner"].principal_id
    assert results["created"].is_error is False
    assert results["rotated"] is True


def test_sdk_client_with_dynamic_registration(server):
    results = anyio.run(_run_flow, server, _MemoryStorage())
    _assert_results(results, server)


def test_sdk_client_with_a_pre_registered_client(server):
    from mcp.shared.auth import OAuthClientInformationFull

    client, _ = server["oauth"].register_client(
        actor=server["owner"],
        organization_id=server["org"].organization_id,
        client_name="Pre-registered SDK client",
        redirect_uris=[REDIRECT],
    )
    info = OAuthClientInformationFull(
        client_id=client["client_id"],
        redirect_uris=[REDIRECT],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="mak4i:read mak4i:write",
    )
    results = anyio.run(_run_flow, server, _MemoryStorage(client_info=info))
    _assert_results(results, server)
