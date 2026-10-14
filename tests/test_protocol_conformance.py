"""Reference outputs validated against the MAK4I protocol's canonical JSON
Schemas at the revision pinned in `PROTOCOL_REVISION`.

Mandatory: these tests FAIL (never skip) when the pinned schemas are not
available. Get them with `scripts/fetch_protocol.sh` (CI does), or point
`MAK4I_PROTOCOL_DIR` at a checkout of exactly that revision. Every instance
comes from a real interface path (MCP over HTTP with a credential and with
OAuth, the OAuth metadata endpoints, the admin event log), not hand-built
fixtures.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _pinned() -> tuple[str, str]:
    for line in (ROOT / "PROTOCOL_REVISION").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            repo, sha = line.split()
            return repo, sha
    raise AssertionError("PROTOCOL_REVISION has no pin")


def _protocol_schemas() -> Path:
    _repo, sha = _pinned()
    root = Path(os.environ.get("MAK4I_PROTOCOL_DIR") or ROOT / ".protocol")
    schemas = root / "schemas"
    if not (schemas / "common.schema.json").exists():
        pytest.fail(
            f"protocol schemas not found at {schemas}. Run scripts/fetch_protocol.sh "
            "(or set MAK4I_PROTOCOL_DIR to a checkout of the pinned revision)."
        )
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode != 0 or head.stdout.strip() != sha:
        pytest.fail(
            f"protocol checkout at {root} is {head.stdout.strip() or 'not a git checkout'}, "
            f"but PROTOCOL_REVISION pins {sha}. Run scripts/fetch_protocol.sh."
        )
    return schemas


def test_protocol_revision_is_pinned():
    _repo, sha = _pinned()
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


@pytest.fixture(scope="module")
def validate():
    from referencing import Registry, Resource

    docs = {p.name: json.loads(p.read_text()) for p in _protocol_schemas().glob("*.schema.json")}
    registry = Registry().with_resources((d["$id"], Resource.from_contents(d)) for d in docs.values())

    def check(schema_name: str, instance, fragment: str = ""):
        schema = docs[schema_name + ".schema.json"]
        target = {"$ref": schema["$id"] + fragment} if fragment else schema
        errors = sorted(
            jsonschema.Draft202012Validator(target, registry=registry, format_checker=jsonschema.FormatChecker())
            .iter_errors(instance),
            key=str,
        )
        assert not errors, f"{schema_name}{fragment}: {errors[0].message} at {list(errors[0].absolute_path)}"

    return check


def _tool(schema_validate, tool: str, direction: str, instance):
    schema_validate("mcp-tools", instance, f"#/$defs/{tool}/properties/{direction}")


def test_reference_interfaces_conform_to_the_protocol_schemas(tmp_path, validate):
    from tests.test_oauth import CLAUDE_CODE_CIMD, World, _fake_fetch

    w = World(tmp_path)
    with w.client:
        # OAuth metadata (MAK-0008 §4) and an accepted client metadata document (§8.3).
        validate("oauth-protected-resource-metadata", w.client.get("/.well-known/oauth-protected-resource/mcp").json())
        validate("oauth-authorization-server-metadata", w.client.get("/.well-known/oauth-authorization-server").json())
        validate("client-id-metadata-document", json.loads(_fake_fetch(CLAUDE_CODE_CIMD).body))

        w.cp.grant(actor=w.owner, principal_id=w.owner.principal_id, project_id=w.project.project_id,
                   permissions=["read", "write", "resolve"])
        agent = w.cp.create_principal(actor=w.owner, organization_id=w.org.organization_id, type="agent",
                                      display_name="Red bot", agent_id="red-bot")
        w.cp.grant(actor=w.owner, principal_id=agent.principal_id, project_id=w.project.project_id,
                   permissions=["read", "write"])
        _cred, agent_token = w.cp.issue_credential(actor=w.owner, principal_id=agent.principal_id)
        oauth_token = w.tokens(scope="mak4i:read mak4i:write mak4i:resolve")["access_token"]
        project = w.project.project_id

        def call(token, tool, args=None):
            _h, rpc = w.call_tool(token, tool, args or {})
            return rpc["result"]

        # whoami and list_projects, both auth methods.
        for token in (agent_token, oauth_token):
            _tool(validate, "mak4i_whoami", "output", call(token, "mak4i_whoami")["structuredContent"])
            _tool(validate, "mak4i_list_projects", "output", call(token, "mak4i_list_projects")["structuredContent"])

        # Records, supersession, conflicts and resolution.
        create = {"project": project, "artifact_id": "red", "artifact_type": "caching_decision",
                  "title": "Cache", "content": "Use RED", "subject_key": "primary"}
        _tool(validate, "mak4i_create", "input", create)
        red = call(agent_token, "mak4i_create", create)["structuredContent"]
        validate("project-record", red)
        validate("provenance", red["provenance"])
        yellow = call(oauth_token, "mak4i_create", {**create, "artifact_id": "yellow", "content": "Use YELLOW"})
        validate("project-record", yellow["structuredContent"])

        current = call(agent_token, "mak4i_get_current", {"project": project})["structuredContent"]
        _tool(validate, "mak4i_get_current", "output", current)
        validate("context-package", current)
        # MAK-0008 §14: change feed, change_sequence and the current resource.
        assert isinstance(current["change_sequence"], int) and current["change_sequence"] >= 2
        _tool(validate, "mak4i_changes", "input", {"project": project, "after": 0})
        changes = call(agent_token, "mak4i_changes", {"project": project, "after": 0})["structuredContent"]
        _tool(validate, "mak4i_changes", "output", changes)
        assert [e["kind"] for e in changes["events"]] == ["record.created", "record.created"]
        for event in changes["events"]:
            validate("change-event", event)
        session = w.session(agent_token)
        listed = w.mcp(agent_token, "resources/list", {}, session=session, request_id=3)
        from tests.test_oauth import _rpc

        (resource,) = _rpc(listed.text)["result"]["resources"]
        read = _rpc(w.mcp(agent_token, "resources/read", {"uri": resource["uri"]}, session=session, request_id=4).text)
        validate("context-package", json.loads(read["result"]["contents"][0]["text"]))
        (conflict,) = current["conflicts"]
        validate("conflict", conflict)
        _tool(validate, "mak4i_list_conflicts", "output",
              call(agent_token, "mak4i_list_conflicts", {"project": project})["structuredContent"])
        _tool(validate, "mak4i_get_conflict", "output",
              call(agent_token, "mak4i_get_conflict", {"project": project, "conflict_id": conflict["conflict_id"]})["structuredContent"])

        resolve = {"project": project, "conflict_id": conflict["conflict_id"],
                   "candidate_artifact_ids": ["red", "yellow"], "action": "merge",
                   "into_artifact_id": "red", "content": "RED in prod, YELLOW in dev", "reason": "both apply",
                   "idempotency_key": "conformance-1"}
        _tool(validate, "mak4i_resolve_conflict", "input", resolve)
        record = call(oauth_token, "mak4i_resolve_conflict", resolve)["structuredContent"]
        validate("resolution-record", record)
        _tool(validate, "mak4i_resolve_conflict", "output", record)

        history = call(agent_token, "mak4i_history", {"project": project, "lineage_id": "red"})["structuredContent"]
        _tool(validate, "mak4i_history", "output", history)
        resolved = call(agent_token, "mak4i_list_conflicts", {"project": project, "state": "resolved"})
        validate("conflict", resolved["structuredContent"]["result"][0])

        supersede = {"project": project, "old_id": record["resulting_heads"][0]["artifact_id"],
                     "content": "RED everywhere", "reason": "simplify"}
        _tool(validate, "mak4i_supersede", "input", supersede)
        validate("project-record", call(agent_token, "mak4i_supersede", supersede)["structuredContent"])
        _tool(validate, "mak4i_search", "output",
              call(agent_token, "mak4i_search", {"project": project, "status": "withdrawn"})["structuredContent"])

        # Structured errors (MAK-0008 §9).
        for args, tool in (
            ({"project": project, "old_id": "yellow", "content": "x", "reason": "y"}, "mak4i_supersede"),
            ({"project": "prj_nope"}, "mak4i_get_current"),
            ({**create, "content": "again"}, "mak4i_create"),  # artifact_exists
            ({**create, "artifact_id": "x", "x_unknown": 1}, "mak4i_create"),  # validation_error (closed inputs)
            ({**create, "artifact_id": "x", "agent_id": "not-me"}, "mak4i_create"),  # identity_mismatch
            ({"project": project, "status": "deleted"}, "mak4i_search"),  # validation_error (enum)
        ):
            result = call(agent_token, tool, args)
            assert result["isError"] is True
            validate("error", result["structuredContent"])
            assert result["structuredContent"]["error"]["correlation_id"]

        # Administrative events (MAK-0006 §8.5), including instance-operator events, and identity objects.
        events = [e.to_protocol() for e in w.cp.list_admin_events(actor=w.owner, organization_id=w.org.organization_id)]
        for event in events:
            validate("admin-event", event)
        assert any(e["actor"]["auth_method"] == "operator" and e["actor"]["principal_id"] is None for e in events)
        from mak4i.cli import _to_json_safe

        validate("principal", _to_json_safe(agent))
        validate("principal", _to_json_safe(w.owner))
        for grant in w.cp.list_grants(actor=w.owner, principal_id=agent.principal_id):
            validate("grant", grant.model_dump(mode="json"))
