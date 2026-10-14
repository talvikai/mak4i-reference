"""Acceptance evidence for MAK-0008 OAuth criteria (CONFORMANCE.md M23,
M29–M33, plus restart, discovery/registration advertisement, session
binding and header-credential precedence).

These tests add *evidence* on top of `tests/test_oauth.py` and reuse its
`World` harness. Each test names the criterion it proves. Where an
existing test in `test_oauth.py` already proves a behavior, the comment
says so instead of duplicating it.
"""

from __future__ import annotations

import copy
import http.client
import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine
from starlette.testclient import TestClient

from mak4i.api import MAK4IEngine
from mak4i.audit import AuditLogger
from mak4i.identity import AccessDeniedError, Authorizer, ControlPlane
from mak4i.identity.admin_events import SqlAdminEventSink
from mak4i.identity.sql_store import SqlControlPlaneStore
from mak4i.identity.tokens import hash_token
from mak4i.mcp_server import build_http_app, build_server
from mak4i.oauth import OAuthService, cimd
from mak4i.oauth import service as oauth_service_module
from mak4i.oauth.service import pkce_s256
from mak4i.oauth.store import SqlOAuthStore
from mak4i.store.local_json import LocalJSONStore
from tests import test_oauth
from tests.test_oauth import CLAUDE_CODE_CIMD, ORIGIN, RESOURCE, VERIFIER, World, _csrf

# `world` fixture (World + open TestClient) re-exported from test_oauth.
from tests.test_oauth import world  # noqa: F401


def _create_args(world, artifact_id: str, artifact_type: str, content: str) -> dict:
    return {
        "project": world.project.project_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "title": artifact_id,
        "content": content,
    }


def _audit_events(caplog) -> list[dict]:
    events = []
    for record in caplog.records:
        if record.name != "mak4i.audit":
            continue
        try:
            events.append(json.loads(record.getMessage()))
        except ValueError:
            continue
    return events


def _durable(world) -> tuple[ControlPlane, OAuthService]:
    """The World's control plane records admin events in memory; production
    wiring (`mak4i.config`, used by the CLI and server) records them in the
    SQL `admin_events` table. Return a control plane + OAuth service over the
    World's database with that durable sink, as production builds them."""
    cp = ControlPlane(SqlControlPlaneStore(world.db), clock=world.clock, events=SqlAdminEventSink(world.db))
    oauth = OAuthService(
        settings=world.settings, store=SqlOAuthStore(world.db), control_plane=cp, audit=AuditLogger(),
        clock=world.clock,
    )
    return cp, oauth


def _cli_env(monkeypatch, world, tmp_path) -> None:
    """Point the real CLI at the World's control-plane database, acting as
    the World's owner through a principal credential (MAK4I_TOKEN)."""
    _cred, raw = world.cp.issue_credential(actor=world.owner, principal_id=world.owner.principal_id)
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{tmp_path / 'cp.db'}")
    monkeypatch.setenv("MAK4I_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAK4I_TOKEN", raw)
    monkeypatch.setenv("MAK4I_OAUTH_ENABLED", "1")
    monkeypatch.setenv("MAK4I_PUBLIC_ENDPOINT", RESOURCE)
    monkeypatch.chdir(tmp_path)


# -- M23: sign-in codes ------------------------------------------------------------------


def test_cli_sign_in_code_has_at_least_128_bits_and_is_stored_only_hashed(
    world, tmp_path, monkeypatch, capsys
):  # M23
    from mak4i import cli

    _cli_env(monkeypatch, world, tmp_path)
    assert cli.main(["oauth", "sign-in-code"]) == 0
    out = capsys.readouterr().out
    code = re.search(r"sign-in code: (\S+)", out).group(1)
    expires_at = datetime.fromisoformat(re.search(r"expires_at:\s+(\S+)", out).group(1))

    # Entropy: the printed code is N symbols from an alphabet of A distinct
    # symbols, chosen with `secrets`; log2(A**N) must be >= 128.
    raw = code.replace("-", "")
    alphabet = oauth_service_module._SIGN_IN_ALPHABET
    assert len(set(alphabet)) == len(alphabet) == 32
    assert set(raw) <= set(alphabet)
    bits = len(raw) * math.log2(len(alphabet))
    assert bits >= 128, bits

    # Every symbol of the alphabet is actually used and codes do not repeat
    # (a sanity check that the alphabet, not a subset, is sampled).
    samples = [
        world.oauth.issue_sign_in_code(actor=world.owner, principal_id=world.owner.principal_id)[0].replace("-", "")
        for _ in range(50)
    ]
    assert len(set(samples)) == len(samples)
    assert set("".join(samples)) == set(alphabet)

    # Stored only as a hash: the raw code (either spelling) appears nowhere in
    # the whole control-plane database; its hash is in oauth_sign_in_codes.
    with world.db.connect() as conn:
        tables = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")]
        dump = "\n".join(
            str(row) for table in tables for row in conn.exec_driver_sql(f'SELECT * FROM "{table}"')
        )
        hashes = {r[0] for r in conn.exec_driver_sql("SELECT code_hash FROM oauth_sign_in_codes")}
    assert code not in dump and raw not in dump
    assert hash_token(raw) in hashes

    # Single use: the CLI-issued code works once on the sign-in page.
    first = world.sign_in(world.authorize(), code=code)
    assert first.status_code == 200 and "Allow" in first.text
    second = world.sign_in(world.authorize(), code=code)
    assert second.status_code == 400 and "invalid, expired or already used" in second.text

    # Expiry: a fresh CLI-issued code is refused once the server clock passes
    # the printed expires_at (the CLI runs on the real clock; the World's
    # server runs on a fake one, which is moved to just past that instant).
    assert cli.main(["oauth", "sign-in-code"]) == 0
    out = capsys.readouterr().out
    code2 = re.search(r"sign-in code: (\S+)", out).group(1)
    expires_at2 = datetime.fromisoformat(re.search(r"expires_at:\s+(\S+)", out).group(1))
    assert expires_at2 - expires_at < timedelta(minutes=5)  # ttl <= 10 min, sanity
    world.clock.now = expires_at2 + timedelta(seconds=1)
    assert world.sign_in(world.authorize(), code=code2).status_code == 400


