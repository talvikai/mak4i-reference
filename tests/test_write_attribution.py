"""Write attribution and operational security logging across every
supported path (MAK-0006 §7, §8.6; MAK-0008 §9.4):

- the real `mak4i` CLI executable (subprocess; events on stderr, stdout
  stays valid JSON);
- a real stdio MCP server process (`python -m mak4i.mcp_server`);
- MCP over HTTP with a principal credential and with OAuth (in process).

Covers successful, refused and failed writes, resolution lifecycle and
recovery, grant/scope denials, identity mismatch, credential revocation,
unknown tokens, and checks every captured log for secret leakage. Two
artifact types run through the same code (CLAUDE.md testing discipline)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from mak4i.audit import LOGGER_NAME
from mak4i.identity.tokens import hash_token

TYPES = ["caching_decision", "database_decision"]
SECRET_CONTENT = "SECRET-RECORD-CONTENT-7f3a"
SRC = Path(__file__).resolve().parents[1] / "src"


# -- helpers -------------------------------------------------------------------


def _events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _assert_no_secrets(text: str, *secrets: str) -> None:
    for secret in secrets:
        if secret:
            assert secret not in text, f"secret material leaked into the log: {secret[:12]}…"
    assert SECRET_CONTENT not in text, "record content leaked into the log"


def _env(tmp_path: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAK4I_")}
    env.update(
        MAK4I_HOME=str(tmp_path / ".mak4i"),
        MAK4I_STORE="local",
        MAK4I_LOCAL_STORE_DIR=str(tmp_path / "artifacts"),
        MAK4I_CONTROL_PLANE_DB=f"sqlite:///{tmp_path / 'control-plane.db'}",
        MAK4I_CONTROL_PLANE_CREATE_TABLES="1",
        # The installed entry points import this checkout's source, not a
        # possibly stale non-editable install.
        PYTHONPATH=os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")])),
    )
    return env


def _mak4i_executable() -> list[str]:
    script = Path(sys.executable).with_name("mak4i")
    return [str(script)] if script.exists() else [sys.executable, "-m", "mak4i.cli"]


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """One organization with an owner (read/write/resolve), a read-only
    human and an agent (read/write) holding a credential, all in a SQLite
    control plane that the subprocesses share."""
    env = _env(tmp_path)
    for key, value in env.items():
        if key.startswith("MAK4I_"):
            monkeypatch.setenv(key, value)
    from mak4i.config import build_control_plane_from_env

    cp = build_control_plane_from_env()
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P")
    cp.grant(actor=owner, principal_id=owner.principal_id, project_id=project.project_id,
             permissions=["read", "write", "resolve"])
    reader = cp.create_principal(actor=owner, organization_id=org.organization_id, type="human",
                                 display_name="Reader")
    cp.grant(actor=owner, principal_id=reader.principal_id, project_id=project.project_id, permissions=["read"])
    agent = cp.create_principal(actor=owner, organization_id=org.organization_id, type="agent",
                                display_name="Red bot", agent_id="red-bot")
    cp.grant(actor=owner, principal_id=agent.principal_id, project_id=project.project_id,
             permissions=["read", "write"])
    credential, token = cp.issue_credential(actor=owner, principal_id=agent.principal_id)
    return {
        "env": env, "cp": cp, "org": org.organization_id, "project": project.project_id,
        "owner": owner, "reader": reader, "agent": agent, "credential": credential, "token": token,
    }


# -- the real CLI executable --------------------------------------------------


def _cli(inst, *args):
    return subprocess.run([*_mak4i_executable(), *args], env=inst["env"], capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("artifact_type", TYPES)
def test_cli_subprocess_attributes_successful_refused_and_denied_writes(installation, artifact_type):
    inst = installation
    owner, reader, project = inst["owner"].principal_id, inst["reader"].principal_id, inst["project"]
    create = ["create", "--project", project, "--artifact-id", "cache-1", "--artifact-type", artifact_type,
              "--title", "Cache", "--content", SECRET_CONTENT]

    ok = _cli(inst, *create, "--principal", owner)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["artifact_id"] == "cache-1"  # stdout stays machine-readable
    (created,) = [e for e in _events(ok.stderr) if e["event"] == "CREATE"]
    assert created["principal_id"] == owner and created["auth_method"] == "operator"
    assert created["artifact_id"] == "cache-1" and created["correlation_id"]

    duplicate = _cli(inst, *create, "--principal", owner)
    assert duplicate.returncode != 0 and not duplicate.stdout.strip()
    (refused,) = [e for e in _events(duplicate.stderr) if e["event"] == "WRITE_REFUSED"]
    assert refused["error_code"] == "artifact_exists" and refused["principal_id"] == owner
    assert refused["operation"] == "create" and refused["project_id"] == project

    denied = _cli(inst, *[a if a != "cache-1" else "cache-2" for a in create], "--principal", reader)
    assert denied.returncode != 0
    events = _events(denied.stderr)
    assert any(e["event"] == "ACCESS_DENIED" and e["principal_id"] == reader for e in events)
    (refused,) = [e for e in events if e["event"] == "WRITE_REFUSED"]
    assert refused["error_code"] == "access_denied" and refused["principal_id"] == reader

    supersede = ["supersede", "--project", project, "--old-id", "cache-1", "--content", SECRET_CONTENT]
    blank = _cli(inst, *supersede, "--reason", "  ", "--principal", owner)
    assert [e["error_code"] for e in _events(blank.stderr) if e["event"] == "WRITE_REFUSED"] == ["validation_error"]

    done = _cli(inst, *supersede, "--reason", "newer\x1b[31m guidance\nINJECTED", "--principal", owner)
    assert done.returncode == 0, done.stderr
    json.loads(done.stdout)
    (superseded,) = [e for e in _events(done.stderr) if e["event"] == "SUPERSEDE"]
    assert superseded["principal_id"] == owner
    assert "\x1b" not in done.stderr and "\nINJECTED" not in done.stderr  # untrusted reason sanitized

    stale = _cli(inst, *supersede, "--reason", "again", "--principal", owner)
    assert [e["error_code"] for e in _events(stale.stderr) if e["event"] == "WRITE_REFUSED"] == ["artifact_not_active"]

    for result in (ok, duplicate, denied, blank, done, stale):
        _assert_no_secrets(result.stderr, inst["token"], hash_token(inst["token"]))


def test_cli_audit_log_can_be_redirected_but_not_disabled(installation, tmp_path):  # MAK-0006 §8.6
    inst = installation
    args = ["get-current", "--project", inst["project"], "--principal", inst["owner"].principal_id]
    path = tmp_path / "audit.jsonl"
    inst["env"]["MAK4I_AUDIT_LOG"] = str(path)
    to_file = _cli(inst, *args)
    assert to_file.returncode == 0 and not _events(to_file.stderr)
    json.loads(to_file.stdout)
    assert any(e["event"] == "GET_CURRENT" for e in _events(path.read_text()))
    for value in ("off", "OFF", "none", "0"):
        inst["env"]["MAK4I_AUDIT_LOG"] = value
        refused = _cli(inst, *args)
        assert refused.returncode != 0 and "can't disable audit events" in refused.stderr
        assert not refused.stdout.strip()


def test_server_log_level_never_silences_security_events(capfd):  # MAK-0006 §8.6
    import logging as _logging

    from mak4i.audit import AuditLogger
    from mak4i.mcp_server import configure_audit_logging

    root = _logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        configure_audit_logging("ERROR")
        AuditLogger().log("WRITE_REFUSED", correlation_id="c-1", actor="prn_x", error_code="access_denied")
        _logging.getLogger("mak4i.diagnostics").info("diagnostic noise")
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        for handler in saved[0]:
            root.addHandler(handler)
        root.setLevel(saved[1])
    err = capfd.readouterr().err
    assert '"event": "WRITE_REFUSED"' in err and "diagnostic noise" not in err


# -- a real stdio server process ------------------------------------------------


def test_stdio_process_attributes_writes_and_logs_revocation(installation, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    inst = installation
    env = {**inst["env"], "MAK4I_TOKEN": inst["token"], "MAK4I_TRANSPORT": "stdio"}
    errlog_path = tmp_path / "stdio-stderr.log"
    project = inst["project"]
    create = {"project": project, "artifact_id": "s-1", "artifact_type": "database_decision",
              "title": "DB", "content": SECRET_CONTENT}
    results = {}

    async def session():
        params = StdioServerParameters(command=sys.executable, args=["-m", "mak4i.mcp_server"], env=env)
        with errlog_path.open("w") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    results["ok"] = await client.call_tool("mak4i_create", create)
                    results["dup"] = await client.call_tool("mak4i_create", create)
                    results["mismatch"] = await client.call_tool("mak4i_create", {**create, "artifact_id": "s-2",
                                                                                  "agent_id": "someone-else"})
                    inst["cp"].revoke_credential(actor=inst["owner"], credential_id=inst["credential"].credential_id)
                    results["revoked"] = await client.call_tool("mak4i_create", {**create, "artifact_id": "s-3"})

    anyio.run(session)
    log = errlog_path.read_text()
    events = _events(log)
    agent, credential_id = inst["agent"].principal_id, inst["credential"].credential_id

    assert not results["ok"].is_error
    startup = [e for e in events if e["event"] == "AUTHENTICATE_SUCCESS"]
    assert startup and startup[0]["principal_id"] == agent and startup[0]["credential_id"] == credential_id
    (created,) = [e for e in events if e["event"] == "CREATE"]
    assert created["principal_id"] == agent and created["agent_id"] == "red-bot"
    assert created["credential_id"] == credential_id and created["auth_method"] == "credential"

    refused = {e["error_code"]: e for e in events if e["event"] == "WRITE_REFUSED"}
    assert set(refused) == {"artifact_exists", "identity_mismatch"}
    for code, result in (("artifact_exists", results["dup"]), ("identity_mismatch", results["mismatch"])):
        error = result.structured_content["error"]
        assert error["code"] == code and refused[code]["correlation_id"] == error["correlation_id"]
        assert refused[code]["credential_id"] == credential_id and refused[code]["agent_id"] == "red-bot"

    assert results["revoked"].structured_content["error"]["code"] == "access_denied"
    failure = [e for e in events if e["event"] == "AUTHENTICATE_FAILURE"][-1]
    assert failure["reason"] == "revoked" and failure["credential_id"] == credential_id
    _assert_no_secrets(log, inst["token"], hash_token(inst["token"]))


# -- MCP over HTTP: credential and OAuth -----------------------------------------


def _http_world(tmp_path):
    from mak4i.api import MAK4IEngine
    from mak4i.audit import AuditLogger
    from mak4i.config import build_resolution_store
    from mak4i.identity import Authorizer
    from mak4i.mcp_server import build_http_app, build_server
    from mak4i.store.local_json import LocalJSONStore
    from tests.test_oauth import ORIGIN, World

    w = World(tmp_path)
    audit = AuditLogger()
    w.engine = MAK4IEngine(LocalJSONStore(tmp_path / "artifacts"), audit=audit,
                           authorizer=Authorizer(w.cp, audit), control_plane=w.cp,
                           resolutions=build_resolution_store(w.cp))
    w.app = build_http_app(build_server(w.engine), control_plane=w.cp, oauth=w.oauth, audit=audit)
    w.client = type(w.client)(w.app, base_url=ORIGIN)
    w.cp.grant(actor=w.owner, principal_id=w.owner.principal_id, project_id=w.project.project_id,
               permissions=["read", "write", "resolve"])
    return w


def _caplog_text(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.name == LOGGER_NAME)


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("method", ["credential", "oauth"])
def test_http_writes_are_attributed_for_both_auth_methods(tmp_path, caplog, artifact_type, method):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    w = _http_world(tmp_path)
    with w.client:
        if method == "credential":
            credential, token = w.cp.issue_credential(actor=w.owner, principal_id=w.owner.principal_id)
            ident = ("credential_id", credential.credential_id)
            secrets = [token, hash_token(token)]
        else:
            issued = w.tokens(scope="mak4i:read mak4i:write")
            token = issued["access_token"]
            ident = ("oauth_client_id", None)
            secrets = [token, issued.get("refresh_token")]
        project = w.project.project_id
        args = {"project": project, "artifact_id": "h-1", "artifact_type": artifact_type,
                "title": "T", "content": SECRET_CONTENT}
        _h, ok = w.call_tool(token, "mak4i_create", args)
        _h, dup = w.call_tool(token, "mak4i_create", args)
        _h, sup = w.call_tool(token, "mak4i_supersede", {"project": project, "old_id": "h-1",
                                                         "content": SECRET_CONTENT, "reason": "r"})
    assert not ok["result"]["isError"] and not sup["result"]["isError"]
    events = _events(_caplog_text(caplog))
    for name in ("CREATE", "SUPERSEDE"):
        (event,) = [e for e in events if e["event"] == name]
        assert event["principal_id"] == w.owner.principal_id
        assert event["auth_method"] == method and event["project_id"] == project
        assert event.get(ident[0]) == ident[1] if ident[1] else event.get(ident[0])
    (refused,) = [e for e in events if e["event"] == "WRITE_REFUSED"]
    assert refused["error_code"] == "artifact_exists"
    assert refused["correlation_id"] == dup["result"]["structuredContent"]["error"]["correlation_id"]
    _assert_no_secrets(_caplog_text(caplog), *secrets)


def test_unknown_tokens_are_never_attributed(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    w = _http_world(tmp_path)
    with w.client:
        for token in ("mak4i_definitely-not-issued", "mak4i_at_definitely-not-issued", "garbage"):
            assert w.mcp(token, "tools/list", {}).status_code == 401
    failures = [e for e in _events(_caplog_text(caplog)) if e["event"].startswith("AUTHENTICATE_FAILURE")]
    assert failures
    for event in failures:
        assert event.get("principal_id") is None and event.get("credential_id") is None
        assert event["actor"] == "unauthenticated"
    _assert_no_secrets(_caplog_text(caplog), "definitely-not-issued")


@pytest.mark.parametrize("artifact_type", TYPES)
def test_unexpected_failure_is_a_write_failed_event(tmp_path, caplog, artifact_type, monkeypatch):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    w = _http_world(tmp_path)

    def broken_put_new(*_a, **_k):
        raise RuntimeError("disk on fire /srv/secret/path")

    monkeypatch.setattr(w.engine._store, "put_new", broken_put_new)
    credential, token = w.cp.issue_credential(actor=w.owner, principal_id=w.owner.principal_id)
    with w.client:
        _h, rpc = w.call_tool(token, "mak4i_create", {"project": w.project.project_id, "artifact_id": "f-1",
                                                     "artifact_type": artifact_type, "title": "T", "content": "x"})
    error = rpc["result"]["structuredContent"]["error"]
    assert error["code"] == "internal_error" and "disk on fire" not in error["message"]
    events = _events(_caplog_text(caplog))
    (failed,) = [e for e in events if e["event"] == "WRITE_FAILED"]
    assert failed["error_code"] == "internal_error" and failed["principal_id"] == w.owner.principal_id
    assert failed["credential_id"] == credential.credential_id
    assert failed["correlation_id"] == error["correlation_id"]
    assert not [e for e in events if e["event"] == "WRITE_REFUSED"]


# -- resolution recovery -----------------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_recovered_resolutions_are_logged_with_their_actor(tmp_path, caplog, artifact_type, monkeypatch):
    from mak4i.api import MAK4IEngine
    from mak4i.audit import AuditLogger
    from mak4i.identity import Authorizer, ControlPlane
    from mak4i.identity.memory_store import InMemoryControlPlaneStore
    from mak4i.resolution.records import InMemoryResolutionStore
    from mak4i.store.local_json import LocalJSONStore

    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    cp = ControlPlane(InMemoryControlPlaneStore())
    audit = AuditLogger()
    resolutions = InMemoryResolutionStore()
    engine = MAK4IEngine(LocalJSONStore(tmp_path / "a"), audit=audit, authorizer=Authorizer(cp, audit),
                         control_plane=cp, resolutions=resolutions)
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P").project_id
    cp.grant(actor=owner, principal_id=owner.principal_id, project_id=project, permissions=["read", "write", "resolve"])
    for artifact_id in ("a", "b"):
        engine.create_artifact(principal=owner, project=project, artifact_id=artifact_id, artifact_type=artifact_type,
                               title=artifact_id, content=artifact_id, subject_key="primary")
    (conflict,) = engine.get_current(principal=owner, project=project).conflicts

    class Crash(BaseException):
        pass

    def crash(*_a, **_k):
        raise Crash()

    monkeypatch.setattr(engine, "_apply_resolution", crash)
    with pytest.raises(Crash):
        engine.resolve_conflict(principal=owner, project=project, conflict_id=conflict.conflict_id,
                                candidate_artifact_ids=["a", "b"], action="select_winner",
                                winner_artifact_id="a", reason="pick a")
    monkeypatch.undo()
    (recovered,) = engine.recover_pending_resolutions()
    assert recovered.state == "completed"
    (event,) = [e for e in _events(_caplog_text(caplog)) if e["event"] == "RESOLUTION_RECOVERED"]
    assert event["actor"] == owner.principal_id and event["outcome"] == "completed"
    assert event["resolution_id"] == recovered.resolution_id and event["project_id"] == project
