"""OAuth authorization service and resource-server tests (MAK-0008).

Each test names the CONFORMANCE.md acceptance criterion it covers (M1–M15).
Real-client interoperability (M16) needs a reachable HTTPS deployment and is
recorded separately.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine
from starlette.testclient import TestClient

from mak4i.api import MAK4IEngine
from mak4i.audit import AuditLogger
from mak4i.identity import Authorizer, ControlPlane
from mak4i.identity.sql_store import SqlControlPlaneStore
from mak4i.mcp_server import build_http_app, build_server
from mak4i.oauth import OAuthService, OAuthSettings
from mak4i.oauth import cimd
from mak4i.oauth.service import InvalidAccessToken, pkce_s256, redirect_uri_matches
from mak4i.oauth.settings import OAuthConfigError, default_issuer
from mak4i.oauth.store import SqlOAuthStore
from mak4i.store.local_json import LocalJSONStore

ORIGIN = "https://mak4i.example.com"
RESOURCE = ORIGIN + "/mcp"
VERIFIER = "v" * 64
CLAUDE_CODE_CIMD = "https://claude.ai/oauth/claude-code-client-metadata"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _fake_fetch(url: str) -> cimd.FetchedDocument:
    if url == CLAUDE_CODE_CIMD:
        doc = {
            "client_id": CLAUDE_CODE_CIMD,
            "client_name": "Claude Code",
            "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        return cimd.FetchedDocument(body=json.dumps(doc).encode(), max_age=None)
    raise cimd.ClientMetadataError("client metadata document returned HTTP 404")


class World:
    def __init__(self, tmp_path, **settings_overrides):
        self.clock = Clock()
        self.db = create_engine(f"sqlite:///{tmp_path / 'cp.db'}", future=True)
        self.cp = ControlPlane(SqlControlPlaneStore(self.db, create_tables=True), clock=self.clock)
        self.org, self.owner = self.cp.onboard_organization(
            organization_name="Acme", owner_display_name="Owner"
        )
        self.project = self.cp.create_project(
            actor=self.owner, organization_id=self.org.organization_id, name="Platform"
        )
        self.cp.grant(
            actor=self.owner,
            principal_id=self.owner.principal_id,
            project_id=self.project.project_id,
            permissions=["read", "write"],
        )
        settings = dict(resource=RESOURCE, issuer=ORIGIN)
        settings.update(settings_overrides)
        self.settings = OAuthSettings(**settings)
        self.oauth = self.make_service()
        engine = MAK4IEngine(
            LocalJSONStore(tmp_path / "artifacts"),
            authorizer=Authorizer(self.cp),
            control_plane=self.cp,
        )
        self.app = build_http_app(
            build_server(engine), control_plane=self.cp, oauth=self.oauth, audit=AuditLogger()
        )
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.public_client, _ = self.oauth.register_client(
            actor=self.owner,
            organization_id=self.org.organization_id,
            client_name="Test client",
            redirect_uris=["http://127.0.0.1/callback", "https://app.example.net/cb"],
        )

    def make_service(self) -> OAuthService:
        return OAuthService(
            settings=self.settings,
            store=SqlOAuthStore(self.db),
            control_plane=self.cp,
            audit=AuditLogger(),
            clock=self.clock,
            fetch_client_document=_fake_fetch,
        )

    # -- browser flow --------------------------------------------------------------

    def authorize(self, **overrides):
        params = {
            "response_type": "code",
            "client_id": self.public_client["client_id"],
            "redirect_uri": "http://127.0.0.1:53111/callback",
            "code_challenge": pkce_s256(VERIFIER),
            "code_challenge_method": "S256",
            "resource": RESOURCE,
            "state": "st-1",
            "scope": "mak4i:read mak4i:write",
        }
        params.update(overrides)
        params = {k: v for k, v in params.items() if v is not None}
        return self.client.get("/oauth/authorize", params=params, follow_redirects=False)

    def sign_in(self, page, principal_id=None, code=None):
        csrf = _csrf(page.text)
        if code is None:
            code, _ = self.oauth.issue_sign_in_code(
                actor=self.owner, principal_id=principal_id or self.owner.principal_id
            )
        return self.client.post("/oauth/sign-in", data={"code": code, "csrf": csrf})

    def consent(self, page, decision="approve"):
        return self.client.post(
            "/oauth/consent",
            data={"csrf": _csrf(page.text), "decision": decision},
            follow_redirects=False,
        )

    def obtain_code(self, principal_id=None, **authorize_overrides) -> tuple[str, dict]:
        page = self.authorize(**authorize_overrides)
        assert page.status_code == 200, page.text
        consent = self.sign_in(page, principal_id=principal_id)
        assert consent.status_code == 200, consent.text
        redirect = self.consent(consent)
        assert redirect.status_code == 302
        query = parse_qs(urlsplit(redirect.headers["location"]).query)
        return query["code"][0], query

    def exchange(self, code, **overrides):
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:53111/callback",
            "code_verifier": VERIFIER,
            "client_id": self.public_client["client_id"],
            "resource": RESOURCE,
        }
        data.update(overrides)
        return self.client.post("/oauth/token", data={k: v for k, v in data.items() if v is not None})

    def tokens(self, principal_id=None, scope="mak4i:read mak4i:write") -> dict:
        code, _ = self.obtain_code(principal_id=principal_id, scope=scope)
        response = self.exchange(code)
        assert response.status_code == 200, response.text
        return response.json()

    def refresh(self, refresh_token, **overrides):
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.public_client["client_id"],
        }
        data.update(overrides)
        return self.client.post("/oauth/token", data=data)

    # -- MCP ---------------------------------------------------------------------------

    def mcp(self, token, method, params=None, *, session=None, request_id=1, extra_headers=None):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if session:
            headers["mcp-session-id"] = session
        if extra_headers:
            headers.update(extra_headers)
        body = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            body["id"] = request_id
        if params is not None:
            body["params"] = params
        return self.client.post("/mcp", content=json.dumps(body), headers=headers)

    def session(self, token) -> str:
        response = self.mcp(
            token,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        )
        assert response.status_code == 200, response.text
        session = response.headers["mcp-session-id"]
        assert self.mcp(token, "notifications/initialized", session=session, request_id=None).status_code == 202
        return session

    def call_tool(self, token, name, arguments=None, session=None):
        session = session or self.session(token)
        response = self.mcp(
            token, "tools/call", {"name": name, "arguments": arguments or {}}, session=session, request_id=2
        )
        return response, (_rpc(response.text) if response.status_code == 200 else None)


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def _rpc(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    return json.loads(text)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    with w.client:
        yield w


# -- M1, M2: discovery ---------------------------------------------------------------


def test_unauthenticated_mcp_request_gets_resource_metadata_challenge(world):  # M1
    response = world.client.post("/mcp", json={})
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/mcp"' in challenge
    assert 'scope="mak4i:read mak4i:write"' in challenge
    assert "error=" not in challenge


def test_invalid_token_challenge_says_invalid_token(world):  # M1/M7
    response = world.mcp("mak4at_not-a-real-token", "initialize", {})
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_metadata_documents_are_public_and_consistent(world):  # M2
    prm = world.client.get("/.well-known/oauth-protected-resource/mcp")
    assert prm.status_code == 200
    assert prm.json() == {
        "resource": RESOURCE,
        "authorization_servers": [ORIGIN],
        "scopes_supported": ["mak4i:read", "mak4i:write", "mak4i:resolve"],
        "bearer_methods_supported": ["header"],
        "resource_name": "MAK4I",
    }
    assert world.client.get("/.well-known/oauth-protected-resource").json() == prm.json()
    asm = world.client.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == ORIGIN
    assert asm["authorization_endpoint"] == ORIGIN + "/oauth/authorize"
    assert asm["token_endpoint"] == ORIGIN + "/oauth/token"
    assert asm["revocation_endpoint"] == ORIGIN + "/oauth/revoke"
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert "none" in asm["token_endpoint_auth_methods_supported"]
    assert asm["authorization_response_iss_parameter_supported"] is True
    assert asm["client_id_metadata_document_supported"] is True
    assert "registration_endpoint" not in asm  # M12: DCR disabled by default
    assert "offline_access" not in asm["scopes_supported"]


def test_metadata_urls_follow_a_configured_path_prefix(tmp_path):  # M2
    w = World(
        tmp_path,
        resource="https://mak4i.example.com/team-a/mcp",
        issuer="https://mak4i.example.com/team-a",
    )
    with w.client:
        prm = w.client.get("/.well-known/oauth-protected-resource/team-a/mcp")
        assert prm.status_code == 200
        assert prm.json()["resource"] == "https://mak4i.example.com/team-a/mcp"
        assert prm.json()["authorization_servers"] == ["https://mak4i.example.com/team-a"]
        asm = w.client.get("/.well-known/oauth-authorization-server/team-a").json()
        assert asm["issuer"] == "https://mak4i.example.com/team-a"
        assert asm["token_endpoint"] == "https://mak4i.example.com/team-a/oauth/token"
        challenge = w.client.post("/mcp", json={}).headers["www-authenticate"]
        assert (
            'resource_metadata="https://mak4i.example.com/.well-known/oauth-protected-resource/team-a/mcp"'
            in challenge
        )


def test_metadata_validates_against_the_protocol_schemas(world):  # M2 / M15
    jsonschema = pytest.importorskip("jsonschema")
    asm_schema = {
        "type": "object",
        "required": [
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "revocation_endpoint",
            "response_types_supported",
            "grant_types_supported",
            "code_challenge_methods_supported",
            "token_endpoint_auth_methods_supported",
            "scopes_supported",
            "authorization_response_iss_parameter_supported",
        ],
    }
    jsonschema.validate(world.client.get("/.well-known/oauth-authorization-server").json(), asm_schema)


# -- the full flow -------------------------------------------------------------------


def test_full_authorization_code_flow_reaches_mcp_tools(world):  # M3
    code, query = world.obtain_code()
    assert query["state"] == ["st-1"]
    assert query["iss"] == [ORIGIN]
    response = world.exchange(code)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "mak4i:read mak4i:write"
    assert body["access_token"].startswith("mak4at_")
    assert body["refresh_token"].startswith("mak4rt_")

    http, rpc = world.call_tool(body["access_token"], "mak4i_whoami")
    assert http.status_code == 200
    who = rpc["result"]["structuredContent"]
    assert who["principal"]["principal_id"] == world.owner.principal_id
    assert who["auth_method"] == "oauth"
    assert who["scopes"] == ["mak4i:read", "mak4i:write"]

    http, rpc = world.call_tool(
        body["access_token"],
        "mak4i_create",
        {
            "project": world.project.project_id,
            "artifact_id": "cache-choice",
            "artifact_type": "caching_decision",
            "title": "Cache",
            "content": "Use RED",
        },
    )
    assert rpc["result"]["isError"] is False
    assert rpc["result"]["structuredContent"]["created_by"] == world.owner.principal_id


def test_cimd_client_with_loopback_any_port(world):  # M4 / M12
    page = world.authorize(client_id=CLAUDE_CODE_CIMD, redirect_uri="http://localhost:61234/callback")
    assert page.status_code == 200
    consent = world.sign_in(page)
    assert "Claude Code" in consent.text
    assert "unverified" in consent.text
    redirect = world.consent(consent)
    assert redirect.headers["location"].startswith("http://localhost:61234/callback?")


def test_consent_page_names_principal_client_host_and_scopes(world):  # M10
    page = world.authorize(redirect_uri="https://app.example.net/cb", scope="mak4i:read")
    consent = world.sign_in(page)
    assert "Owner" in consent.text
    assert "Test client" in consent.text
    assert "unverified" not in consent.text  # pre-registered by an owner
    assert "app.example.net" in consent.text
    assert "Read project knowledge" in consent.text
    assert "Create and supersede" not in consent.text


def test_consent_denied_redirects_with_access_denied(world):  # M10
    consent = world.sign_in(world.authorize())
    redirect = world.consent(consent, decision="deny")
    query = parse_qs(urlsplit(redirect.headers["location"]).query)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["st-1"]


def test_csrf_is_enforced_on_sign_in(world):  # M10
    page = world.authorize()
    code, _ = world.oauth.issue_sign_in_code(actor=world.owner, principal_id=world.owner.principal_id)
    response = world.client.post("/oauth/sign-in", data={"code": code, "csrf": "forged"})
    assert response.status_code == 400, response.text
    assert "could not be verified" in response.text, response.text  # observed failing once under heavy load
    assert page.status_code == 200


def test_pages_forbid_framing_and_caching(world):  # M10
    page = world.authorize()
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    cookie = page.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()


# -- M3, M4, M6: authorization request validation ---------------------------------


def test_pkce_is_required_and_s256_only(world):  # M3
    for overrides in ({"code_challenge": None}, {"code_challenge_method": "plain"}):
        response = world.authorize(**overrides)
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["error"] == ["invalid_request"]
        assert query["iss"] == [ORIGIN]


def test_wrong_pkce_verifier_is_rejected(world):  # M3
    code, _ = world.obtain_code()
    response = world.exchange(code, code_verifier="w" * 64)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_unregistered_redirect_shows_an_error_page_and_never_redirects(world):  # M4
    for redirect in ("https://evil.example/cb", "http://127.0.0.1:5000/other", "https://app.example.net/cb/x"):
        response = world.authorize(redirect_uri=redirect)
        assert response.status_code == 400
        assert "location" not in response.headers


def test_unknown_client_shows_an_error_page(world):  # M4
    response = world.authorize(client_id="mak4c_unknown")
    assert response.status_code == 400 and "location" not in response.headers


def test_redirect_uri_matching_rules():  # M4
    registered = ["http://127.0.0.1/callback", "https://app.example.net/cb"]
    assert redirect_uri_matches("http://127.0.0.1:9999/callback", registered)
    assert redirect_uri_matches("https://app.example.net/cb", registered)
    assert not redirect_uri_matches("https://app.example.net:8443/cb", registered)
    assert not redirect_uri_matches("http://127.0.0.1:9999/callback/x", registered)
    assert not redirect_uri_matches("http://localhost:9999/callback", registered)
    assert not redirect_uri_matches("http://127.0.0.1.evil.com/callback", registered)


def test_wrong_resource_is_invalid_target(world):  # M6
    response = world.authorize(resource="https://other.example.com/mcp")
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["error"] == ["invalid_target"]
    code, _ = world.obtain_code()
    assert world.exchange(code, resource="https://other.example.com/mcp").json()["error"] == "invalid_target"


def test_token_for_another_resource_is_rejected_at_the_mcp_endpoint(world):  # M6
    tokens = world.tokens()
    from mak4i.identity import schema

    with world.db.begin() as conn:
        conn.execute(schema.oauth_authorizations.update().values(resource="https://other.example.com/mcp"))
    assert world.mcp(tokens["access_token"], "initialize", {}).status_code == 401


def test_unsupported_scope_is_rejected(world):
    response = world.authorize(scope="mak4i:read offline_access")
    assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == ["invalid_scope"]


# -- M5: codes ---------------------------------------------------------------------


def test_code_reuse_fails_and_revokes_the_authorization(world):  # M5
    code, _ = world.obtain_code()
    first = world.exchange(code)
    assert first.status_code == 200
    second = world.exchange(code)
    assert second.status_code == 400 and second.json()["error"] == "invalid_grant"
    assert world.mcp(first.json()["access_token"], "initialize", {}).status_code == 401
    assert world.refresh(first.json()["refresh_token"]).json()["error"] == "invalid_grant"


def test_expired_code_is_rejected(world):  # M5
    code, _ = world.obtain_code()
    world.clock.advance(61)
    assert world.exchange(code).json()["error"] == "invalid_grant"


def test_code_issued_to_another_client_is_rejected(world):  # M5
    other, _ = world.oauth.register_client(
        actor=world.owner,
        organization_id=world.org.organization_id,
        client_name="Other",
        redirect_uris=["http://127.0.0.1/callback"],
    )
    code, _ = world.obtain_code()
    assert world.exchange(code, client_id=other["client_id"]).json()["error"] == "invalid_grant"


# -- M7, M8: tokens ----------------------------------------------------------------


def test_expired_access_token_is_rejected_without_fallback(world):  # M7
    tokens = world.tokens()
    world.clock.advance(3601)
    response = world.mcp(tokens["access_token"], "initialize", {})
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_refresh_rotates_and_replay_revokes_the_authorization(world):  # M8
    tokens = world.tokens()
    world.clock.advance(120)
    rotated = world.refresh(tokens["refresh_token"])
    assert rotated.status_code == 200
    new = rotated.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    assert world.mcp(new["access_token"], "initialize", {}).status_code == 200

    replay = world.refresh(tokens["refresh_token"])
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    # The whole authorization is gone, including the legitimately rotated tokens.
    assert world.mcp(new["access_token"], "initialize", {}).status_code == 401
    assert world.refresh(new["refresh_token"]).json()["error"] == "invalid_grant"


def test_refresh_may_narrow_but_not_widen_scope(world):  # M8
    tokens = world.tokens(scope="mak4i:read")
    widened = world.refresh(tokens["refresh_token"], scope="mak4i:read mak4i:write")
    assert widened.json()["error"] == "invalid_scope"


def test_idle_and_absolute_refresh_lifetimes_force_sign_in(world):  # M8
    tokens = world.tokens()
    world.clock.advance(7 * 86400 + 1)
    assert world.refresh(tokens["refresh_token"]).json()["error"] == "invalid_grant"

    tokens = world.tokens()
    for _ in range(6):  # keep it alive (never idle) past the absolute lifetime
        world.clock.advance(6 * 86400)
        response = world.refresh(tokens["refresh_token"])
        if response.status_code != 200:
            break
        tokens = response.json()
    assert response.json()["error"] == "invalid_grant"


def test_revocation_endpoint(world):  # M8 / §6.7
    tokens = world.tokens()
    client_id = world.public_client["client_id"]
    assert world.client.post("/oauth/revoke", data={"token": "unknown", "client_id": client_id}).status_code == 200
    assert world.client.post(
        "/oauth/revoke", data={"token": tokens["refresh_token"], "client_id": client_id}
    ).status_code == 200
    assert world.mcp(tokens["access_token"], "initialize", {}).status_code == 401


# -- MAK-0006 §5.3 / §6.3: ceilings and revocation policy ----------------------------


def test_scope_ceiling_returns_insufficient_scope(world):  # M15 / MAK-0006 I5
    tokens = world.tokens(scope="mak4i:read")
    http, _ = world.call_tool(
        tokens["access_token"],
        "mak4i_create",
        {
            "project": world.project.project_id,
            "artifact_id": "x",
            "artifact_type": "t",
            "title": "t",
            "content": "c",
        },
    )
    assert http.status_code == 403
    challenge = http.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert "mak4i:write" in challenge


def test_scopes_never_widen_a_grant(world):  # MAK-0006 I5
    reader = world.cp.create_principal(
        actor=world.owner, organization_id=world.org.organization_id, type="human", display_name="Reader"
    )
    world.cp.grant(
        actor=world.owner, principal_id=reader.principal_id, project_id=world.project.project_id, permissions=["read"]
    )
    tokens = world.tokens(principal_id=reader.principal_id, scope="mak4i:read mak4i:write")
    _, rpc = world.call_tool(
        tokens["access_token"],
        "mak4i_create",
        {"project": world.project.project_id, "artifact_id": "x", "artifact_type": "t", "title": "t", "content": "c"},
    )
    assert rpc["result"]["isError"] is True
    assert "access denied" in rpc["result"]["content"][0]["text"]
    _, rpc = world.call_tool(tokens["access_token"], "mak4i_list_projects")
    assert rpc["result"]["structuredContent"]["result"][0]["permissions"] == ["read"]


def test_deactivated_principal_loses_access_immediately(world):  # MAK-0006 I6
    member = world.cp.create_principal(
        actor=world.owner, organization_id=world.org.organization_id, type="human", display_name="Member"
    )
    world.cp.grant(
        actor=world.owner, principal_id=member.principal_id, project_id=world.project.project_id, permissions=["read"]
    )
    tokens = world.tokens(principal_id=member.principal_id)
    assert world.mcp(tokens["access_token"], "initialize", {}).status_code == 200
    world.cp.deactivate_principal(actor=world.owner, principal_id=member.principal_id)
    assert world.mcp(tokens["access_token"], "initialize", {}).status_code == 401
    assert world.refresh(tokens["refresh_token"]).json()["error"] == "invalid_grant"


def test_removed_grant_takes_effect_on_the_next_request(world):  # MAK-0006 I6
    tokens = world.tokens()
    session = world.session(tokens["access_token"])
    args = {"project": world.project.project_id}
    _, rpc = world.call_tool(tokens["access_token"], "mak4i_get_current", args, session=session)
    assert rpc["result"]["isError"] is False
    world.cp.revoke_grant(
        actor=world.owner, principal_id=world.owner.principal_id, project_id=world.project.project_id
    )
    _, rpc = world.call_tool(tokens["access_token"], "mak4i_get_current", args, session=session)
    assert rpc["result"]["isError"] is True


def test_admin_revocation_by_principal_and_client(world):  # §8.5
    tokens = world.tokens()
    listed = world.oauth.list_authorizations(actor=world.owner, principal_id=world.owner.principal_id)
    assert len(listed) == 1 and "token" not in json.dumps(listed, default=str).lower().replace("token_", "")
    assert world.oauth.revoke_all(actor=world.owner, principal_id=world.owner.principal_id) == 1
    assert world.mcp(tokens["access_token"], "initialize", {}).status_code == 401

    tokens = world.tokens()
    world.oauth.disable_client(actor=world.owner, client_id=world.public_client["client_id"])
    assert world.mcp(tokens["access_token"], "initialize", {}).status_code == 401
    assert world.authorize().status_code == 400


def test_other_organizations_owner_cannot_administer(world):
    from mak4i.identity import AccessDeniedError

    _org, stranger = world.cp.onboard_organization(organization_name="Other", owner_display_name="Stranger")
    world.tokens()
    with pytest.raises(AccessDeniedError):
        world.oauth.list_authorizations(actor=stranger, principal_id=world.owner.principal_id)
    with pytest.raises(AccessDeniedError):
        world.oauth.issue_sign_in_code(actor=stranger, principal_id=world.owner.principal_id)
    with pytest.raises(AccessDeniedError):
        world.oauth.disable_client(actor=stranger, client_id=world.public_client["client_id"])


def test_pre_registered_client_is_limited_to_its_organization(world):
    other_org, stranger = world.cp.onboard_organization(organization_name="Other", owner_display_name="Stranger")
    page = world.authorize()
    code, _ = world.oauth.issue_sign_in_code(actor=stranger, principal_id=stranger.principal_id)
    consent = world.sign_in(page, code=code)
    redirect = world.consent(consent)
    assert parse_qs(urlsplit(redirect.headers["location"]).query)["error"] == ["access_denied"]


# -- M9: sign-in codes -----------------------------------------------------------------


def test_sign_in_code_is_single_use(world):  # M9
    code, _ = world.oauth.issue_sign_in_code(actor=world.owner, principal_id=world.owner.principal_id)
    first = world.sign_in(world.authorize(), code=code)
    assert first.status_code == 200 and "Allow" in first.text
    second = world.sign_in(world.authorize(), code=code)
    assert second.status_code == 400 and "invalid, expired or already used" in second.text


def test_sign_in_code_expires(world):  # M9
    code, _ = world.oauth.issue_sign_in_code(actor=world.owner, principal_id=world.owner.principal_id)
    world.clock.advance(601)
    assert world.sign_in(world.authorize(), code=code).status_code == 400


def test_sign_in_code_is_never_a_bearer_token(world):  # M9
    code, _ = world.oauth.issue_sign_in_code(actor=world.owner, principal_id=world.owner.principal_id)
    assert world.mcp(code, "initialize", {}).status_code == 401
    assert world.mcp(code.replace("-", ""), "initialize", {}).status_code == 401


def test_sign_in_is_rate_limited(tmp_path):  # M9
    w = World(tmp_path, rate_limit_per_minute=3)
    with w.client:
        page = w.authorize()
        statuses = [w.sign_in(page, code="WRONG-CODE").status_code for _ in range(4)]
    assert statuses[:3] == [400, 400, 400]
    assert statuses[3] == 429


def test_deactivated_principal_gets_no_sign_in_code(world):  # M9
    from mak4i.identity import CredentialInvalidError

    member = world.cp.create_principal(
        actor=world.owner, organization_id=world.org.organization_id, type="human", display_name="M"
    )
    world.cp.deactivate_principal(actor=world.owner, principal_id=member.principal_id)
    with pytest.raises(CredentialInvalidError):
        world.oauth.issue_sign_in_code(actor=world.owner, principal_id=member.principal_id)


# -- M11: credentials and malformed authorization ---------------------------------------


def test_principal_credentials_keep_working_with_oauth_enabled(world):  # M11
    _cred, raw = world.cp.issue_credential(actor=world.owner, principal_id=world.owner.principal_id)
    http, rpc = world.call_tool(raw, "mak4i_whoami")
    assert http.status_code == 200
    assert rpc["result"]["structuredContent"]["auth_method"] == "credential"
    assert rpc["result"]["structuredContent"]["scopes"] is None


def test_invalid_token_of_either_kind_never_falls_back(world):  # M11 / M7
    _cred, raw = world.cp.issue_credential(actor=world.owner, principal_id=world.owner.principal_id)
    for bad in ("mak4i_" + "x" * 40, "mak4at_" + "x" * 40, "eyJhbGciOiJSUzI1NiJ9.e30.sig", raw + "x"):
        assert world.mcp(bad, "initialize", {}).status_code == 401


def test_duplicate_authorization_headers_or_query_tokens_are_rejected(world):  # M11
    tokens = world.tokens()
    raw_headers = [
        (b"authorization", f"Bearer {tokens['access_token']}".encode()),
        (b"authorization", f"Bearer {tokens['access_token']}".encode()),
    ]

    async def run():
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(message):
            sent.append(message)

        await world.app(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "headers": raw_headers,
                "scheme": "https",
                "server": ("mak4i.example.com", 443),
                "client": ("1.2.3.4", 1234),
                "root_path": "",
                "http_version": "1.1",
                "asgi": {"version": "3.0"},
            },
            receive,
            send,
        )
        return sent

    import anyio

    sent = anyio.run(run)
    assert sent[0]["status"] == 400
    response = world.client.post(f"/mcp?access_token={tokens['access_token']}", json={})
    assert response.status_code == 400


def test_bearer_scheme_is_case_insensitive(world):  # M11
    tokens = world.tokens()
    response = world.client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}},
        headers={"Authorization": f"bearer {tokens['access_token']}", "Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 200


def test_session_is_bound_to_its_principal(world):  # MAK-0008 §1.3
    tokens = world.tokens()
    session = world.session(tokens["access_token"])
    other = world.cp.create_principal(
        actor=world.owner, organization_id=world.org.organization_id, type="service", display_name="Svc"
    )
    _cred, raw = world.cp.issue_credential(actor=world.owner, principal_id=other.principal_id)
    response = world.mcp(raw, "tools/list", {}, session=session, request_id=3)
    assert response.status_code == 404


# -- M12: registration modes -------------------------------------------------------------


def test_dcr_is_off_by_default(world):  # M12
    assert world.client.post("/oauth/register", json={"redirect_uris": ["https://x.example/cb"]}).status_code in (404, 405)


def test_dcr_registers_public_clients_only_when_enabled(tmp_path):  # M12
    w = World(tmp_path, enable_dcr=True)
    with w.client:
        asm = w.client.get("/.well-known/oauth-authorization-server").json()
        assert asm["registration_endpoint"] == ORIGIN + "/oauth/register"
        ok = w.client.post(
            "/oauth/register",
            json={"client_name": "DCR client", "redirect_uris": ["http://127.0.0.1/cb"], "token_endpoint_auth_method": "none"},
        )
        assert ok.status_code == 201
        assert ok.json()["token_endpoint_auth_method"] == "none"
        confidential = w.client.post(
            "/oauth/register",
            json={"redirect_uris": ["https://x.example/cb"], "token_endpoint_auth_method": "client_secret_basic"},
        )
        assert confidential.status_code == 400
        bad_redirect = w.client.post("/oauth/register", json={"redirect_uris": ["http://x.example/cb"]})
        assert bad_redirect.json()["error"] == "invalid_redirect_uri"
        page = w.authorize(client_id=ok.json()["client_id"], redirect_uri="http://127.0.0.1:4000/cb")
        assert page.status_code == 200
        assert "unverified" in w.sign_in(page).text


def test_cimd_disabled_is_not_advertised_and_refused(tmp_path):  # M12
    w = World(tmp_path, enable_cimd=False)
    with w.client:
        assert w.client.get("/.well-known/oauth-authorization-server").json()[
            "client_id_metadata_document_supported"
        ] is False
        assert w.authorize(client_id=CLAUDE_CODE_CIMD, redirect_uri="http://localhost:1/callback").status_code == 400


def test_cimd_document_must_match_its_url(world):  # M12
    response = world.authorize(client_id="https://unknown.example/meta", redirect_uri="http://localhost:1/callback")
    assert response.status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://client.example/meta",
        "https://user:pw@client.example/meta",
        "https://client.example/a/../meta",
        "https://client.example/meta#frag",
        "https://client.example",
    ],
)
def test_cimd_url_rules(url):  # M12
    with pytest.raises(cimd.ClientMetadataError):
        cimd.check_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.1", "0.0.0.0"])
def test_cimd_refuses_non_public_addresses(address):  # M12 (SSRF)
    with pytest.raises(cimd.ClientMetadataError):
        cimd.public_addresses("client.example", 443, lambda host, port: [address])
    with pytest.raises(cimd.ClientMetadataError):
        cimd.public_addresses("client.example", 443, lambda host, port: ["93.184.216.34", address])


def test_cimd_document_validation():  # M12
    good = {"client_id": CLAUDE_CODE_CIMD, "redirect_uris": ["http://localhost/callback"]}
    assert cimd.parse_document(CLAUDE_CODE_CIMD, json.dumps(good).encode())["redirect_uris"]
    for bad in (
        {**good, "client_id": "https://other.example/meta"},
        {**good, "token_endpoint_auth_method": "client_secret_post"},
        {**good, "redirect_uris": []},
        [good],
    ):
        with pytest.raises(cimd.ClientMetadataError):
            cimd.parse_document(CLAUDE_CODE_CIMD, json.dumps(bad).encode())


# -- M13, M14: persistence and secrecy --------------------------------------------------


def test_oauth_state_survives_a_restart(world):  # M13
    tokens = world.tokens()
    restarted = world.make_service()  # new service instance, same database
    validated = restarted.validate_access_token(tokens["access_token"])
    assert validated.principal.principal_id == world.owner.principal_id
    with pytest.raises(InvalidAccessToken):
        restarted.validate_access_token("mak4at_unknown")


def test_no_secret_reaches_the_logs(world, caplog):  # M14
    caplog.set_level(logging.DEBUG)
    code_value, _ = world.oauth.issue_sign_in_code(actor=world.owner, principal_id=world.owner.principal_id)
    page = world.authorize()
    consent = world.sign_in(page, code=code_value)
    redirect = world.consent(consent)
    auth_code = parse_qs(urlsplit(redirect.headers["location"]).query)["code"][0]
    tokens = world.exchange(auth_code).json()
    refreshed = world.refresh(tokens["refresh_token"]).json()
    world.mcp(refreshed["access_token"], "initialize", {})
    world.refresh(tokens["refresh_token"])  # replay
    _client, secret = world.oauth.register_client(
        actor=world.owner,
        organization_id=world.org.organization_id,
        client_name="Conf",
        redirect_uris=["https://c.example/cb"],
        confidential=True,
    )
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "OAUTH_TOKEN_ISSUED" in logged and "OAUTH_REFRESH_REPLAY" in logged
    for secret_value in (
        code_value,
        code_value.replace("-", ""),
        auth_code,
        tokens["access_token"],
        tokens["refresh_token"],
        refreshed["access_token"],
        refreshed["refresh_token"],
        secret,
        _csrf(page.text),
    ):
        assert secret_value not in logged


def test_secrets_are_stored_only_as_hashes(world):  # M14
    tokens = world.tokens()
    with world.db.connect() as conn:
        dump = "\n".join(
            str(row)
            for table in ("oauth_tokens", "oauth_codes", "oauth_sign_in_codes", "oauth_browser_sessions", "oauth_clients")
            for row in conn.exec_driver_sql(f"SELECT * FROM {table}")
        )
    assert tokens["access_token"] not in dump and tokens["refresh_token"] not in dump


def test_confidential_client_must_authenticate(world):
    client, secret = world.oauth.register_client(
        actor=world.owner,
        organization_id=world.org.organization_id,
        client_name="Conf",
        redirect_uris=["http://127.0.0.1/callback"],
        confidential=True,
    )
    code, _ = world.obtain_code(client_id=client["client_id"])
    unauthenticated = world.exchange(code, client_id=client["client_id"])
    assert unauthenticated.status_code == 401 and unauthenticated.json()["error"] == "invalid_client"
    code, _ = world.obtain_code(client_id=client["client_id"])
    ok = world.exchange(code, client_id=client["client_id"], client_secret=secret)
    assert ok.status_code == 200


# -- settings ------------------------------------------------------------------------


def test_settings_validation():
    assert default_issuer("https://h.example/team-a/mcp") == "https://h.example/team-a"
    assert default_issuer("https://h.example/mcp") == "https://h.example"
    for resource, issuer in (
        ("http://h.example/mcp", "http://h.example"),  # http off loopback
        ("https://h.example/mcp/", "https://h.example"),  # trailing slash
        ("https://h.example/mcp", "https://other.example"),  # different origin
        ("https://h.example/mcp?x=1", "https://h.example"),
    ):
        with pytest.raises(OAuthConfigError):
            OAuthSettings(resource=resource, issuer=issuer)
    OAuthSettings(resource="http://127.0.0.1:9090/mcp", issuer="http://127.0.0.1:9090")
    assert OAuthSettings.from_env({}) is None
    with pytest.raises(OAuthConfigError):
        OAuthSettings.from_env({"MAK4I_OAUTH_ENABLED": "1"})
    with pytest.raises(OAuthConfigError):
        OAuthSettings.from_env(
            {"MAK4I_OAUTH_ENABLED": "1", "MAK4I_PUBLIC_ENDPOINT": RESOURCE, "MAK4I_OAUTH_ACCESS_TOKEN_TTL": "999999"}
        )
    s = OAuthSettings.from_env({"MAK4I_OAUTH_ENABLED": "1", "MAK4I_PUBLIC_ENDPOINT": RESOURCE})
    assert s.issuer == ORIGIN and s.enable_cimd and not s.enable_dcr


# -- stdio and CLI ----------------------------------------------------------------------


def test_stdio_session_rechecks_its_credential_before_each_call(tmp_path):  # MAK-0006 §6.4
    from mcp.server.mcpserver.exceptions import ToolError

    from mak4i import mcp_server

    w = World(tmp_path)
    credential, raw = w.cp.issue_credential(actor=w.owner, principal_id=w.owner.principal_id)
    token = mcp_server._stdio_reauthenticate.set(lambda: w.cp.authenticate(raw))
    try:
        assert mcp_server._require_principal().principal_id == w.owner.principal_id
        w.cp.revoke_credential(actor=w.owner, credential_id=credential.credential_id)
        with pytest.raises(ToolError, match="no longer valid"):
            mcp_server._require_principal()
    finally:
        mcp_server._stdio_reauthenticate.reset(token)
        mcp_server.current_principal.set(None)


def test_cli_oauth_commands(tmp_path, monkeypatch, capsys):
    from mak4i import cli

    db = tmp_path / "cli.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{db}")
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", "1")
    monkeypatch.setenv("MAK4I_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    cp = ControlPlane(SqlControlPlaneStore(f"sqlite:///{db}", create_tables=True))
    _org, owner = cp.onboard_organization(organization_name="CLI", owner_display_name="Owner")
    _cred, raw = cp.issue_credential(actor=owner, principal_id=owner.principal_id)
    monkeypatch.setenv("MAK4I_TOKEN", raw)

    monkeypatch.delenv("MAK4I_OAUTH_ENABLED", raising=False)
    assert cli.main(["oauth", "sign-in-code"]) != 0
    assert "OAuth is not enabled" in capsys.readouterr().err

    monkeypatch.setenv("MAK4I_OAUTH_ENABLED", "1")
    monkeypatch.setenv("MAK4I_PUBLIC_ENDPOINT", RESOURCE)
    assert cli.main(["oauth", "sign-in-code"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"sign-in code: [A-Z2-9]{4}(-[A-Z2-9]{4}){6}", out)

    assert cli.main(["oauth", "client", "register", "--name", "Desk", "--redirect-uri", "http://127.0.0.1/cb"]) == 0
    client_id = re.search(r"client_id: (\S+)", capsys.readouterr().out).group(1)
    assert cli.main(["oauth", "client", "list"]) == 0
    listed = capsys.readouterr().out
    assert client_id in listed and "secret" not in listed.replace("client_secret", "")
    assert cli.main(["oauth", "authorization", "list"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert cli.main(["oauth", "authorization", "revoke", "--principal-id", owner.principal_id]) == 0
    assert json.loads(capsys.readouterr().out) == {"revoked": 0}
    assert cli.main(["oauth", "client", "disable", "--client-id", client_id]) == 0
    assert '"status": "disabled"' in capsys.readouterr().out