def test_no_password_or_credential_for_token_exchange_exists(world):  # M23 (§5.3.2)
    _cred, credential = world.cp.issue_credential(actor=world.owner, principal_id=world.owner.principal_id)
    client_id = world.public_client["client_id"]
    for grant in ("password", "client_credentials", "urn:ietf:params:oauth:grant-type:token-exchange"):
        response = world.client.post(
            "/oauth/token",
            data={
                "grant_type": grant,
                "client_id": client_id,
                "username": world.owner.principal_id,
                "password": credential,
                "subject_token": credential,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "unsupported_grant_type"
    # A principal credential is neither an authorization code nor a sign-in code.
    assert world.exchange(credential).json()["error"] == "invalid_grant"
    assert world.sign_in(world.authorize(), code=credential).status_code == 400
    # Sign-in codes are issued only by local tooling: no network endpoint issues them.
    for path in ("/oauth/sign-in-code", "/oauth/sign-in-codes", "/oauth/sign-in/code"):
        assert world.client.post(path, data={"principal_id": world.owner.principal_id}).status_code in (401, 404, 405)


# -- M29: refresh narrowing ----------------------------------------------------------


def test_refresh_narrowing_scope_yields_a_narrower_access_token(world):  # M29
    tokens = world.tokens(scope="mak4i:read mak4i:write")
    ok, rpc = world.call_tool(
        tokens["access_token"], "mak4i_create", _create_args(world, "cache", "caching_decision", "Use RED")
    )
    assert ok.status_code == 200 and rpc["result"]["isError"] is False

    narrowed = world.refresh(tokens["refresh_token"], scope="mak4i:read")
    assert narrowed.status_code == 200, narrowed.text
    body = narrowed.json()
    assert body["scope"] == "mak4i:read"

    # The narrowed access token reads...
    _http, rpc = world.call_tool(body["access_token"], "mak4i_whoami")
    assert rpc["result"]["structuredContent"]["scopes"] == ["mak4i:read"]
    _http, rpc = world.call_tool(body["access_token"], "mak4i_search", {"project": world.project.project_id})
    assert rpc["result"]["isError"] is False
    # ...but cannot write any artifact type: 403 insufficient_scope before the tool runs.
    for artifact_id, artifact_type in (("cache-2", "caching_decision"), ("db", "database_decision")):
        denied, _ = world.call_tool(
            body["access_token"], "mak4i_create", _create_args(world, artifact_id, artifact_type, "x")
        )
        assert denied.status_code == 403
        assert 'error="insufficient_scope"' in denied.headers["www-authenticate"]
        assert "mak4i:write" in denied.headers["www-authenticate"]

    # The narrowed refresh token cannot widen back (widening refused).
    widened = world.refresh(body["refresh_token"], scope="mak4i:read mak4i:write")
    assert widened.status_code == 400 and widened.json()["error"] == "invalid_scope"


# -- M30: revocation endpoint ------------------------------------------------------------


def test_revoking_an_access_token_makes_it_unusable_immediately(world):  # M30
    tokens = world.tokens()
    client_id = world.public_client["client_id"]
    session = world.session(tokens["access_token"])

    # Unknown, malformed and empty tokens: 200 per RFC 7009 / §6.7.
    for unknown in ("mak4at_" + "u" * 43, "mak4rt_" + "u" * 43, "garbage"):
        assert world.client.post("/oauth/revoke", data={"token": unknown, "client_id": client_id}).status_code == 200

    # A token presented by a different client: 200, and no effect.
    other, _ = world.oauth.register_client(
        actor=world.owner, organization_id=world.org.organization_id, client_name="Other",
        redirect_uris=["http://127.0.0.1/other"],
    )
    assert world.client.post(
        "/oauth/revoke", data={"token": tokens["access_token"], "client_id": other["client_id"]}
    ).status_code == 200
    assert world.mcp(tokens["access_token"], "tools/list", {}, session=session, request_id=5).status_code == 200

    # The owning client revokes the ACCESS token: the very next request, even
    # on the already-open MCP session, is 401 invalid_token.
    assert world.client.post(
        "/oauth/revoke",
        data={"token": tokens["access_token"], "token_type_hint": "access_token", "client_id": client_id},
    ).status_code == 200
    rejected = world.mcp(tokens["access_token"], "tools/list", {}, session=session, request_id=6)
    assert rejected.status_code == 401
    assert 'error="invalid_token"' in rejected.headers["www-authenticate"]
    assert world.mcp(tokens["access_token"], "initialize", {}).status_code == 401
    # Revoking it again is still 200.
    assert world.client.post(
        "/oauth/revoke", data={"token": tokens["access_token"], "client_id": client_id}
    ).status_code == 200

    # §6.7 requires "at least that token": this implementation keeps the
    # authorization, so the refresh token still yields a working new token.
    refreshed = world.refresh(tokens["refresh_token"])
    assert refreshed.status_code == 200
    assert world.mcp(refreshed.json()["access_token"], "initialize", {}).status_code == 200
    # (Revoking a REFRESH token revokes the whole authorization:
    # test_oauth.py::test_revocation_endpoint.)


# -- M31 / §7.4: default scopes; credentials have no ceiling (item 10) -----------------


def test_authorization_without_scope_grants_read_write_only(world):  # M31
    page = world.authorize(scope=None)
    assert page.status_code == 200
    consent = world.sign_in(page)
    assert "Read project knowledge" in consent.text and "Create and supersede" in consent.text
    redirect = world.consent(consent)
    code = parse_qs(urlsplit(redirect.headers["location"]).query)["code"][0]
    body = world.exchange(code).json()
    assert body["scope"] == "mak4i:read mak4i:write"

    # Owner gets the `resolve` grant so that only the token's scope can stop it.
    world.cp.grant(
        actor=world.owner, principal_id=world.owner.principal_id, project_id=world.project.project_id,
        permissions=["read", "write", "resolve"],
    )
    _http, rpc = world.call_tool(body["access_token"], "mak4i_whoami")
    assert rpc["result"]["structuredContent"]["scopes"] == ["mak4i:read", "mak4i:write"]
    resolve_args = {
        "project": world.project.project_id, "conflict_id": "cfl_none", "decision": "keep",
        "reason": "test",
    }
    denied, _ = world.call_tool(body["access_token"], "mak4i_resolve_conflict", resolve_args)
    assert denied.status_code == 403
    challenge = denied.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge and "mak4i:resolve" in challenge

    # Contrast: a token that asked for mak4i:resolve passes the scope gate.
    with_resolve = world.tokens(scope="mak4i:read mak4i:resolve")["access_token"]
    passed, _ = world.call_tool(with_resolve, "mak4i_resolve_conflict", resolve_args)
    assert passed.status_code == 200

    # §7.4 / item 10: a principal credential on the same OAuth-enabled server
    # has no scope ceiling — it is never treated as a scoped (downgraded) token.
    _cred, credential = world.cp.issue_credential(actor=world.owner, principal_id=world.owner.principal_id)
    passed, rpc = world.call_tool(credential, "mak4i_resolve_conflict", resolve_args)
    assert passed.status_code == 200
    _http, rpc = world.call_tool(credential, "mak4i_whoami")
    assert rpc["result"]["structuredContent"]["auth_method"] == "credential"
    assert rpc["result"]["structuredContent"]["scopes"] is None
    for artifact_id, artifact_type in (("cache", "caching_decision"), ("db", "database_decision")):
        http, rpc = world.call_tool(credential, "mak4i_create", _create_args(world, artifact_id, artifact_type, "v1"))
        assert http.status_code == 200 and rpc["result"]["isError"] is False
        assert rpc["result"]["structuredContent"]["created_by"] == world.owner.principal_id


# -- M32: CIMD fetch limits against a real local HTTP server ----------------------------
#
# `cimd.fetch_document` only speaks HTTPS and its SSRF guard refuses every
# non-public address (127.0.0.1 included), so a plain local server cannot be
# reached through it unmodified. These tests keep the real code path —
# `check_url`, `public_addresses`, the status/size/Cache-Control handling,
# the timeout, http.client's no-redirect behavior — and replace only two
# seams: the resolver (injectable, returns a public documentation address so
# the guard passes) and `_PinnedHTTPSConnection` (monkeypatched to a plain
# HTTP connection that dials the loopback test server instead of the
# validated address; TLS is therefore not exercised here). The connection
# records the address it was asked to pin, proving the guard's validated
# address is what the fetcher connects to.

_PUBLIC_ADDRESS = "93.184.216.34"
_CIMD_HOST = "client.example"


class _CimdServer:
    def __init__(self):
        self.hits: dict[str, int] = {}
        self.release_slow = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def do_GET(self):
                path = urlsplit(self.path).path
                outer.hits[path] = outer.hits.get(path, 0) + 1
                url = f"https://{_CIMD_HOST}{path}"
                if path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"https://{_CIMD_HOST}/ok")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path == "/slow":
                    outer.release_slow.wait(2)
                doc = {
                    "client_id": url,
                    "client_name": "Local CIMD client",
                    "redirect_uris": ["http://localhost/callback"],
                    "token_endpoint_auth_method": "none",
                }
                body = json.dumps(doc).encode()
                if path == "/big":
                    body = body + b" " * (cimd.MAX_DOCUMENT_BYTES + 1 - len(body))
                if path == "/exact":
                    body = body + b" " * (cimd.MAX_DOCUMENT_BYTES - len(body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                max_age = {"/ok": 60, "/capped": 10 * 365 * 86400}.get(path)
                if max_age is not None:
                    self.send_header("Cache-Control", f"public, max-age={max_age}")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass  # the client gave up (timeout test)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def url(self, path: str) -> str:
        return f"https://{_CIMD_HOST}{path}"


@pytest.fixture
def cimd_server(monkeypatch):
    server = _CimdServer()
    server.thread.start()
    pinned: list[tuple[str, str, int]] = []

    class LoopbackConnection(http.client.HTTPConnection):
        def __init__(self, host, address, port, timeout):
            pinned.append((host, address, port))
            super().__init__("127.0.0.1", server.port, timeout=timeout)

    monkeypatch.setattr(cimd, "_PinnedHTTPSConnection", LoopbackConnection)
    server.pinned = pinned
    yield server
    server.release_slow.set()
    server.httpd.shutdown()
    server.httpd.server_close()


def _fetch(url: str) -> cimd.FetchedDocument:
    return cimd.fetch_document(url, resolver=lambda host, port: [_PUBLIC_ADDRESS])


def test_cimd_limits_are_within_the_spec():  # M32 (§8.3 bounds)
    assert 0 < cimd.FETCH_TIMEOUT_SECONDS <= 5
    assert 0 < cimd.MAX_DOCUMENT_BYTES <= 64 * 1024
    assert 0 < cimd.DEFAULT_CACHE_SECONDS <= 24 * 3600


def test_cimd_fetch_succeeds_and_connects_to_the_validated_address(cimd_server):  # M32
    fetched = _fetch(cimd_server.url("/ok"))
    assert fetched.max_age == 60
    assert cimd.parse_document(cimd_server.url("/ok"), fetched.body)["client_name"] == "Local CIMD client"
    assert cimd_server.pinned == [(_CIMD_HOST, _PUBLIC_ADDRESS, 443)]


def test_cimd_fetch_never_follows_redirects(cimd_server):  # M32
    with pytest.raises(cimd.ClientMetadataError, match="HTTP 302"):
        _fetch(cimd_server.url("/redirect"))
    assert cimd_server.hits == {"/redirect": 1}  # the Location target was never requested


def test_cimd_fetch_enforces_the_size_limit(cimd_server):  # M32
    with pytest.raises(cimd.ClientMetadataError, match="too large"):
        _fetch(cimd_server.url("/big"))
    exact = _fetch(cimd_server.url("/exact"))  # exactly at the limit is accepted
    assert len(exact.body) == cimd.MAX_DOCUMENT_BYTES


def test_cimd_fetch_times_out(cimd_server, monkeypatch):  # M32
    monkeypatch.setattr(cimd, "FETCH_TIMEOUT_SECONDS", 0.3)
    started = time.monotonic()
    with pytest.raises(cimd.ClientMetadataError, match="could not be fetched"):
        _fetch(cimd_server.url("/slow"))
    assert time.monotonic() - started < 1.5
    cimd_server.release_slow.set()


def test_cimd_documents_are_cached_within_their_lifetime(cimd_server, world):  # M32
    service = OAuthService(
        settings=world.settings,
        store=SqlOAuthStore(world.db),
        control_plane=world.cp,
        clock=world.clock,
        fetch_client_document=_fetch,
    )
    url = cimd_server.url("/ok")
    first = service.resolve_client(url)
    assert first["client_name"] == "Local CIMD client"
    world.clock.advance(59)
    assert service.resolve_client(url)["client_id"] == url
    assert cimd_server.hits["/ok"] == 1  # second resolution served from the cache
    world.clock.advance(2)  # past Cache-Control max-age=60
    service.resolve_client(url)
    assert cimd_server.hits["/ok"] == 2

    # A longer max-age is capped at 24 hours.
    capped = service.resolve_client(cimd_server.url("/capped"))
    assert capped["cache_expires_at"] - capped["fetched_at"] == timedelta(hours=24)


def test_cimd_cache_applies_through_the_authorization_endpoint(cimd_server, tmp_path, monkeypatch):  # M32
    # World.make_service wires `test_oauth._fake_fetch`; swap in the real fetcher.
    monkeypatch.setattr(test_oauth, "_fake_fetch", _fetch)
    w = World(tmp_path)
    with w.client:
        url = cimd_server.url("/ok")
        for port in (50001, 50002):
            page = w.authorize(client_id=url, redirect_uri=f"http://localhost:{port}/callback")
            assert page.status_code == 200, page.text
        assert cimd_server.hits["/ok"] == 1
        # A redirecting or oversized document is refused at the endpoint (error page, no redirect).
        for path in ("/redirect", "/big"):
            refused = w.authorize(client_id=cimd_server.url(path), redirect_uri="http://localhost:1/callback")
            assert refused.status_code == 400 and "location" not in refused.headers


# -- M33: per-authorization revocation and auditing -------------------------------------


def test_revoking_one_authorization_leaves_the_principals_others_working(
    world, tmp_path, monkeypatch, capsys, caplog
):  # M33
    from mak4i import cli

    caplog.set_level(logging.INFO, logger="mak4i.audit")
    second_client, _ = world.oauth.register_client(
        actor=world.owner, organization_id=world.org.organization_id, client_name="Second",
        redirect_uris=["http://127.0.0.1/callback"],
    )
    a = world.tokens()
    b_code, _ = world.obtain_code(client_id=second_client["client_id"])
    b = world.exchange(b_code, client_id=second_client["client_id"]).json()
    c = world.tokens()  # same principal, same client, separate authorization

    listed = world.oauth.list_authorizations(actor=world.owner, principal_id=world.owner.principal_id)
    assert len(listed) == 3
    target = world.oauth.validate_access_token(a["access_token"]).authorization_id

    _cli_env(monkeypatch, world, tmp_path)
    assert cli.main(["oauth", "authorization", "revoke", "--authorization-id", target]) == 0
    revoked = json.loads(capsys.readouterr().out)
    assert revoked["authorization_id"] == target
    assert revoked["status"] == "revoked" and revoked["revoked_reason"] == "revoked_by_admin"
    assert "token" not in json.dumps(revoked).replace("token_", "")

    # Only that authorization's tokens stop working.
    assert world.mcp(a["access_token"], "initialize", {}).status_code == 401
    assert world.refresh(a["refresh_token"]).json()["error"] == "invalid_grant"
    for other, client_id in ((b, second_client["client_id"]), (c, world.public_client["client_id"])):
        http, rpc = world.call_tool(other["access_token"], "mak4i_whoami")
        assert http.status_code == 200
        assert world.refresh(other["refresh_token"], client_id=client_id).status_code == 200

    # Audited in the log stream...
    revocations = [e for e in _audit_events(caplog) if e["event"] == "OAUTH_REVOKED" and e.get("by") == "admin"]
    assert revocations and revocations[-1]["authorization_id"] == target
    assert revocations[-1]["actor"] == world.owner.principal_id
    # ...and durably, in the owner-retrievable admin event log (MAK-0006 §8.5),
    # read back through the owner-gated API of a control plane over the same DB.
    durable_cp, _ = _durable(world)
    events = durable_cp.list_admin_events(actor=world.owner, organization_id=world.org.organization_id)
    revoke_events = [e for e in events if e.action == "oauth_authorization.revoke"]
    assert len(revoke_events) == 1
    assert revoke_events[0].targets == {"authorization_id": target}
    assert revoke_events[0].actor_principal_id == world.owner.principal_id
    assert revoke_events[0].outcome == "succeeded"


def test_every_oauth_admin_action_is_durably_audited(world, caplog):  # M33
    caplog.set_level(logging.INFO, logger="mak4i.audit")
    org_id = world.org.organization_id
    cp, oauth = _durable(world)
    client, _ = oauth.register_client(
        actor=world.owner, organization_id=org_id, client_name="Audited", redirect_uris=["http://127.0.0.1/a"]
    )
    oauth.issue_sign_in_code(actor=world.owner, principal_id=world.owner.principal_id)
    tokens = world.tokens()
    authorization_id = oauth.validate_access_token(tokens["access_token"]).authorization_id
    oauth.revoke_authorization(actor=world.owner, authorization_id=authorization_id)
    world.tokens()
    assert oauth.revoke_all(actor=world.owner, principal_id=world.owner.principal_id) == 1
    world.tokens()
    assert oauth.revoke_all(actor=world.owner, client_id=world.public_client["client_id"]) == 1
    oauth.disable_client(actor=world.owner, client_id=client["client_id"])

    events = cp.list_admin_events(actor=world.owner, organization_id=org_id)
    actions = [e.action for e in events]
    for action in (
        "oauth_client.register",
        "oauth_sign_in_code.issue",
        "oauth_authorization.revoke",
        "oauth_authorization.revoke_all",
        "oauth_client.disable",
    ):
        assert action in actions, actions
    for event in events:
        if event.action.startswith("oauth_"):
            assert event.actor_principal_id == world.owner.principal_id
            assert event.outcome == "succeeded"
            assert event.correlation_id
    log_names = {e["event"] for e in _audit_events(caplog)}
    assert {"OAUTH_CLIENT_REGISTERED", "OAUTH_SIGN_IN_CODE_ISSUED", "OAUTH_REVOKED", "OAUTH_CLIENT_DISABLED"} <= log_names


def test_denied_oauth_admin_actions_are_durably_audited(world):  # M33 / MAK-0006 §8.5
    cp, oauth = _durable(world)
    _other_org, stranger = cp.onboard_organization(organization_name="Other", owner_display_name="Stranger")
    tokens = world.tokens()
    authorization_id = oauth.validate_access_token(tokens["access_token"]).authorization_id
    with pytest.raises(AccessDeniedError):
        oauth.revoke_authorization(actor=stranger, authorization_id=authorization_id)
    with pytest.raises(AccessDeniedError):
        oauth.disable_client(actor=stranger, client_id=world.public_client["client_id"])
    with pytest.raises(AccessDeniedError):
        oauth.issue_sign_in_code(actor=stranger, principal_id=world.owner.principal_id)
    with pytest.raises(AccessDeniedError):
        oauth.revoke_all(actor=stranger, principal_id=world.owner.principal_id)
    with pytest.raises(AccessDeniedError):
        oauth.register_client(actor=stranger, organization_id=world.org.organization_id, client_name="x",
                              redirect_uris=["https://client.example/cb"])
    events = cp.list_admin_events(actor=world.owner, organization_id=world.org.organization_id)
    denied = [e for e in events if e.action.startswith("oauth_") and e.outcome == "denied"]
    assert {e.action for e in denied} == {
        "oauth_authorization.revoke", "oauth_client.disable", "oauth_sign_in_code.issue",
        "oauth_authorization.revoke_all", "oauth_client.register",
    }
    assert all(e.actor_principal_id == stranger.principal_id for e in denied)


# -- M13 / §6.8: restart -------------------------------------------------------------------


def _restart(world: World, tmp_path) -> World:
    """A new process over the same storage: fresh engine, control plane,
    OAuth service, MCP app and HTTP client; only the SQLite file and the
    artifact directory are shared."""
    restarted = copy.copy(world)
    restarted.db = create_engine(f"sqlite:///{tmp_path / 'cp.db'}", future=True)
    restarted.cp = ControlPlane(SqlControlPlaneStore(restarted.db), clock=world.clock)
    restarted.oauth = restarted.make_service()
    engine = MAK4IEngine(
        LocalJSONStore(tmp_path / "artifacts"), authorizer=Authorizer(restarted.cp), control_plane=restarted.cp
    )
    restarted.app = build_http_app(
        build_server(engine), control_plane=restarted.cp, oauth=restarted.oauth, audit=AuditLogger()
    )
    restarted.client = TestClient(restarted.app, base_url=ORIGIN)
    return restarted


def test_oauth_state_survives_a_full_restart(tmp_path):  # M13 / §6.8
    w = World(tmp_path)
    with w.client:
        tokens = w.tokens()
        rotated_away = w.tokens()
        rotated = w.refresh(rotated_away["refresh_token"]).json()  # rotation recorded before restart
        _http, rpc = w.call_tool(
            tokens["access_token"], "mak4i_create", _create_args(w, "cache", "caching_decision", "Use RED")
        )
        assert rpc["result"]["isError"] is False
        pending_code, _ = w.obtain_code()  # outstanding authorization code
        sign_in_code, _ = w.oauth.issue_sign_in_code(actor=w.owner, principal_id=w.owner.principal_id)
        w.authorize(client_id=CLAUDE_CODE_CIMD, redirect_uri="http://localhost:1/callback")  # CIMD cached
        browser_page = w.authorize()  # browser session started before the restart
        cookies = dict(w.client.cookies)
    w.db.dispose()

    r = _restart(w, tmp_path)
    with r.client:
        # Access token issued before the restart works and sees prior data.
        http, rpc = r.call_tool(tokens["access_token"], "mak4i_search", {"project": w.project.project_id})
        assert http.status_code == 200
        assert "cache" in json.dumps(rpc["result"]["structuredContent"])
        http, rpc = r.call_tool(
            tokens["access_token"], "mak4i_create", _create_args(r, "db", "database_decision", "Use Postgres")
        )
        assert rpc["result"]["isError"] is False
        # Refresh token issued before the restart rotates.
        assert r.refresh(tokens["refresh_token"]).status_code == 200
        # Rotation state survived: replaying the pre-restart rotated-away token is detected.
        assert r.refresh(rotated_away["refresh_token"]).json()["error"] == "invalid_grant"
        assert r.mcp(rotated["access_token"], "initialize", {}).status_code == 401
        # Outstanding authorization code and sign-in code are redeemable after the restart.
        assert r.exchange(pending_code).status_code == 200
        assert r.sign_in(r.authorize(), code=sign_in_code).status_code == 200
        # Pre-registered client and cached CIMD client are still known.
        assert r.authorize().status_code == 200
        assert r.authorize(client_id=CLAUDE_CODE_CIMD, redirect_uri="http://localhost:2/callback").status_code == 200
        # A browser session started before the restart continues (server-side sessions).
        r.client.cookies.update(cookies)
        resumed = r.client.post(
            "/oauth/sign-in",
            data={
                "code": r.oauth.issue_sign_in_code(actor=r.owner, principal_id=r.owner.principal_id)[0],
                "csrf": _csrf(browser_page.text),
            },
        )
        assert resumed.status_code == 200 and "Allow" in resumed.text


# -- M2 / M12 / §4.3–§4.4: registration advertisement and discovery ---------------------


@pytest.mark.parametrize("enable_dcr", [False, True])
@pytest.mark.parametrize("enable_cimd", [False, True])
def test_metadata_advertises_exactly_the_enabled_registration_modes(tmp_path, enable_dcr, enable_cimd):  # §4.3 / M12
    w = World(tmp_path, enable_dcr=enable_dcr, enable_cimd=enable_cimd)
    with w.client:
        asm = w.client.get("/.well-known/oauth-authorization-server")
        assert asm.status_code == 200
        metadata = asm.json()
        assert ("registration_endpoint" in metadata) is enable_dcr
        if enable_dcr:
            assert metadata["registration_endpoint"] == ORIGIN + "/oauth/register"
        assert metadata["client_id_metadata_document_supported"] is enable_cimd
        register = w.client.post("/oauth/register", json={"redirect_uris": ["http://127.0.0.1/cb"]})
        assert (register.status_code == 201) is enable_dcr


def test_discovery_is_public_and_prefix_aware(tmp_path):  # M2 / §4.1 / §4.4
    prefix_resource = "https://mak4i.example.com/team-a/mcp"
    prefix_issuer = "https://mak4i.example.com/team-a"
    w = World(tmp_path, resource=prefix_resource, issuer=prefix_issuer, enable_dcr=True)
    with w.client:
        # Reachable with no Authorization header at all.
        assert "authorization" not in {k.lower() for k in w.client.headers}
        for path in (
            "/.well-known/oauth-protected-resource/team-a/mcp",
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-authorization-server/team-a",
        ):
            response = w.client.get(path)
            assert response.status_code == 200, path
        prm = w.client.get("/.well-known/oauth-protected-resource/team-a/mcp").json()
        assert prm["resource"] == prefix_resource
        assert prm["authorization_servers"] == [prefix_issuer]
        asm = w.client.get("/.well-known/oauth-authorization-server/team-a").json()
        assert asm["issuer"] == prefix_issuer
        for key, path in (
            ("authorization_endpoint", "/oauth/authorize"),
            ("token_endpoint", "/oauth/token"),
            ("revocation_endpoint", "/oauth/revoke"),
            ("registration_endpoint", "/oauth/register"),
        ):
            assert asm[key] == prefix_issuer + path

        # The full flow works through a simulated reverse proxy that routes the
        # advertised (prefixed) URLs to the app by stripping /team-a, as §4.4
        # requires. Going through the prefixed paths matters: the browser-
        # session cookie is scoped to the public path /team-a/oauth.
        proxy = TestClient(_StripPrefix(w.app, "/team-a"), base_url=ORIGIN)
        authorize_url = urlsplit(asm["authorization_endpoint"]).path
        page = proxy.get(authorize_url, params={
            "response_type": "code", "client_id": w.public_client["client_id"],
            "redirect_uri": "http://127.0.0.1:53111/callback", "code_challenge": pkce_s256(VERIFIER),
            "code_challenge_method": "S256", "resource": prefix_resource, "state": "s",
        })
        assert page.status_code == 200, page.text
        sign_in_code, _ = w.oauth.issue_sign_in_code(actor=w.owner, principal_id=w.owner.principal_id)
        consent = proxy.post("/team-a/oauth/sign-in", data={"code": sign_in_code, "csrf": _csrf(page.text)})
        assert consent.status_code == 200, consent.text
        redirect = proxy.post(
            "/team-a/oauth/consent", data={"csrf": _csrf(consent.text), "decision": "approve"}, follow_redirects=False
        )
        query = parse_qs(urlsplit(redirect.headers["location"]).query)
        assert query["iss"] == [prefix_issuer]
        token = proxy.post(urlsplit(asm["token_endpoint"]).path, data={
            "grant_type": "authorization_code", "code": query["code"][0],
            "redirect_uri": "http://127.0.0.1:53111/callback", "code_verifier": VERIFIER,
            "client_id": w.public_client["client_id"], "resource": prefix_resource,
        })
        assert token.status_code == 200, token.text
        access = token.json()["access_token"]
        initialize = proxy.post(
            "/team-a/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}},
            headers={"Authorization": f"Bearer {access}", "Accept": "application/json, text/event-stream"},
        )
        assert initialize.status_code == 200
        challenge = proxy.post("/team-a/mcp", json={}).headers["www-authenticate"]
        assert f'resource_metadata="{ORIGIN}/.well-known/oauth-protected-resource/team-a/mcp"' in challenge
        # The unprefixed resource is a different audience and is refused.
        wrong = proxy.get(authorize_url, params={
            "response_type": "code", "client_id": w.public_client["client_id"],
            "redirect_uri": "http://127.0.0.1:53111/callback", "code_challenge": pkce_s256(VERIFIER),
            "code_challenge_method": "S256", "resource": RESOURCE,
        }, follow_redirects=False)
        assert wrong.status_code == 302
        assert parse_qs(urlsplit(wrong.headers["location"]).query)["error"] == ["invalid_target"]


class _StripPrefix:
    """Minimal reverse proxy: forwards /<prefix>/x as /x, everything else unchanged."""

    def __init__(self, app, prefix: str):
        self.app, self.prefix = app, prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(self.prefix + "/"):
            path = scope["path"][len(self.prefix):]
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await self.app(scope, receive, send)


# -- §1.3: MCP session binding (item 9) -----------------------------------------------------


def test_session_cannot_be_used_with_another_principals_oauth_token(world):  # MAK-0008 §1.3
    # test_oauth.py::test_session_is_bound_to_its_principal covers an OAuth
    # session hijacked with another principal's *credential*; this covers
    # another principal's *OAuth token*, and that the refused call had no effect.
    member = world.cp.create_principal(
        actor=world.owner, organization_id=world.org.organization_id, type="human", display_name="Member"
    )
    world.cp.grant(
        actor=world.owner, principal_id=member.principal_id, project_id=world.project.project_id,
        permissions=["read", "write"],
    )
    a = world.tokens()
    b = world.tokens(principal_id=member.principal_id)
    session_a = world.session(a["access_token"])

    hijack = world.mcp(
        b["access_token"], "tools/call",
        {"name": "mak4i_create", "arguments": _create_args(world, "db", "database_decision", "hijack")},
        session=session_a, request_id=7,
    )
    assert hijack.status_code in (403, 404)
    _http, rpc = world.call_tool(a["access_token"], "mak4i_search", {"project": world.project.project_id}, session=session_a)
    assert "hijack" not in json.dumps(rpc["result"])
    # The legitimate owner's session still works and can write.
    _http, rpc = world.call_tool(
        a["access_token"], "mak4i_create", _create_args(world, "cache", "caching_decision", "Use RED"), session=session_a
    )
    assert rpc["result"]["isError"] is False


# -- M25 / M38: consent is never remembered; token, revocation and registration are rate-limited --


def test_consent_is_asked_again_even_for_a_pre_registered_client(tmp_path):  # M25
    w = World(tmp_path)
    with w.client:
        w.obtain_code()  # signed in and approved once; the browser session is still valid
        again = w.authorize(state="st-2")
        assert again.status_code == 200, again.headers.get("location")  # no silent redirect with a code
        after_sign_in = w.sign_in(again)
    assert after_sign_in.status_code == 200
    assert 'name="decision"' in after_sign_in.text  # consent is asked again, never remembered


@pytest.mark.parametrize("endpoint", ["/oauth/token", "/oauth/revoke", "/oauth/register"])
def test_token_revocation_and_registration_endpoints_are_rate_limited(tmp_path, endpoint):  # M38
    w = World(tmp_path, rate_limit_per_minute=3, enable_dcr=True)
    with w.client:
        if endpoint == "/oauth/register":
            send = lambda: w.client.post(endpoint, json={"redirect_uris": ["http://127.0.0.1/cb"], "client_name": "x"})  # noqa: E731
        else:
            send = lambda: w.client.post(endpoint, data={"client_id": w.public_client["client_id"], "token": "x",  # noqa: E731
                                                         "grant_type": "authorization_code", "code": "nope"})
        statuses = [send().status_code for _ in range(4)]
    assert 429 not in statuses[:3], statuses
    assert statuses[3] == 429
