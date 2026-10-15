"""Client-scoped project access (MAK-0006 §5.7, CLIENT-AUTH-001), mapped to
CONFORMANCE.md CP1–CP12.

The same principal reaches the same project through two pre-registered
OAuth clients ("Claude" and "ChatGPT") and through a principal credential,
over real MCP-over-HTTP requests with real OAuth tokens. Writes run with two
artifact types through the same code (CLAUDE.md testing discipline).
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlsplit

import pytest

from mak4i.audit import LOGGER_NAME
from mak4i.identity import AccessDeniedError
from mak4i.identity.control_plane import ControlPlane
from mak4i.identity.errors import InvalidClientPolicyError
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from tests.test_admin_cli_acceptance import admin, template  # noqa: F401 - fixtures
from tests.test_oauth import World

TYPES = ["caching_decision", "database_decision"]


class Clients(World):
    """`World` plus a second pre-registered client and per-client tokens."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.claude = self.public_client["client_id"]
        chatgpt, _ = self.oauth.register_client(
            actor=self.owner, organization_id=self.org.organization_id,
            client_name="ChatGPT", redirect_uris=["http://127.0.0.1/callback"],
        )
        self.chatgpt = chatgpt["client_id"]
        self.project_id = self.project.project_id

    def tokens_for(self, client_id: str, principal_id=None, scope="mak4i:read mak4i:write") -> dict:
        page = self.authorize(client_id=client_id, scope=scope)
        assert page.status_code == 200, page.text
        consent = self.sign_in(page, principal_id=principal_id)
        redirect = self.consent(consent)
        code = parse_qs(urlsplit(redirect.headers["location"]).query)["code"][0]
        response = self.exchange(code, client_id=client_id)
        assert response.status_code == 200, response.text
        return response.json()

    def policy(self, client_id: str, permissions: list[str], *, kind="oauth_client", principal_id=None):
        return self.cp.set_client_policy(
            actor=self.owner, principal_id=principal_id or self.owner.principal_id,
            project_id=self.project_id, client_kind=kind, client_id=client_id, permissions=permissions,
        )

    def tool(self, token, name, arguments=None, session=None):
        _response, rpc = self.call_tool(token, name, arguments, session=session)
        return rpc["result"]

    def create(self, token, artifact_id, artifact_type="caching_decision", session=None):
        return self.tool(token, "mak4i_create", {
            "project": self.project_id, "artifact_id": artifact_id, "artifact_type": artifact_type,
            "title": artifact_id, "content": f"content of {artifact_id}",
        }, session=session)


@pytest.fixture
def w(tmp_path):
    world = Clients(tmp_path)
    with world.client:
        yield world


def _error(result) -> dict:
    assert result["isError"] is True, result
    return result["structuredContent"]["error"]


# -- CP4: same principal and project, two clients ------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_claude_read_only_chatgpt_read_write(w, artifact_type):  # CP4, CP12
    w.policy(w.claude, ["read"])
    w.policy(w.chatgpt, ["read", "write"])
    claude = w.tokens_for(w.claude)["access_token"]
    chatgpt = w.tokens_for(w.chatgpt)["access_token"]

    denied = _error(w.create(claude, "from-claude", artifact_type))
    assert denied["code"] == "access_denied" and denied["reason"] == "client_not_permitted"
    assert denied["retryable"] is False
    assert w.chatgpt not in denied["message"]  # names no other client (§5.7.6)

    written = w.create(chatgpt, "from-chatgpt", artifact_type)
    assert written.get("isError") is not True, written
    record = written["structuredContent"]
    assert record["provenance"]["oauth_client_id"] == w.chatgpt  # CP12
    assert record["provenance"]["auth_method"] == "oauth"

    current = w.tool(claude, "mak4i_get_current", {"project": w.project_id})
    assert [a["artifact_id"] for a in current["structuredContent"]["artifacts"]] == ["from-chatgpt"]


# -- CP1: identity comes from the authentication only ---------------------------------


