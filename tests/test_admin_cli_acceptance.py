"""Administration without a portal (MAK-0006 §8; MAK-0008 §8.5): acceptance
evidence through the REAL `mak4i` executable.

Every administrative step runs as a subprocess of the installed `mak4i`
entry point (importing this checkout's source), against one SQLite control
plane and one local artifact store per test. State is bootstrapped only
through the CLI itself (`org create`, `project create`, ...); the only
in-process step is seeding an OAuth authorization, which otherwise needs a
browser (the revocations themselves always go through the CLI).

Areas (see docs/ADMIN_CLI.md for the command inventory):

1. provisioning     - org/project/principal/grant/credential, access provision
2. disabling        - principal deactivate, project archive, org suspend /
                      reactivate, OAuth client disable
3. grants           - create, list, revoke and their effect on reads/writes
4. revocation       - credentials, sign-in codes, OAuth authorizations,
                      instance-wide OAuth revocation
5. boundaries       - members and other organizations' owners are refused
                      every administrative mutation, durably recorded as
                      `denied`, with nothing changed

Throughout: successful JSON commands print parseable JSON on stdout,
refusals exit non-zero with nothing on stdout, and no raw token, token
hash, sign-in code or client secret appears anywhere except the one
stdout that issued it. Two artifact types run through the same code
(CLAUDE.md testing discipline).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mak4i.identity.tokens import hash_token

TYPES = ["caching_decision", "database_decision"]
SRC = Path(__file__).resolve().parents[1] / "src"
ENDPOINT = "https://mak4i.example.test/mcp"
REDIRECT = "http://127.0.0.1:8765/callback"


def _mak4i_executable() -> list[str]:
    script = Path(sys.executable).with_name("mak4i")
    return [str(script)] if script.exists() else [sys.executable, "-m", "mak4i.cli"]


def _env(root: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAK4I_")}
    env.update(
        MAK4I_HOME=str(root / ".mak4i"),
        MAK4I_STORE="local",
        MAK4I_LOCAL_STORE_DIR=str(root / "artifacts"),
        MAK4I_CONTROL_PLANE_DB=f"sqlite:///{root / 'control-plane.db'}",
        MAK4I_CONTROL_PLANE_CREATE_TABLES="1",
        MAK4I_AUDIT_LOG=str(root / "audit.jsonl"),
        # OAuth administration needs the OAuth service configured.
        MAK4I_OAUTH_ENABLED="1",
        MAK4I_PUBLIC_ENDPOINT=ENDPOINT,
        # Subprocesses import this checkout's source, not a stale install.
        PYTHONPATH=os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")])),
    )
    return env


_AUTHENTICATE = """
import sys
from mak4i.config import build_control_plane_from_env
from mak4i.identity import CredentialInvalidError
try:
    principal = build_control_plane_from_env().authenticate(sys.stdin.read().strip())
    print("ok", principal.principal_id)
except CredentialInvalidError as exc:
    print("invalid", exc.reason)
