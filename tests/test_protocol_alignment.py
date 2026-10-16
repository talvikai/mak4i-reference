"""Alignment with protocol PR #7 (MAK-0006 §3.7, §6.4, §7.6, §8.5, §8.6;
MAK-0008 §4.2, §9, §10.2, §10.5): closed tool inputs, identity claims,
`artifact_exists`, correlation ids, refused-write security events,
`whoami.protocol`, stdio revocation, operator admin events.

Exercised through the real HTTP app composed as `mcp_server.main` does
(audit logger on the engine and authorizer)."""

from __future__ import annotations

import json
import logging

import anyio
import pytest
from mcp.client import Client

from mak4i.api import MAK4IEngine
from mak4i.audit import LOGGER_NAME, AuditLogger
from mak4i.config import build_resolution_store
from mak4i.identity import Authorizer, ControlPlane
from mak4i.mcp_server import build_http_app, build_server, current_auth, current_principal
from mak4i.store.local_json import LocalJSONStore
from tests.test_oauth import ORIGIN, World

TYPES = ["caching_decision", "database_decision"]


@pytest.fixture
def world(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    w = World(tmp_path)
    audit = AuditLogger()
    engine = MAK4IEngine(
        LocalJSONStore(tmp_path / "artifacts"), audit=audit, authorizer=Authorizer(w.cp, audit),
        control_plane=w.cp, resolutions=build_resolution_store(w.cp),
    )
    w.engine = engine
    w.app = build_http_app(build_server(engine), control_plane=w.cp, oauth=w.oauth, audit=audit)
    w.client = type(w.client)(w.app, base_url=ORIGIN)
    w.cp.grant(actor=w.owner, principal_id=w.owner.principal_id, project_id=w.project.project_id,
               permissions=["read", "write", "resolve"])
    agent = w.cp.create_principal(actor=w.owner, organization_id=w.org.organization_id, type="agent",
                                  display_name="Red bot", agent_id="red-bot")
    w.cp.grant(actor=w.owner, principal_id=agent.principal_id, project_id=w.project.project_id,
               permissions=["read", "write"])
    w.agent = agent
    w.agent_credential, w.agent_token = w.cp.issue_credential(actor=w.owner, principal_id=agent.principal_id)
    with w.client:
        yield w


def _events(caplog, name=None):
    out = []
    for record in caplog.records:
        if record.name != LOGGER_NAME:
            continue
        try:
            data = json.loads(record.getMessage())
        except ValueError:
            continue
        if name is None or data.get("event") == name:
            out.append(data)
    return out


def _call(w, token, tool, args):
    _headers, rpc = w.call_tool(token, tool, args)
    return rpc["result"]


def _create_args(w, artifact_id, artifact_type, **extra):
    return {"project": w.project.project_id, "artifact_id": artifact_id, "artifact_type": artifact_type,
            "title": "Cache", "content": "Use RED", "subject_key": "primary", **extra}


def test_whoami_reports_protocol_and_scopes_key(world):  # M37, MAK-0008 §10.5
    result = _call(world, world.agent_token, "mak4i_whoami", {})["structuredContent"]
    assert result["protocol"]["version"] == "0.3"
    assert "server-project-records" in result["protocol"]["profiles"]
    assert "scopes" in result and result["scopes"] is None


@pytest.mark.parametrize("artifact_type", TYPES)
def test_unknown_arguments_are_validation_errors_and_audited(world, artifact_type, caplog):  # M35, I22
    result = _call(world, world.agent_token, "mak4i_create",
                   _create_args(world, "x1", artifact_type, x_extra="nope"))
    error = result["structuredContent"]["error"]
    assert result["isError"] and error["code"] == "validation_error"
    refused = _events(caplog, "WRITE_REFUSED")[-1]
    assert refused["error_code"] == "validation_error" and refused["operation"] == "create"
    assert refused["principal_id"] == world.agent.principal_id and refused["agent_id"] == "red-bot"
    assert refused["credential_id"] == world.agent_credential.credential_id
    assert refused["correlation_id"] == error["correlation_id"]
    assert world.engine._store.get(world.org.organization_id, world.project.project_id, "x1") is None


def test_differing_identity_claim_is_identity_mismatch(world, caplog):  # I3, M35
    result = _call(world, world.agent_token, "mak4i_create",
                   _create_args(world, "x2", "caching_decision", agent_id="someone-else"))
    assert result["structuredContent"]["error"]["code"] == "identity_mismatch"
    assert _events(caplog, "WRITE_REFUSED")[-1]["error_code"] == "identity_mismatch"


@pytest.mark.parametrize(
    "tool,args",
    [
        ("mak4i_search", {"status": "deleted"}),
        ("mak4i_search", {"status": None}),
        ("mak4i_get_conflict", {"conflict_id": "cfl_not-a-hash"}),
        ("mak4i_list_conflicts", {"state": "everything"}),
        ("mak4i_get_current", {"tags": [""]}),
    ],
)
def test_input_constraints_are_enforced(world, tool, args):  # MAK-0008 §10.2, §10.5a
    result = _call(world, world.agent_token, tool, {"project": world.project.project_id, **args})
    assert result["structuredContent"]["error"]["code"] == "validation_error"


def test_advertised_input_schemas_are_closed(world):  # MAK-0008 §10.2
    async def schemas():
        server = build_server(world.engine)
        return {t.name: t.input_schema for t in await server.list_tools()}

    tools = anyio.run(schemas)
    assert all(schema.get("additionalProperties") is False for schema in tools.values())
    assert tools["mak4i_search"]["properties"]["status"]["enum"] == ["active", "superseded", "withdrawn"]
    assert "enum" in json.dumps(tools["mak4i_resolve_conflict"]["properties"]["action"])


@pytest.mark.parametrize("artifact_type", TYPES)
def test_duplicate_create_is_artifact_exists(world, artifact_type, caplog):  # P16, MAK-0008 §9
    assert not _call(world, world.agent_token, "mak4i_create", _create_args(world, "dup", artifact_type))["isError"]
    result = _call(world, world.agent_token, "mak4i_create", _create_args(world, "dup", artifact_type))
    assert result["structuredContent"]["error"]["code"] == "artifact_exists"
    assert _events(caplog, "WRITE_REFUSED")[-1]["error_code"] == "artifact_exists"


def test_engine_refusals_share_the_error_correlation_id(world, caplog):  # MAK-0008 §9.4
    _call(world, world.agent_token, "mak4i_create", _create_args(world, "head", "caching_decision"))
    result = _call(world, world.agent_token, "mak4i_supersede",
                   {"project": world.project.project_id, "old_id": "missing", "content": "x", "reason": "r"})
    error = result["structuredContent"]["error"]
    related = [e for e in _events(caplog) if e.get("correlation_id") == error["correlation_id"]]
    assert {"ACCESS_GRANTED", "WRITE_REFUSED"} <= {e["event"] for e in related}


def test_scope_refusal_is_attributed_to_the_principal(world, caplog):  # I22, MAK-0008 §4.2
    token = world.tokens(scope="mak4i:read")["access_token"]
    response, _rpc = world.call_tool(token, "mak4i_create", _create_args(world, "x3", "caching_decision"))
    assert response.status_code == 403
    denied = [e for e in _events(caplog, "ACCESS_DENIED") if e.get("reason") == "insufficient_scope"][-1]
    assert denied["principal_id"] == world.owner.principal_id and denied["tool"] == "mak4i_create"
    assert denied["oauth_client_id"] and denied["required_scopes"] == ["mak4i:write"]
    refused = _events(caplog, "WRITE_REFUSED")[-1]
    assert refused["error_code"] == "insufficient_scope" and refused["auth_method"] == "oauth"
    assert not [e for e in _events(caplog, "AUTHENTICATE_FAILURE") if e.get("reason") == "insufficient_scope"]


def test_revoked_credential_failure_names_the_credential(world, caplog):  # MAK-0006 §8.6
    world.cp.revoke_credential(actor=world.owner, credential_id=world.agent_credential.credential_id)
    response = world.mcp(world.agent_token, "tools/list", {})
    assert response.status_code == 401
    failure = _events(caplog, "AUTHENTICATE_FAILURE")[-1]
    assert failure["reason"] == "revoked" and failure["credential_id"] == world.agent_credential.credential_id


def test_stdio_session_with_revoked_credential_gets_access_denied(world):  # I17, MAK-0006 §6.4
    from mak4i.identity import CredentialInvalidError
    from mak4i.mcp_server import _stdio_reauthenticate

    server = build_server(world.engine)

    def reauthenticate():
        raise CredentialInvalidError("revoked")

    tokens = (current_principal.set(world.agent), current_auth.set(None), _stdio_reauthenticate.set(reauthenticate))
    try:
        async def call():
            async with Client(server=server) as client:
                return await client.call_tool("mak4i_whoami", {})

        result = anyio.run(call)
    finally:
        _stdio_reauthenticate.reset(tokens[2])
        current_auth.reset(tokens[1])
        current_principal.reset(tokens[0])
    assert result.is_error and result.structured_content["error"]["code"] == "access_denied"


def test_instance_operator_events_have_no_principal(world):  # I18, MAK-0006 §7.6
    from mak4i.identity import ControlPlane
    from mak4i.identity.memory_store import InMemoryControlPlaneStore

    cp = ControlPlane(InMemoryControlPlaneStore())
    org, _owner = cp.onboard_organization(organization_name="Boot", owner_display_name="Owner")
    events = [e.to_protocol() for e in cp.list_admin_events(actor=_owner, organization_id=org.organization_id)]
    bootstrap = [e for e in events if e["actor"]["auth_method"] == "operator" and e["actor"]["principal_id"] is None]
    assert bootstrap, events


@pytest.mark.parametrize(
    "extra",
    [
        {"subject_key": "k" * 513},  # P13
        {"version": "9.0"},  # P9: server-assigned field
        {"bot_id": "red-bot"},  # closed inputs: an unknown identity-like key is just unknown
    ],
)
def test_rejected_create_inputs(world, extra):  # P9, P13, MAK-0008 §10.2
    result = _call(world, world.agent_token, "mak4i_create", _create_args(world, "x4", "database_decision", **extra))
    assert result["structuredContent"]["error"]["code"] == "validation_error"


def test_null_optional_fields_are_treated_as_omitted(world):  # P9
    result = _call(world, world.agent_token, "mak4i_create",
                   _create_args(world, "x5", "database_decision", rationale=None))
    assert not result["isError"]


def test_write_without_grant_is_a_refused_write_event(world, caplog):  # I22, MAK-0006 §8.6
    reader = world.cp.create_principal(actor=world.owner, organization_id=world.org.organization_id, type="human",
                                       display_name="Reader")
    world.cp.grant(actor=world.owner, principal_id=reader.principal_id, project_id=world.project.project_id,
                   permissions=["read"])
    _cred, token = world.cp.issue_credential(actor=world.owner, principal_id=reader.principal_id)
    result = _call(world, token, "mak4i_create", _create_args(world, "x6", "caching_decision"))
    assert result["structuredContent"]["error"]["code"] == "access_denied"
    refused = _events(caplog, "WRITE_REFUSED")[-1]
    assert refused["error_code"] == "access_denied" and refused["principal_id"] == reader.principal_id


def test_credential_mode_challenge_uses_a_realm(tmp_path):  # M1 (OAuth disabled)
    from starlette.testclient import TestClient

    from mak4i.identity.memory_store import InMemoryControlPlaneStore

    cp = ControlPlane(InMemoryControlPlaneStore())
    engine = MAK4IEngine(LocalJSONStore(tmp_path / "a"), authorizer=Authorizer(cp), control_plane=cp)
    with TestClient(build_http_app(build_server(engine), control_plane=cp), base_url="http://127.0.0.1") as client:
        missing = client.post("/mcp", json={})
        bad = client.post("/mcp", json={}, headers={"Authorization": "Bearer mak4i_nope"})
    assert missing.status_code == 401 and missing.headers["www-authenticate"].startswith('Bearer realm="mak4i"')
    assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["www-authenticate"]


def test_forwarded_headers_are_trusted_only_from_configured_proxies():  # M22, MAK-0008 §3.3
    from mak4i.mcp_server import uvicorn_proxy_options

    assert uvicorn_proxy_options([]) == {"proxy_headers": False}  # not even loopback by default
    assert uvicorn_proxy_options(["10.0.0.5", "10.1.0.0/16"]) == {
        "proxy_headers": True, "forwarded_allow_ips": "10.0.0.5,10.1.0.0/16"
    }


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1", "[::1]"])
@pytest.mark.parametrize("declared", [None, "0", "1"])
def test_loopback_binds_never_need_a_declaration(host, declared):  # M18
    from mak4i.mcp_server import plain_http_problem

    env = {} if declared is None else {"MAK4I_TLS_TERMINATED_UPSTREAM": declared}
    assert plain_http_problem(host, env) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.12", "192.168.1.5", "mak4i.example.com"])
def test_plain_http_is_refused_off_loopback_unless_tls_is_terminated_upstream(host):  # M18, MAK-0008 §1.2
    from mak4i.mcp_server import plain_http_problem

    assert "MAK-0008 §1.2" in plain_http_problem(host, {})
    assert "MAK-0008 §1.2" in plain_http_problem(host, {"MAK4I_TLS_TERMINATED_UPSTREAM": "0"})
    assert plain_http_problem(host, {"MAK4I_TLS_TERMINATED_UPSTREAM": "yes"}) == "MAK4I_TLS_TERMINATED_UPSTREAM must be 1 or 0"
    assert plain_http_problem(host, {"MAK4I_TLS_TERMINATED_UPSTREAM": "1"}) is None


def test_server_process_refuses_a_non_loopback_plain_http_bind(tmp_path):  # M18 (real process)
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if not k.startswith("MAK4I_")}
    env.update(
        MAK4I_TRANSPORT="http", MAK4I_HOST="0.0.0.0", MAK4I_PORT="59871",  # refused before binding MAK4I_STORE="local",
        MAK4I_LOCAL_STORE_DIR=str(tmp_path / "a"), MAK4I_CONTROL_PLANE_DB=f"sqlite:///{tmp_path / 'cp.db'}",
        MAK4I_CONTROL_PLANE_CREATE_TABLES="1",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    result = subprocess.run([sys.executable, "-m", "mak4i.mcp_server"], env=env, capture_output=True, text=True,
                            timeout=60)
    assert result.returncode == 2
    assert "refusing plain HTTP on non-loopback address '0.0.0.0'" in result.stderr


def test_profiles_follow_the_deployment_configuration(monkeypatch):  # MAK-0008 §10.5, CONFORMANCE profiles
    from mak4i.mcp_server import _deployment_profiles
    from mak4i.protocol import claimed_profiles

    class OAuth:
        def __init__(self, cimd, dcr):
            self.enable_cimd, self.enable_dcr = cimd, dcr

    base = ["server-project-records", "server-client-policies"]  # MAK-0006 §5.7, always claimed
    assert claimed_profiles("stdio") == base + ["transport-stdio"]
    assert claimed_profiles("stdio", OAuth(True, True)) == base + ["transport-stdio"]
    assert claimed_profiles("http") == base + ["transport-http-credential"]
    assert claimed_profiles("http", OAuth(True, False)) == base + ["transport-http-credential", "transport-http-oauth", "oauth-cimd"]
    assert claimed_profiles("http", OAuth(False, True)) == base + ["transport-http-credential", "transport-http-oauth", "oauth-dcr"]

    for key in [k for k in __import__("os").environ if k.startswith("MAK4I_OAUTH")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("MAK4I_TRANSPORT", "http")
    monkeypatch.setenv("MAK4I_PUBLIC_ENDPOINT", "https://mak4i.example.com/mcp")
    assert "transport-http-oauth" not in _deployment_profiles()  # OAuth off by default
    monkeypatch.setenv("MAK4I_OAUTH_ENABLED", "1")
    assert _deployment_profiles()[len(base) + 1:] == ["transport-http-oauth", "oauth-cimd"]  # CIMD on, DCR off by default