def test_client_info_cannot_change_the_client(w):  # CP1, CP10
    w.policy(w.claude, ["read"])
    w.policy(w.chatgpt, ["read", "write"])
    claude = w.tokens_for(w.claude)["access_token"]
    spoofed = w.mcp(claude, "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "ChatGPT", "version": "1.0.0"},
    })
    session = spoofed.headers["mcp-session-id"]
    assert w.mcp(claude, "notifications/initialized", session=session, request_id=None).status_code == 202
    assert _error(w.create(claude, "spoof", session=session))["reason"] == "client_not_permitted"
    whoami = w.tool(claude, "mak4i_whoami", session=session)["structuredContent"]
    assert whoami["client"] == {"kind": "oauth_client", "id": w.claude, "registration": "pre_registered"}
    assert "server-client-policies" in whoami["protocol"]["profiles"]


# -- CP2, CP3: which clients a policy may name ---------------------------------------


def test_policy_targets_are_validated(w):  # CP2, CP3
    other_org, other_owner = w.cp.onboard_organization(organization_name="Globex", owner_display_name="Gus")
    foreign, _ = w.oauth.register_client(
        actor=other_owner, organization_id=other_org.organization_id,
        client_name="Foreign", redirect_uris=["http://127.0.0.1/callback"],
    )
    member = w.cp.create_principal(actor=w.owner, organization_id=w.org.organization_id,
                                   type="human", display_name="Mia")
    member_cred, _ = w.cp.issue_credential(actor=w.owner, principal_id=member.principal_id)
    for kind, client_id in (
        ("oauth_client", foreign["client_id"]),       # another organization's client
        ("oauth_client", "mak4c_does_not_exist"),     # unknown client
        ("credential", member_cred.credential_id),    # another principal's credential
        ("client_info", "ChatGPT"),                   # not a client kind
    ):
        with pytest.raises(InvalidClientPolicyError):
            w.policy(client_id, ["read"], kind=kind)
    # A dynamically registered client can never be named.
    w.oauth._store.put_client({**w.oauth._store.get_client(w.claude), "client_id": "dcr-1", "kind": "dcr",
                              "organization_id": None})
    with pytest.raises(InvalidClientPolicyError, match="dynamically registered"):
        w.policy("dcr-1", ["read"])
    # A metadata-document client (e.g. Claude Code) can.
    cimd = w.policy("https://claude.ai/oauth/claude-code-client-metadata", ["read"])
    assert cimd.to_protocol()["client"]["kind"] == "oauth_client"
    # One policy per (principal, project, client): setting again replaces it.
    first = w.policy(w.chatgpt, ["read"])
    second = w.policy(w.chatgpt, ["read", "write"])
    assert first.policy_id == second.policy_id
    assert [p.permissions for p in w.cp.list_client_policies(actor=w.owner, project_id=w.project_id)
            if p.client_id == w.chatgpt] == [["read", "write"]]


# -- CP5: a policy never widens -------------------------------------------------------


def test_policy_never_adds_a_permission(w):  # CP5
    reader = w.cp.create_principal(actor=w.owner, organization_id=w.org.organization_id,
                                   type="human", display_name="Reader")
    w.cp.grant(actor=w.owner, principal_id=reader.principal_id, project_id=w.project_id, permissions=["read"])
    w.policy(w.chatgpt, ["read", "write", "resolve"], principal_id=reader.principal_id)
    token = w.tokens_for(w.chatgpt, principal_id=reader.principal_id)["access_token"]
    error = _error(w.create(token, "nope"))
    assert error["code"] == "access_denied" and "reason" not in error  # the grant lacks write
    projects = w.tool(token, "mak4i_list_projects")["structuredContent"]["result"]
    assert projects[0]["permissions"] == ["read"]


# -- CP6: restriction mode -------------------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_mode_off_is_unchanged_and_mode_on_denies_unlisted_clients(w, artifact_type):  # CP6, CP10
    token = w.tokens_for(w.chatgpt)["access_token"]
    assert w.create(token, "before", artifact_type).get("isError") is not True
    w.cp.set_client_restrictions(actor=w.owner, project_id=w.project_id, enabled=True)
    session = w.session(token)
    assert w.tool(token, "mak4i_list_projects", session=session)["structuredContent"]["result"] == []
    error = _error(w.tool(token, "mak4i_get_current", {"project": w.project_id}, session=session))
    assert error["reason"] == "client_not_permitted"
    w.policy(w.chatgpt, ["read"])
    listed = w.tool(token, "mak4i_list_projects", session=session)["structuredContent"]["result"]
    assert [(p["project_id"], p["permissions"]) for p in listed] == [(w.project_id, ["read"])]
    w.cp.set_client_restrictions(actor=w.owner, project_id=w.project_id, enabled=False)
    # Policies still apply with mode off; only unlisted clients become unrestricted.
    assert _error(w.create(token, "still-read-only", artifact_type, session=session))["reason"] == "client_not_permitted"