"""


class Admin:
    """One installation: its environment, every CLI run, and every secret
    the CLI handed out (with the run that printed it)."""

    def __init__(self, root: Path):
        self.root = root
        self.env = _env(root)
        self.runs: list[subprocess.CompletedProcess] = []
        self.secrets: dict[str, int | None] = {}  # secret -> index of the run that printed it
        self.ids: dict[str, str] = {}

    # -- running the executable -------------------------------------------------

    def run(self, *args: str, **env_overrides: str) -> subprocess.CompletedProcess:
        env = {**self.env, **env_overrides}
        result = subprocess.run(
            [*_mak4i_executable(), *args], env=env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=120,
        )
        self.runs.append(result)
        return result

    def ok(self, *args: str, **env) -> object:
        """A successful JSON command: exit 0, stdout is JSON."""
        result = self.run(*args, **env)
        assert result.returncode == 0, (args, result.stderr)
        assert "Traceback" not in result.stderr
        return json.loads(result.stdout)

    def text(self, *args: str, **env) -> str:
        """A successful human-text command."""
        result = self.run(*args, **env)
        assert result.returncode == 0, (args, result.stderr)
        return result.stdout

    def refused(self, *args: str, code: int = 1, **env) -> subprocess.CompletedProcess:
        result = self.run(*args, **env)
        assert result.returncode == code, (args, result.returncode, result.stdout, result.stderr)
        assert not result.stdout.strip(), (args, result.stdout)
        assert "Traceback" not in result.stderr, result.stderr
        return result

    def denied(self, *args: str, **env) -> subprocess.CompletedProcess:
        result = self.refused(*args, **env)
        assert "access denied" in result.stderr, result.stderr
        return result

    # -- secrets ----------------------------------------------------------------

    def _remember(self, *secrets: str) -> None:
        for secret in secrets:
            self.secrets[secret] = len(self.runs) - 1
            self.secrets[hash_token(secret)] = None  # a hash is never printed, even once

    def issue_credential(self, actor: str, principal_id: str, *extra: str) -> tuple[str, str]:
        out = self.text("credential", "issue", "--actor", actor, "--principal-id", principal_id, *extra)
        credential_id = re.search(r"^credential_id: (\S+)$", out, re.M).group(1)
        token = re.search(r"^Authorization: Bearer (\S+)$", out, re.M).group(1)
        assert "It will not be shown again" in out
        self._remember(token)
        return credential_id, token

    def sign_in_code(self, actor: str, principal_id: str | None = None) -> str:
        args = ["oauth", "sign-in-code", "--actor", actor]
        if principal_id:
            args += ["--principal-id", principal_id]
        out = self.text(*args)
        code = re.search(r"^sign-in code: (\S+)$", out, re.M).group(1)
        assert re.fullmatch(r"[A-Z0-9]{4}(-[A-Z0-9]{4})+", code)
        assert "will not be shown again" in out
        raw = code.replace("-", "")
        self._remember(code)
        self.secrets[raw] = self.secrets[code]
        self.secrets[hash_token(raw)] = None  # what the database stores
        return code

    def register_client(self, actor: str, name: str, *extra: str) -> tuple[str, str | None]:
        out = self.text("oauth", "client", "register", "--actor", actor, "--name", name,
                        "--redirect-uri", REDIRECT, *extra)
        client_id = re.search(r"^client_id: (\S+)$", out, re.M).group(1)
        secret = re.search(r"^client_secret: (\S+)$", out, re.M)
        if secret:
            self._remember(secret.group(1))
        return client_id, secret.group(1) if secret else None

    def assert_no_leaks(self) -> None:
        audit_file = Path(self.env["MAK4I_AUDIT_LOG"])
        audit_text = audit_file.read_text() if audit_file.exists() else ""
        for secret, origin in self.secrets.items():
            assert secret not in audit_text, f"secret in the audit log: {secret[:10]}…"
            for index, result in enumerate(self.runs):
                assert secret not in result.stderr, f"secret on stderr of run {index}"
                if index != origin:
                    assert secret not in result.stdout, f"secret on stdout of run {index}: {secret[:10]}…"
        for result in self.runs:
            assert "token_hash" not in result.stdout and "client_secret_hash" not in result.stdout
            assert "Traceback" not in result.stderr

    # -- inspection -------------------------------------------------------------

    def authenticate(self, token: str) -> str:
        result = subprocess.run([sys.executable, "-c", _AUTHENTICATE], input=token, env=self.env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    def events(self, actor: str | None = None, organization_id: str | None = None) -> list[dict]:
        actor = actor or self.ids["owner"]
        args = ["audit", "list", "--actor", actor]
        if organization_id or "org" in self.ids:
            args += ["--organization-id", organization_id or self.ids["org"]]
        events = self.ok(*args)
        assert isinstance(events, list)
        return events

    def security_events(self) -> list[dict]:
        path = Path(self.env["MAK4I_AUDIT_LOG"])
        out = []
        for line in path.read_text().splitlines():
            if line.startswith("{"):
                out.append(json.loads(line))
        return out

    def snapshot(self) -> dict:
        """Everything an administrative mutation could change, seen through the CLI."""
        owner = self.ids["owner"]
        return {
            "inventory": self.ok("admin", "inventory"),
            "clients": self.ok("oauth", "client", "list", "--actor", owner),
            "authorizations": self.ok("oauth", "authorization", "list", "--actor", owner),
        }

    # -- seeding an OAuth authorization (needs a browser otherwise) --------------

    def seed_authorization(self, principal_id: str, client_id: str) -> str:
        from mak4i.identity import ControlPlane
        from mak4i.identity.sql_store import SqlControlPlaneStore
        from mak4i.oauth import OAuthService, OAuthSettings
        from mak4i.oauth.service import AuthorizationRequest, pkce_s256
        from mak4i.oauth.store import SqlOAuthStore

        store = SqlControlPlaneStore(self.env["MAK4I_CONTROL_PLANE_DB"])
        try:
            cp = ControlPlane(store)
            settings = OAuthSettings.from_env(self.env)
            service = OAuthService(settings=settings, store=SqlOAuthStore(store.engine), control_plane=cp)
            before = {a["authorization_id"] for a in SqlOAuthStore(store.engine).list_authorizations(
                organization_id=cp.get_principal(principal_id).organization_id)}
            service.approve(cp.get_principal(principal_id), AuthorizationRequest(
                client_id=client_id, client_name=None, client_kind="pre_registered", redirect_uri=REDIRECT,
                state=None, code_challenge=pkce_s256("v" * 43), scopes=("mak4i:read",),
                resource=settings.resource))
            after = {a["authorization_id"] for a in SqlOAuthStore(store.engine).list_authorizations(
                organization_id=cp.get_principal(principal_id).organization_id)}
        finally:
            store.engine.dispose()
        (authorization_id,) = after - before
        return authorization_id


def _one(events: list[dict], action: str, outcome: str = "succeeded", actor: str | None = "any") -> dict:
    found = [e for e in events if e["action"] == action and e["outcome"] == outcome
             and (actor == "any" or e["actor"]["principal_id"] == actor)]
    assert len(found) == 1, (action, outcome, actor, found)
    return found[0]


def _artifact(admin: Admin, principal: str, project: str, artifact_id: str, artifact_type: str):
    return admin.run("create", "--principal", principal, "--project", project, "--artifact-id", artifact_id,
                     "--artifact-type", artifact_type, "--title", artifact_id, "--content", f"{artifact_id} body")


def _refused_writes(admin: Admin, principal: str, project: str) -> list[dict]:
    return [e for e in admin.security_events() if e.get("event") == "WRITE_REFUSED"
            and e.get("principal_id") == principal and e.get("project_id") == project]


# -- the installation, bootstrapped once through the CLI --------------------------


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """Acme (owner Olivia; projects Platform and Docs; a human member, a
    service and an agent) and Globex (owner Gus), created only with the
    real executable. Each test gets its own copy of the database."""
    root = tmp_path_factory.mktemp("admin-template")
    a = Admin(root)
    created = a.ok("org", "create", "--name", "Acme", "--owner-display-name", "Olivia")
    org, owner = created["organization"]["organization_id"], created["owner"]["principal_id"]
    a.ids.update(org=org, owner=owner)
    other = a.ok("org", "create", "--name", "Globex", "--owner-display-name", "Gus")
    a.ids.update(other_org=other["organization"]["organization_id"], other_owner=other["owner"]["principal_id"])
    for key, name in (("p1", "Platform"), ("p2", "Docs")):
        a.ids[key] = a.ok("project", "create", "--actor", owner, "--organization-id", org, "--name", name)["project_id"]
    principals = {
        "member": ("--type", "human", "--display-name", "Mia"),
        "service": ("--type", "service", "--display-name", "CI"),
        "agent": ("--type", "agent", "--agent-id", "release-bot", "--display-name", "Release bot"),
    }
    outputs = {"org_create": created}
    for key, flags in principals.items():
        outputs[key] = a.ok("principal", "create", "--actor", owner, "--organization-id", org, *flags)
        a.ids[key] = outputs[key]["principal_id"]
    grants = [("owner", "p1", "read,write,resolve"), ("member", "p1", "read,write,resolve"),
              ("member", "p2", "read"), ("agent", "p1", "read,write")]
    for who, project, permissions in grants:
        outputs[f"grant_{who}_{project}"] = a.ok(
            "grant", "create", "--actor", owner, "--principal-id", a.ids[who],
            "--project-id", a.ids[project], "--permissions", permissions)
    a.assert_no_leaks()
    return {"root": root, "ids": dict(a.ids), "outputs": outputs}


@pytest.fixture
def admin(template, tmp_path):
    root = template["root"]
    for name in ("control-plane.db", "audit.jsonl"):
        shutil.copy2(root / name, tmp_path / name)
    if (root / "artifacts").exists():
        shutil.copytree(root / "artifacts", tmp_path / "artifacts")
    a = Admin(tmp_path)
    a.ids.update(template["ids"])
    yield a
    a.assert_no_leaks()


# == 1. provisioning ==============================================================


def test_bootstrap_outputs_and_their_durable_admin_events(admin, template):
    ids, out = admin.ids, template["outputs"]
    owner = out["org_create"]["owner"]
    assert owner["role"] == "owner" and owner["principal_type"] == owner["type"] == "human"
    assert out["org_create"]["organization"]["status"] == "active"
    assert (out["member"]["principal_type"], out["member"]["role"], out["member"]["agent_id"]) == ("human", "member", None)
    assert out["service"]["principal_type"] == "service"
    assert (out["agent"]["principal_type"], out["agent"]["agent_id"]) == ("agent", "release-bot")
    assert out["grant_member_p1"]["permissions"] == ["read", "write", "resolve"]

    events = admin.events()
    created = _one(events, "organization.create")
    assert created["actor"] == {"principal_id": None, "auth_method": "operator"}  # instance operator
    assert created["targets"]["owner_principal_id"] == ids["owner"]
    succeeded = [e for e in events if e["outcome"] == "succeeded" and e["actor"]["principal_id"] == ids["owner"]]
    assert sorted(e["action"] for e in succeeded) == sorted(
        ["project.create"] * 2 + ["principal.create"] * 3 + ["grant.set"] * 4)
    for event in events:
        assert event["organization_id"] == ids["org"] and event["event_id"] and event["correlation_id"]
    agent_event = [e for e in events if e["action"] == "principal.create" and e["targets"].get("agent_id")]
    assert agent_event[0]["targets"]["principal_id"] == ids["agent"]
    # Globex's history is its own.
    assert not any(e["organization_id"] == ids["org"]
                   for e in admin.events(ids["other_owner"], ids["other_org"]))

    listed = admin.ok("principal", "list", "--actor", ids["owner"])
    assert {p["principal_id"] for p in listed} == {ids[k] for k in ("owner", "member", "service", "agent")}
    assert {p["project_id"] for p in admin.ok("project", "list", "--actor", ids["owner"])} == {ids["p1"], ids["p2"]}


def test_agent_principal_needs_a_valid_unique_agent_id(admin):
    ids = admin.ids
    before = admin.ok("principal", "list", "--actor", ids["owner"])
    missing = admin.refused("principal", "create", "--actor", ids["owner"], "--organization-id", ids["org"],
                            "--type", "agent", "--display-name", "No id")
    assert "agent_id" in missing.stderr
    taken = admin.refused("principal", "create", "--actor", ids["owner"], "--organization-id", ids["org"],
                          "--type", "agent", "--agent-id", "release-bot", "--display-name", "Clone")
    assert "release-bot" in taken.stderr
    assert admin.ok("principal", "list", "--actor", ids["owner"]) == before
    failed = [e for e in admin.events() if e["action"] == "principal.create" and e["outcome"] == "failed"]
    assert len(failed) == 2 and all(e["actor"]["principal_id"] == ids["owner"] for e in failed)


@pytest.mark.parametrize("artifact_type", TYPES)
def test_credential_is_shown_once_and_never_listed(admin, artifact_type):
    ids = admin.ids
    # With the connection help: it repeats the token, still only on this one stdout.
    credential_id, token = admin.issue_credential(ids["owner"], ids["agent"], "--display-name", "bot key")
    assert ENDPOINT in admin.runs[-1].stdout
    assert admin.authenticate(token) == f"ok {ids['agent']}"
    listed = admin.ok("credential", "list", "--actor", ids["owner"], "--principal-id", ids["agent"])
    (summary,) = listed
    assert summary["credential_id"] == credential_id and summary["status"] == "active"
    assert set(summary) == {"credential_id", "principal_id", "display_name", "status", "created_at",
                            "expires_at", "revoked_at"}
    inventory = admin.ok("admin", "inventory")
    acme = next(o for o in inventory["organizations"] if o["organization_id"] == ids["org"])
    agent = next(p for p in acme["principals"] if p["principal_id"] == ids["agent"])
    assert agent["agent_id"] == "release-bot" and agent["credentials"][0]["credential_id"] == credential_id
    issued = _one(admin.events(), "credential.issue")
    assert issued["targets"] == {"principal_id": ids["agent"], "display_name": "bot key",
                                 "credential_id": credential_id}
    # The provisioned agent can write through the same engine.
    assert _artifact(admin, ids["agent"], ids["p1"], "a-1", artifact_type).returncode == 0


def test_invalid_expiry_is_a_clean_error_and_issues_nothing(admin):
    ids = admin.ids
    admin.refused("credential", "issue", "--actor", ids["owner"], "--principal-id", ids["agent"],
                  "--expires-at", "not-a-date")
    assert admin.ok("credential", "list", "--actor", ids["owner"], "--principal-id", ids["agent"]) == []


@pytest.mark.xfail(strict=True, reason=(
    "`grant create --permissions read,admin` lets pydantic's ValidationError escape main(): "
    "a Python traceback instead of a one-line `error:` (the failed admin event is recorded correctly)"))
def test_unknown_permission_is_a_clean_error(admin):
    ids = admin.ids
    result = admin.run("grant", "create", "--actor", ids["owner"], "--principal-id", ids["service"],
                       "--project-id", ids["p1"], "--permissions", "read,admin")
    admin.runs.pop()  # judged here, not by the fixture's leak check
    assert result.returncode == 1 and not result.stdout.strip()
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error:") and "'read', 'write' or 'resolve'" in result.stderr


def test_unknown_permission_grants_nothing_and_is_recorded_as_failed(admin):
    ids = admin.ids
    result = admin.run("grant", "create", "--actor", ids["owner"], "--principal-id", ids["service"],
                       "--project-id", ids["p1"], "--permissions", "read,admin")
    assert result.returncode != 0 and not result.stdout.strip()
    admin.runs.pop()  # its traceback is the xfail above, not a leak
    assert admin.ok("grant", "list", "--actor", ids["owner"], "--principal-id", ids["service"]) == []
    failed = _one(admin.events(), "grant.set", "failed")
    assert failed["targets"]["permissions"] == "read,admin"


def test_access_provision_runs_non_interactively(admin, tmp_path):
    before = admin.ok("org", "list")
    no_endpoint = admin.refused("access", "provision", "--display-name", "Cara", "--org-name", "Partner",
                                "--project-name", "Joint", MAK4I_PUBLIC_ENDPOINT="")
    assert "Nothing was provisioned" in no_endpoint.stderr
    prompt = admin.refused("access", "provision", "--display-name", "Cara", "--org-name", "Partner")
    assert "--flag" in prompt.stderr  # never blocks on a prompt without a terminal
    assert admin.ok("org", "list") == before

    handoff = tmp_path / "handoff.txt"
    out = admin.text("access", "provision", "--display-name", "Cara", "--org-name", "Partner",
                     "--project-name", "Joint", "--output", str(handoff))
    token = re.search(r"^Token:\s+(\S+)$", out, re.M).group(1)
    project = re.search(r"^Project:\s+(\S+)$", out, re.M).group(1)
    admin._remember(token)
    assert f"Endpoint: {ENDPOINT}" in out
    assert token in handoff.read_text() and stat.S_IMODE(handoff.stat().st_mode) == 0o600

    (partner,) = [o for o in admin.ok("org", "list") if o not in before]
    inventory = admin.ok("admin", "inventory")
    org = next(o for o in inventory["organizations"] if o["organization_id"] == partner["organization_id"])
    (owner,) = org["owners"]
    collaborator = next(p for p in org["principals"] if p["display_name"] == "Cara")
    assert collaborator["role"] == "member"
    assert collaborator["grants"] == [{"project_id": project, "permissions": ["read", "write"]}]
    assert admin.authenticate(token) == f"ok {collaborator['principal_id']}"
    events = admin.events(owner, partner["organization_id"])
    assert sorted(e["action"] for e in events if e["outcome"] == "succeeded") == sorted(
        ["organization.create", "principal.create", "project.create", "grant.set", "credential.issue"])
    # The collaborator is a member: it can't administer its own organization.
    admin.denied("project", "create", "--organization-id", partner["organization_id"], "--name", "More",
                 MAK4I_TOKEN=token)


def test_config_commands_print_json(admin):
    env = {"MAK4I_OAUTH_ENABLED": "0"}
    described = admin.ok("config", "check", **env)
    assert described["storage"]["backend"] == "local"
    assert described["control_plane"]["database"] == {"source": "env", "reference": "MAK4I_CONTROL_PLANE_DB"}
    assert "sqlite" not in json.dumps(described["control_plane"])  # a reference, never the value
    assert admin.ok("config", "schema")["$schema"].startswith("https://json-schema.org/")


# == 2. disabling =================================================================


def test_principal_deactivate_stops_every_access_and_is_recorded(admin):
    ids = admin.ids
    _cred, token = admin.issue_credential(ids["owner"], ids["member"], "--no-connection-help")
    client_id, _ = admin.register_client(ids["owner"], "Desktop")
    authorization_id = admin.seed_authorization(ids["member"], client_id)
    assert admin.ok("get-current", "--principal", ids["member"], "--project", ids["p1"])

    result = admin.ok("principal", "deactivate", "--actor", ids["owner"], "--principal-id", ids["member"])
    assert result["principal"]["status"] == "deactivated" and result["oauth_authorizations_revoked"] == 1

    assert admin.authenticate(token) == "invalid principal_inactive"
    admin.denied("get-current", "--principal", ids["member"], "--project", ids["p1"])
    admin.refused("project", "list", MAK4I_TOKEN=token)  # its credential can't act as an actor either
    (authorization,) = admin.ok("oauth", "authorization", "list", "--actor", ids["owner"],
                                "--principal-id", ids["member"])
    assert (authorization["authorization_id"], authorization["status"], authorization["revoked_reason"]) == (
        authorization_id, "revoked", "principal_deactivated")
    admin.refused("oauth", "sign-in-code", "--actor", ids["owner"], "--principal-id", ids["member"])
    # History is kept: the principal still exists, deactivated.
    shown = admin.ok("principal", "show", "--actor", ids["owner"], "--principal-id", ids["member"])
    assert shown["status"] == "deactivated"

    # The last active owner can't be deactivated.
    last = admin.refused("principal", "deactivate", "--actor", ids["owner"], "--principal-id", ids["owner"])
    assert "at least one active owner" in last.stderr

    events = admin.events()
    assert _one(events, "principal.deactivate")["targets"] == {"principal_id": ids["member"]}
    assert _one(events, "principal.deactivate", "failed")["detail"] == "LastOwnerError"


def test_deactivated_owner_can_no_longer_administer(admin):
    ids = admin.ids
    second = admin.ok("principal", "create", "--actor", ids["owner"], "--organization-id", ids["org"],
                      "--type", "human", "--display-name", "Deputy", "--role", "owner")["principal_id"]
    admin.ok("project", "create", "--actor", second, "--organization-id", ids["org"], "--name", "Deputy's")
    admin.ok("principal", "deactivate", "--actor", ids["owner"], "--principal-id", second)
    before = admin.ok("project", "list", "--actor", ids["owner"])
    admin.denied("project", "create", "--actor", second, "--organization-id", ids["org"], "--name", "Again")
    assert admin.ok("project", "list", "--actor", ids["owner"]) == before
    _one(admin.events(), "project.create", "denied", actor=second)


@pytest.mark.parametrize("artifact_type", TYPES)
def test_project_archive_refuses_further_access_and_deletes_nothing(admin, artifact_type):
    ids = admin.ids
    assert _artifact(admin, ids["owner"], ids["p1"], "before", artifact_type).returncode == 0
    archived = admin.ok("project", "archive", "--actor", ids["owner"], "--project-id", ids["p1"])
    assert archived["status"] == "archived" and archived["project_id"] == ids["p1"]

    other_type = next(t for t in TYPES if t != artifact_type)
    after = _artifact(admin, ids["owner"], ids["p1"], "after", other_type)
    assert after.returncode == 1 and not after.stdout.strip()
    assert [e["error_code"] for e in _refused_writes(admin, ids["owner"], ids["p1"])] == ["access_denied"]
    admin.denied("get-current", "--principal", ids["owner"], "--project", ids["p1"])
    admin.denied("get-current", "--principal", ids["member"], "--project", ids["p1"])

    statuses = {p["project_id"]: p["status"] for p in admin.ok("project", "list", "--actor", ids["owner"])}
    assert statuses == {ids["p1"]: "archived", ids["p2"]: "active"}
    grants = admin.ok("grant", "list", "--actor", ids["owner"], "--principal-id", ids["member"])
    assert {g["project_id"] for g in grants} == {ids["p1"], ids["p2"]}  # grants kept, just ineffective
    assert admin.ok("get-current", "--principal", ids["member"], "--project", ids["p2"])
    assert _one(admin.events(), "project.archive")["targets"] == {"project_id": ids["p1"]}


def test_org_suspend_and_reactivate_are_instance_operator_actions(admin):
    ids = admin.ids
    _c, member_token = admin.issue_credential(ids["owner"], ids["member"], "--no-connection-help")
    _c, owner_token = admin.issue_credential(ids["owner"], ids["owner"], "--no-connection-help")

    # Not an owner command: there is no --actor to pass, so an org owner can't name itself.
    rejected = admin.refused("org", "suspend", "--actor", ids["owner"], "--organization-id", ids["org"], code=2)
    assert "unrecognized arguments" in rejected.stderr
    assert admin.ok("org", "show", "--organization-id", ids["org"])["status"] == "active"

    assert admin.ok("org", "suspend", "--organization-id", ids["org"])["status"] == "suspended"
    assert admin.authenticate(member_token) == "invalid organization_inactive"
    assert admin.authenticate(owner_token) == "invalid organization_inactive"
    admin.refused("project", "create", "--organization-id", ids["org"], "--name", "During", MAK4I_TOKEN=owner_token)
    admin.denied("get-current", "--principal", ids["member"], "--project", ids["p1"])
    # Globex is unaffected.
    admin.ok("project", "create", "--actor", ids["other_owner"], "--organization-id", ids["other_org"], "--name", "G")

    assert admin.ok("org", "reactivate", "--organization-id", ids["org"])["status"] == "active"
    assert admin.authenticate(member_token) == f"ok {ids['member']}"
    created = admin.ok("project", "create", "--organization-id", ids["org"], "--name", "After",
                       MAK4I_TOKEN=owner_token)

    events = admin.events()
    for action in ("organization.suspend", "organization.reactivate"):
        event = _one(events, action)
        assert event["actor"] == {"principal_id": None, "auth_method": "operator"}
        assert event["targets"] == {"organization_id": ids["org"]}
    by_token = [e for e in events if e["action"] == "project.create" and e["targets"].get("name") == "After"]
    assert by_token[0]["targets"]["project_id"] == created["project_id"]
    assert by_token[0]["actor"] == {"principal_id": ids["owner"], "auth_method": "credential"}
    assert not [e for e in events if e["targets"].get("name") == "During" and e["outcome"] == "succeeded"]


def test_oauth_client_register_list_and_disable(admin):
    ids = admin.ids
    public_id, no_secret = admin.register_client(ids["owner"], "Desktop")
    confidential_id, secret = admin.register_client(ids["owner"], "Server", "--confidential")
    assert no_secret is None and secret
    clients = {c["client_id"]: c for c in admin.ok("oauth", "client", "list", "--actor", ids["owner"])}
    assert set(clients) == {public_id, confidential_id}
    assert clients[public_id]["token_endpoint_auth_method"] == "none"
    assert clients[confidential_id]["token_endpoint_auth_method"] == "client_secret_basic"
    assert all(c["status"] == "active" and c["redirect_uris"] == [REDIRECT] for c in clients.values())

    authorization_id = admin.seed_authorization(ids["member"], public_id)
    disabled = admin.ok("oauth", "client", "disable", "--actor", ids["owner"], "--client-id", public_id)
    assert disabled["status"] == "disabled"
    (authorization,) = admin.ok("oauth", "authorization", "list", "--actor", ids["owner"], "--client-id", public_id)
    assert (authorization["authorization_id"], authorization["status"], authorization["revoked_reason"]) == (
        authorization_id, "revoked", "client_disabled")
    statuses = {c["client_id"]: c["status"] for c in admin.ok("oauth", "client", "list", "--actor", ids["owner"])}
    assert statuses == {public_id: "disabled", confidential_id: "active"}

    events = admin.events()
    registered = [e for e in events if e["action"] == "oauth_client.register"]
    assert {(e["targets"]["client_id"], e["targets"]["confidential"]) for e in registered} == {
        (public_id, "false"), (confidential_id, "true")}
    assert _one(events, "oauth_client.disable")["targets"] == {"client_id": public_id}


# == 3. grants ====================================================================


def test_grant_create_list_revoke_and_its_effect(admin):
    ids = admin.ids
    member, p1, p2 = ids["member"], ids["p1"], ids["p2"]
    assert _artifact(admin, member, p1, "cache-1", "caching_decision").returncode == 0

    out = admin.text("grant", "revoke", "--actor", ids["owner"], "--principal-id", member, "--project-id", p1)
    assert out.strip() == f"revoked grant for principal {member!r} on project {p1!r}"
    grants = admin.ok("grant", "list", "--actor", ids["owner"], "--principal-id", member)
    assert [(g["project_id"], g["permissions"]) for g in grants] == [(p2, ["read"])]

    refused = _artifact(admin, member, p1, "db-1", "database_decision")
    assert refused.returncode == 1 and not refused.stdout.strip() and "access denied" in refused.stderr
    assert [e["error_code"] for e in _refused_writes(admin, member, p1)] == ["access_denied"]
    admin.denied("get-current", "--principal", member, "--project", p1)
    assert admin.ok("get-current", "--principal", member, "--project", p2)  # still granted
    read_only = _artifact(admin, member, p2, "db-2", "database_decision")
    assert read_only.returncode == 1
    assert [e["error_code"] for e in _refused_writes(admin, member, p2)] == ["access_denied"]

    # Narrowing to read: reads come back, writes stay refused.
    admin.ok("grant", "create", "--actor", ids["owner"], "--principal-id", member, "--project-id", p1,
             "--permissions", "read")
    current = admin.ok("get-current", "--principal", member, "--project", p1)
    assert "cache-1" in json.dumps(current)
    assert _artifact(admin, member, p1, "cache-2", "caching_decision").returncode == 1

    events = admin.events()
    assert _one(events, "grant.revoke")["targets"] == {"principal_id": member, "project_id": p1}
    regrant = [e for e in events if e["action"] == "grant.set" and e["targets"]["permissions"] == "read"
               and e["targets"]["project_id"] == p1]
    assert len(regrant) == 1


# == 4. revocation ================================================================


def test_credential_revoke_takes_effect_and_never_shows_the_hash(admin):
    ids = admin.ids
    credential_id, token = admin.issue_credential(ids["owner"], ids["service"], "--no-connection-help")
    assert admin.authenticate(token) == f"ok {ids['service']}"
    revoked = admin.ok("credential", "revoke", "--actor", ids["owner"], "--credential-id", credential_id)
    assert revoked["status"] == "revoked" and revoked["revoked_at"] and "token_hash" not in revoked
    assert admin.authenticate(token) == "invalid revoked"
    (listed,) = admin.ok("credential", "list", "--actor", ids["owner"], "--principal-id", ids["service"])
    assert listed["status"] == "revoked"

    # An owner credential used as the CLI actor (MAK4I_TOKEN, no --actor) stops at once too.
    owner_credential, owner_token = admin.issue_credential(ids["owner"], ids["owner"], "--no-connection-help")
    assert admin.ok("project", "list", MAK4I_TOKEN=owner_token)
    admin.ok("credential", "revoke", "--actor", ids["owner"], "--credential-id", owner_credential)
    stale = admin.refused("project", "list", MAK4I_TOKEN=owner_token)
    assert "invalid or expired credential" in stale.stderr

    revokes = [e for e in admin.events() if e["action"] == "credential.revoke"]
    assert {e["targets"]["credential_id"] for e in revokes} == {credential_id, owner_credential}


def test_sign_in_codes_and_authorization_revocation(admin):
    ids = admin.ids
    owner, member, agent = ids["owner"], ids["member"], ids["agent"]
    admin.sign_in_code(owner, member)          # an owner, for a principal of its organization
    admin.sign_in_code(member)                 # a principal, for itself
    admin.denied("oauth", "sign-in-code", "--actor", member, "--principal-id", owner)

    client_id, _ = admin.register_client(owner, "Desktop")
    first = admin.seed_authorization(member, client_id)
    second = admin.seed_authorization(member, client_id)
    agents = admin.seed_authorization(agent, client_id)
    listed = admin.ok("oauth", "authorization", "list", "--actor", owner)
    assert {a["authorization_id"] for a in listed} == {first, second, agents}
    assert all(set(a) >= {"authorization_id", "principal_id", "client_id", "scopes", "status"} for a in listed)
    assert not any("token" in key for a in listed for key in a)

    one = admin.ok("oauth", "authorization", "revoke", "--actor", owner, "--authorization-id", first)
    assert one["status"] == "revoked" and one["revoked_reason"] == "revoked_by_admin"
    assert admin.ok("oauth", "authorization", "revoke", "--actor", owner, "--principal-id", member) == {"revoked": 1}
    statuses = {a["authorization_id"]: a["status"] for a in admin.ok("oauth", "authorization", "list", "--actor", owner)}
    assert statuses == {first: "revoked", second: "revoked", agents: "active"}

    # Instance operator: every active authorization, every organization (run after a restore).
    assert admin.ok("admin", "revoke-oauth", "--all", "--reason", "restore") == {"revoked": 1}
    assert {a["status"] for a in admin.ok("oauth", "authorization", "list", "--actor", owner)} == {"revoked"}
    assert admin.ok("admin", "revoke-oauth", "--all") == {"revoked": 0}

    events = admin.events()
    codes = [e for e in events if e["action"] == "oauth_sign_in_code.issue"]
    assert {(e["actor"]["principal_id"], e["targets"]["principal_id"], e["outcome"]) for e in codes} == {
        (owner, member, "succeeded"), (member, member, "succeeded"), (member, owner, "denied")}
    assert _one(events, "oauth_authorization.revoke")["targets"] == {"authorization_id": first}
    assert _one(events, "oauth_authorization.revoke_all")["targets"] == {"principal_id": member, "count": "1"}


def test_a_principal_may_revoke_its_own_authorization(admin):
    ids = admin.ids
    client_id, _ = admin.register_client(ids["owner"], "Desktop")
    own = admin.seed_authorization(ids["member"], client_id)
    revoked = admin.ok("oauth", "authorization", "revoke", "--actor", ids["member"], "--authorization-id", own)
    assert revoked["status"] == "revoked"
    _one(admin.events(), "oauth_authorization.revoke", actor=ids["member"])


def test_instance_wide_oauth_revocation_is_visible_to_affected_owners(admin):
    ids = admin.ids
    client_id, _ = admin.register_client(ids["owner"], "Desktop")
    admin.seed_authorization(ids["member"], client_id)
    assert admin.ok("admin", "revoke-oauth", "--all") == {"revoked": 1}
    assert [e for e in admin.events() if e["action"].startswith("oauth_authorization.revoke")]


# == 5. permission boundaries =====================================================


def _mutations(admin: Admin, target_credential: str, client_id: str, authorization_id: str) -> list:
    ids = admin.ids
    return [
        ("project.create", ["project", "create", "--organization-id", ids["org"], "--name", "Rogue"]),
        ("project.archive", ["project", "archive", "--project-id", ids["p1"]]),
        ("principal.create", ["principal", "create", "--organization-id", ids["org"], "--type", "human",
                              "--display-name", "Rogue", "--role", "owner"]),
        ("principal.rename", ["principal", "rename", "--principal-id", ids["agent"], "--display-name", "Rogue"]),
        ("principal.deactivate", ["principal", "deactivate", "--principal-id", ids["agent"]]),
        ("grant.set", ["grant", "create", "--principal-id", ids["agent"], "--project-id", ids["p2"],
                       "--permissions", "read,write,resolve"]),
        ("grant.revoke", ["grant", "revoke", "--principal-id", ids["agent"], "--project-id", ids["p1"]]),
        ("credential.issue", ["credential", "issue", "--principal-id", ids["agent"]]),
        ("credential.revoke", ["credential", "revoke", "--credential-id", target_credential]),
        ("oauth_client.register", ["oauth", "client", "register", "--organization-id", ids["org"],
                                   "--name", "Rogue", "--redirect-uri", REDIRECT]),
        ("oauth_client.disable", ["oauth", "client", "disable", "--client-id", client_id]),
        ("oauth_sign_in_code.issue", ["oauth", "sign-in-code", "--principal-id", ids["agent"]]),
        ("oauth_authorization.revoke", ["oauth", "authorization", "revoke", "--authorization-id", authorization_id]),
        ("oauth_authorization.revoke_all", ["oauth", "authorization", "revoke", "--principal-id", ids["agent"]]),
    ]


@pytest.mark.parametrize("who", ["member", "other_owner"])
def test_non_owners_are_refused_every_admin_mutation_and_nothing_changes(admin, who):
    ids = admin.ids
    actor = ids[who]
    target_credential, _ = admin.issue_credential(ids["owner"], ids["agent"], "--no-connection-help")
    client_id, _ = admin.register_client(ids["owner"], "Desktop")
    authorization_id = admin.seed_authorization(ids["agent"], client_id)
    before = admin.snapshot()
    other_before = admin.events(ids["other_owner"], ids["other_org"])

    mutations = _mutations(admin, target_credential, client_id, authorization_id)
    for _action, args in mutations:
        admin.denied(*_with_actor(args, actor))

    # Inspection is owner-only too (no event: nothing was attempted to change).
    for args in (["project", "list", "--organization-id", ids["org"]], ["project", "show", "--project-id", ids["p1"]],
                 ["principal", "list", "--organization-id", ids["org"]],
                 ["principal", "show", "--principal-id", ids["agent"]],
                 ["grant", "list", "--principal-id", ids["agent"]],
                 ["credential", "list", "--principal-id", ids["agent"]],
                 ["audit", "list", "--organization-id", ids["org"]],
                 ["oauth", "client", "list", "--organization-id", ids["org"]],
                 ["oauth", "authorization", "list", "--principal-id", ids["agent"]]):
        admin.denied(*_with_actor(args, actor))

    assert admin.snapshot() == before
    denied = [e for e in admin.events() if e["outcome"] == "denied"]
    assert sorted(e["action"] for e in denied) == sorted(action for action, _ in mutations)
    for event in denied:
        assert event["actor"] == {"principal_id": actor, "auth_method": "operator"}
        assert event["organization_id"] == ids["org"]  # recorded where the target's owners look
    # Nothing was recorded in (or leaked to) the other organization's history.
    assert admin.events(ids["other_owner"], ids["other_org"]) == other_before


def _with_actor(args: list[str], actor: str) -> list[str]:
    """Insert `--actor` right after the (sub)command words."""
    words = 3 if args[0] == "oauth" and args[1] in ("client", "authorization") else 2
    return [*args[:words], "--actor", actor, *args[words:]]


def test_self_service_rules(admin):
    ids = admin.ids
    member = ids["member"]
    # A credential is issued by an owner only, even for oneself.
    admin.denied("credential", "issue", "--actor", member, "--principal-id", member)
    assert admin.ok("credential", "list", "--actor", ids["owner"], "--principal-id", member) == []
    # A sign-in code may be requested for oneself (MAK-0008 §5.3.1).
    admin.sign_in_code(member)
    # Holding `resolve` (and write) on a project confers no administration (MAK-0006 §8.3).
    admin.denied("project", "archive", "--actor", member, "--project-id", ids["p1"])
    admin.denied("grant", "create", "--actor", member, "--principal-id", member, "--project-id", ids["p2"],
                 "--permissions", "read,write")
    events = admin.events()
    assert _one(events, "credential.issue", "denied")["targets"]["principal_id"] == member
    assert _one(events, "oauth_sign_in_code.issue", actor=member)["targets"] == {"principal_id": member}


@pytest.mark.parametrize("args", [
    ["org", "create", "--name", "X", "--owner-display-name", "Y"],
    ["org", "suspend", "--organization-id", "{org}"],
    ["org", "reactivate", "--organization-id", "{org}"],
    ["org", "list"],
    ["org", "show", "--organization-id", "{org}"],
    ["admin", "inventory"],
    ["admin", "revoke-oauth", "--all"],
])
def test_instance_operator_commands_take_no_actor(admin, args):
    """These act with the instance operator's direct database access
    (MAK-0006 §8.1); they accept no `--actor`, so no organization owner
    can run them as itself. Nothing changes when one tries."""
    before = admin.ok("admin", "inventory")
    rejected = admin.refused(*[a.format(org=admin.ids["org"]) for a in args], "--actor", admin.ids["owner"], code=2)
    assert "unrecognized arguments: --actor" in rejected.stderr
    assert admin.ok("admin", "inventory") == before