# -- CP7: changes take effect on the next request --------------------------------------


def test_policy_changes_apply_to_existing_sessions_and_tokens(w):  # CP7
    token = w.tokens_for(w.claude)["access_token"]
    session = w.session(token)
    assert w.create(token, "one", session=session).get("isError") is not True
    w.policy(w.claude, ["read"])
    assert _error(w.create(token, "two", session=session))["reason"] == "client_not_permitted"
    w.cp.remove_client_policy(actor=w.owner, principal_id=w.owner.principal_id, project_id=w.project_id,
                              client_kind="oauth_client", client_id=w.claude)
    assert w.create(token, "three", "database_decision", session=session).get("isError") is not True
    w.policy(w.claude, [])  # an empty policy: no access through this client
    assert _error(w.tool(token, "mak4i_get_current", {"project": w.project_id}, session=session))["code"] == "access_denied"


# -- CP8: refresh tokens are bound to their client --------------------------------------


def test_refresh_token_presented_by_another_client_fails_and_revokes(w):  # CP8
    tokens = w.tokens_for(w.claude)
    stolen = w.refresh(tokens["refresh_token"], client_id=w.chatgpt)
    assert stolen.status_code == 400 and stolen.json()["error"] == "invalid_grant"
    again = w.refresh(tokens["refresh_token"], client_id=w.claude)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"  # authorization revoked
    initialize = w.mcp(tokens["access_token"], "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"},
    })
    assert initialize.status_code == 401  # its access token died with the authorization


# -- CP9: denials stay uniform for principals without a grant ---------------------------


def test_principal_without_grant_learns_nothing(w):  # CP9
    outsider = w.cp.create_principal(actor=w.owner, organization_id=w.org.organization_id,
                                     type="human", display_name="Outsider")
    w.cp.set_client_restrictions(actor=w.owner, project_id=w.project_id, enabled=True)
    token = w.tokens_for(w.chatgpt, principal_id=outsider.principal_id)["access_token"]
    error = _error(w.tool(token, "mak4i_get_current", {"project": w.project_id}))
    assert error["code"] == "access_denied" and "reason" not in error
    unknown = _error(w.tool(token, "mak4i_get_current", {"project": "prj_nope"}))
    assert {k: v for k, v in unknown.items() if k != "correlation_id"} == \
        {k: v for k, v in error.items() if k != "correlation_id"}


# -- credentials are clients too ---------------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_credential_is_its_own_client(w, artifact_type):  # CP1, CP4, CP6
    credential, raw = w.cp.issue_credential(actor=w.owner, principal_id=w.owner.principal_id)
    w.policy(credential.credential_id, ["read"], kind="credential")
    assert _error(w.create(raw, "cred-write", artifact_type))["reason"] == "client_not_permitted"
    whoami = w.tool(raw, "mak4i_whoami")["structuredContent"]
    assert whoami["client"] == {"kind": "credential", "id": credential.credential_id, "registration": None}
    other, other_raw = w.cp.issue_credential(actor=w.owner, principal_id=w.owner.principal_id)
    assert w.create(other_raw, "other-cred", artifact_type).get("isError") is not True  # mode off, no policy
    w.cp.set_client_restrictions(actor=w.owner, project_id=w.project_id, enabled=True)
    assert _error(w.create(other_raw, "other-cred-2", artifact_type))["reason"] == "client_not_permitted"


# -- CP11: administration, audit and security events ---------------------------------------


def test_only_owners_administer_and_every_change_is_audited(w):  # CP11
    member = w.cp.create_principal(actor=w.owner, organization_id=w.org.organization_id,
                                   type="human", display_name="Mia")
    with pytest.raises(AccessDeniedError):
        w.cp.set_client_policy(actor=member, principal_id=member.principal_id, project_id=w.project_id,
                               client_kind="oauth_client", client_id=w.chatgpt, permissions=["read"])
    with pytest.raises(AccessDeniedError):
        w.cp.set_client_restrictions(actor=member, project_id=w.project_id, enabled=True)
    w.policy(w.claude, ["read"])
    w.cp.set_client_restrictions(actor=w.owner, project_id=w.project_id, enabled=True)
    w.cp.remove_client_policy(actor=w.owner, principal_id=w.owner.principal_id, project_id=w.project_id,
                              client_kind="oauth_client", client_id=w.claude)
    events = [(e.action, e.outcome) for e in w.cp.list_admin_events(actor=w.owner, organization_id=w.org.organization_id)]
    assert ("client_policy.set", "denied") in events
    assert ("project.client_restrictions", "denied") in events
    assert ("client_policy.set", "succeeded") in events
    assert ("project.client_restrictions", "succeeded") in events
    assert ("client_policy.remove", "succeeded") in events



@pytest.mark.parametrize("artifact_type", TYPES)
def test_refused_write_security_event_carries_the_reason(w, caplog, artifact_type):  # CP11
    from mak4i.api import MAK4IEngine
    from mak4i.audit import AuditLogger
    from mak4i.identity import Authorizer
    from mak4i.identity.auth_context import AuthContext
    from mak4i.store.local_json import LocalJSONStore

    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    audit = AuditLogger()
    engine = MAK4IEngine(LocalJSONStore(w.db.url.database + ".artifacts"),
                         authorizer=Authorizer(w.cp, audit), control_plane=w.cp, audit=audit)
    w.policy(w.claude, ["read"])
    auth = AuthContext.for_scopes(["mak4i:read", "mak4i:write"], oauth_client_id=w.claude,
                                  oauth_authorization_id="oaz_test", client_registration="pre_registered")
    with pytest.raises(AccessDeniedError) as denied:
        engine.create_artifact(principal=w.owner, project=w.project_id, artifact_id="x",
                               artifact_type=artifact_type, title="x", content="x", auth=auth)
    assert denied.value.reason == "client_not_permitted"
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME]
    refused = [e for e in events if e["event"] == "WRITE_REFUSED"]
    assert refused and refused[0]["reason"] == "client_not_permitted"
    assert refused[0]["oauth_client_id"] == w.claude and refused[0]["error_code"] == "access_denied"
    assert any(e["event"] == "ACCESS_DENIED" and e.get("reason") == "client_not_permitted" for e in events)


# -- memory store parity -------------------------------------------------------------------


def test_in_memory_store_enforces_the_same_ceiling():
    cp = ControlPlane(InMemoryControlPlaneStore())
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P")
    cp.grant(actor=owner, principal_id=owner.principal_id, project_id=project.project_id,
             permissions=["read", "write"])
    cp.client_registry = {"mak4c_a": {"kind": "pre_registered", "organization_id": org.organization_id}}.get
    client = ("oauth_client", "mak4c_a")
    assert cp.client_ceiling(owner, project.project_id, client) is None
    cp.set_client_policy(actor=owner, principal_id=owner.principal_id, project_id=project.project_id,
                         client_kind="oauth_client", client_id="mak4c_a", permissions=["read"])
    assert cp.client_ceiling(owner, project.project_id, client) == frozenset({"read"})
    assert cp.client_ceiling(owner, project.project_id, None) is None  # operator tooling
    cp.set_client_restrictions(actor=owner, project_id=project.project_id, enabled=True)
    assert cp.client_ceiling(owner, project.project_id, ("oauth_client", "mak4c_b")) == frozenset()


# -- CLI (real executable) ---------------------------------------------------------------------


def test_cli_client_policy_lifecycle_and_restriction_preview(admin):  # CP11
    ids = admin.ids
    owner, project = ids["owner"], ids["p1"]
    admin.ok("grant", "create", "--actor", owner, "--principal-id", owner, "--project-id", project,
             "--permissions", "read,write")
    client_id, _ = admin.register_client(owner, "ChatGPT")
    credential_id, _token = admin.issue_credential(owner, owner)

    policy = admin.ok("client-policy", "set", "--actor", owner, "--principal-id", owner, "--project-id", project,
                      "--oauth-client-id", client_id, "--permissions", "read,write")
    assert policy["client"] == {"kind": "oauth_client", "id": client_id}
    assert policy["permissions"] == ["read", "write"]
    listed = admin.ok("client-policy", "list", "--actor", owner, "--project-id", project)["result"]
    assert [p["client"]["id"] for p in listed] == [client_id]

    # Preview: the credential has no policy and would lose access, so --on is refused without --yes.
    shown = admin.ok("project", "client-restrictions", "--actor", owner, "--project-id", project)
    assert shown["mode"] == "off"
    assert any(c["client"] == {"kind": "credential", "id": credential_id} and c["policy"] is None
               for c in shown["clients"])
    refused = admin.run("project", "client-restrictions", "--actor", owner, "--project-id", project, "--on")
    admin.runs.pop()  # refused on purpose; judged here
    assert refused.returncode == 1 and "would lose all access" in refused.stderr
    assert admin.ok("project", "client-restrictions", "--actor", owner, "--project-id", project)["mode"] == "off"

    admin.ok("client-policy", "set", "--actor", owner, "--principal-id", owner, "--project-id", project,
             "--credential-id", credential_id, "--permissions", "none")
    switched = admin.run("project", "client-restrictions", "--actor", owner, "--project-id", project, "--on")
    admin.runs.pop()
    assert switched.returncode == 1  # the credential's empty policy still means no access
    on = admin.ok("project", "client-restrictions", "--actor", owner, "--project-id", project, "--on", "--yes")
    assert on["mode"] == "on"

    removed = admin.ok("client-policy", "remove", "--actor", owner, "--principal-id", owner,
                       "--project-id", project, "--credential-id", credential_id)
    assert removed["removed"] is True
    member = ids["member"]
    admin.denied("client-policy", "set", "--actor", member, "--principal-id", member, "--project-id", project,
                 "--oauth-client-id", client_id, "--permissions", "read")
    actions = {(e["action"], e["outcome"]) for e in admin.events()}
    assert {("client_policy.set", "succeeded"), ("client_policy.remove", "succeeded"),
            ("project.client_restrictions", "succeeded"), ("client_policy.set", "denied")} <= actions


# -- change feed and MCP resources honor the client ceiling (MAK-0008 §14) ----------------------


def test_change_feed_and_resources_honor_the_client_ceiling(w):  # CP7, CP10
    from mak4i.changes.notify import AccessChecker, current_uri
    from mak4i.identity.auth_context import AuthContext

    token = w.tokens_for(w.claude)["access_token"]
    session = w.session(token)
    assert w.create(token, "seen", session=session).get("isError") is not True
    changes = w.tool(token, "mak4i_changes", {"project": w.project_id, "after": 0}, session=session)
    assert [e["kind"] for e in changes["structuredContent"]["events"]] == ["record.created"]

    w.policy(w.claude, [])
    refused = _error(w.tool(token, "mak4i_changes", {"project": w.project_id, "after": 0}, session=session))
    assert refused["reason"] == "client_not_permitted"
    uri = current_uri(w.org.organization_id, w.project_id)
    read = w.mcp(token, "resources/read", {"uri": uri}, session=session, request_id=7)
    assert "error" in json.loads(read.text.split("data:", 1)[-1]) if "data:" in read.text else read.json()
    listed = w.mcp(token, "resources/list", {}, session=session, request_id=8)
    body = json.loads(listed.text.split("data:", 1)[-1]) if "data:" in listed.text else listed.json()
    assert body["result"]["resources"] == []

    checker = AccessChecker(w.cp)
    claude = AuthContext.for_scopes(["mak4i:read"], oauth_client_id=w.claude, oauth_authorization_id="oaz_x")
    chatgpt = AuthContext.for_scopes(["mak4i:read"], oauth_client_id=w.chatgpt, oauth_authorization_id="oaz_y")
    assert not checker.can_read(w.owner, claude, w.org.organization_id, w.project_id)  # no notifications
    assert checker.can_read(w.owner, chatgpt, w.org.organization_id, w.project_id)
