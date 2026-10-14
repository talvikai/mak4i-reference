"""MAK-0008 §14 change feed and MCP resources (profile `server-change-feed`,
CONFORMANCE F1–F14), on a durable SQLite feed, through the engine and over
MCP (2025-11-25 `resources/subscribe` and 2026-07-28 `subscriptions/listen`).
Two artifact types run through the same code (CLAUDE.md)."""

from __future__ import annotations

import json
import warnings
from datetime import timedelta
from pathlib import Path

import anyio
import jsonschema
import pytest
from mcp.client import Client
from referencing import Registry, Resource

from mak4i.api import MAK4IEngine
from mak4i.changes import InMemoryChangeFeed, SqlChangeFeed
from mak4i.changes.notify import conflict_uri, current_uri, record_uri
from mak4i.errors import CursorExpiredError, ValidationFailedError
from mak4i.identity import AccessDeniedError, Authorizer, ControlPlane
from mak4i.identity.sql_store import SqlControlPlaneStore
from mak4i.mcp_server import build_server, current_auth, current_principal
from mak4i.resolution.records import SqlResolutionStore
from mak4i.store.local_json import LocalJSONStore

TYPES = ["caching_decision", "database_decision"]
SCHEMAS = Path(__file__).resolve().parents[1] / ".protocol" / "schemas"


def _validator(name):
    docs = [json.loads(p.read_text()) for p in SCHEMAS.glob("*.schema.json")]
    registry = Registry().with_resources([(d["$id"], Resource.from_contents(d)) for d in docs])
    schema = json.loads((SCHEMAS / name).read_text())
    return jsonschema.Draft202012Validator(schema, registry=registry, format_checker=jsonschema.FormatChecker())


@pytest.fixture
def w(tmp_path):
    cp = ControlPlane(SqlControlPlaneStore(f"sqlite:///{tmp_path / 'cp.db'}", create_tables=True))
    from mak4i.config import build_change_feed

    feed = build_change_feed(cp)
    assert isinstance(feed, SqlChangeFeed)
    engine = MAK4IEngine(
        LocalJSONStore(tmp_path / "artifacts"), authorizer=Authorizer(cp), control_plane=cp,
        resolutions=SqlResolutionStore(cp._store.engine), changes=feed,
    )
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P").project_id
    cp.grant(actor=owner, principal_id=owner.principal_id, project_id=project, permissions=["read", "write", "resolve"])
    agent = cp.create_principal(actor=owner, organization_id=org.organization_id, type="agent",
                                display_name="Red bot", agent_id="red-bot")
    cp.grant(actor=owner, principal_id=agent.principal_id, project_id=project, permissions=["read", "write"])
    reader = cp.create_principal(actor=owner, organization_id=org.organization_id, type="human", display_name="R")
    cp.grant(actor=owner, principal_id=reader.principal_id, project_id=project, permissions=["read"])
    other_org, stranger = cp.onboard_organization(organization_name="Other", owner_display_name="S")
    other_project = cp.create_project(actor=stranger, organization_id=other_org.organization_id, name="Q").project_id
    cp.grant(actor=stranger, principal_id=stranger.principal_id, project_id=other_project, permissions=["read", "write"])

    class W:
        pass

    x = W()
    x.__dict__.update(cp=cp, feed=feed, engine=engine, org=org.organization_id, project=project, owner=owner,
                      agent=agent, reader=reader, stranger=stranger, other_org=other_org.organization_id,
                      other_project=other_project, tmp=tmp_path)
    return x


def _create(w, artifact_id, artifact_type, who=None, key="primary"):
    return w.engine.create_artifact(principal=who or w.agent, project=w.project, artifact_id=artifact_id,
                                    artifact_type=artifact_type, title="T", content=f"SECRET {artifact_id}",
                                    subject_key=key)


def _events(w, after=0):
    return w.feed.read(w.org, w.project, after=after, limit=500).events


# -- F1 / F2 / F3: one event per committed change, no content, conflict ids ----


@pytest.mark.parametrize("artifact_type", TYPES)
def test_every_committed_change_has_one_event_and_nothing_else(w, artifact_type):  # F1, F2
    red = _create(w, "red", artifact_type)
    new = w.engine.supersede_artifact(principal=w.agent, project=w.project, old_id="red", content="v2", reason="r")
    w.cp.archive_project(actor=w.owner, project_id=w.project)
    events = _events(w)
    assert [e.kind for e in events] == ["record.created", "record.superseded", "project.archived"]
    created, superseded, archived = events
    assert (created.artifact_id, created.lineage_id, created.artifact_type) == ("red", "red", artifact_type)
    assert created.actor.principal_id == w.agent.principal_id and created.actor.agent_id == "red-bot"
    assert (superseded.artifact_id, superseded.previous_artifact_id) == (new.artifact_id, red.artifact_id)
    assert archived.actor.principal_id == w.owner.principal_id
    validator = _validator("change-event.schema.json")
    for event in events:
        body = event.to_protocol()
        assert not list(validator.iter_errors(body)), body
        assert "SECRET" not in json.dumps(body) and "content" not in body


@pytest.mark.parametrize("artifact_type", TYPES)
def test_refused_or_failed_writes_produce_no_event(w, artifact_type):  # F1
    _create(w, "red", artifact_type)
    before = w.feed.watermark(w.org, w.project)
    with pytest.raises(Exception):
        _create(w, "red", artifact_type)  # artifact_exists
    with pytest.raises(AccessDeniedError):
        _create(w, "blue", artifact_type, who=w.reader)
    with pytest.raises(ValidationFailedError):
        w.engine.supersede_artifact(principal=w.agent, project=w.project, old_id="red", content="x", reason=" ")
    assert [e.kind for e in _events(w)] == ["record.created"]
    assert w.feed.watermark(w.org, w.project) >= before
    assert not w.feed.pending_events()


@pytest.mark.parametrize("artifact_type", TYPES)
def test_conflict_candidates_and_resolution_events(w, artifact_type):  # F1, F3
    _create(w, "red", artifact_type)
    _create(w, "yellow", artifact_type, who=w.owner)
    (conflict,) = w.engine.get_current(principal=w.owner, project=w.project).conflicts
    created = _events(w)
    assert created[0].conflict_id is None and created[1].conflict_id == conflict.conflict_id
    record = w.engine.resolve_conflict(
        principal=w.owner, project=w.project, conflict_id=conflict.conflict_id,
        candidate_artifact_ids=["red", "yellow"], action="merge", into_artifact_id="red",
        content="RED and YELLOW", reason="both",
    )
    after = _events(w, after=created[-1].sequence)
    kinds = [e.kind for e in after]
    assert kinds == ["record.superseded", "record.withdrawn", "resolution.completed"]
    assert all(e.conflict_id == conflict.conflict_id and e.resolution_id == record.resolution_id for e in after)
    assert after[0].previous_artifact_id == "red" and after[1].artifact_id == "yellow"
    assert after[2].actor.principal_id == w.owner.principal_id


# -- F4 / F5: ordering, watermark, reconciliation -------------------------------------


def test_readers_never_see_past_a_pending_event(w):  # F4
    first = w.feed.begin(w.org, w.project, "record.created", verify={"type": "artifact_exists", "artifact_id": "a"},
                         artifact_id="a", lineage_id="a", artifact_type="t")
    second = w.feed.begin(w.org, w.project, "record.created", verify={"type": "artifact_exists", "artifact_id": "b"},
                          artifact_id="b", lineage_id="b", artifact_type="t")
    w.feed.commit(second)  # commits out of order
    assert w.feed.watermark(w.org, w.project) == 0 and _events(w) == []
    w.feed.commit(first)
    assert [e.artifact_id for e in _events(w)] == ["a", "b"] and w.feed.watermark(w.org, w.project) == 2


@pytest.mark.parametrize("artifact_type", TYPES)
def test_crash_between_write_and_event_is_reconciled(w, artifact_type, monkeypatch):  # F5
    real_commit = w.feed.commit

    def die(*_a, **_k):
        raise SystemExit("process died after the record write")

    monkeypatch.setattr(w.feed, "commit", die)
    with pytest.raises(SystemExit):
        _create(w, "red", artifact_type)
    monkeypatch.setattr(w.feed, "commit", real_commit)
    orphan = w.feed.begin(w.org, w.project, "record.created", verify={"type": "artifact_exists", "artifact_id": "never"},
                          artifact_id="never", lineage_id="never", artifact_type=artifact_type)
    assert w.feed.watermark(w.org, w.project) == 0  # the stuck pending event holds the feed
    counts = w.engine.reconcile_change_feed()
    assert counts == {"committed": 1, "abandoned": 1}
    assert [e.artifact_id for e in _events(w)] == ["red"]
    assert orphan.sequence > 1 and not w.feed.pending_events()


def test_periodic_reconciliation_only_decides_stale_events(w):  # F5
    w.feed.begin(w.org, w.project, "record.created", verify={"type": "artifact_exists", "artifact_id": "x"},
                 artifact_id="x", lineage_id="x", artifact_type="t")
    assert w.engine.reconcile_change_feed(older_than=timedelta(minutes=1)) == {"committed": 0, "abandoned": 0}
    assert w.engine.reconcile_change_feed(older_than=timedelta(seconds=0)) == {"committed": 0, "abandoned": 1}


# -- F6 / F7: retention and the tool contract ------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_paging_cursor_expiry_and_validation(w, artifact_type):  # F6, F7
    for i in range(5):
        _create(w, f"r{i}", artifact_type, key=f"k{i}")
    start = w.feed.read(w.org, w.project, after=None)
    assert start.events == [] and start.next == start.watermark == 5
    page = w.feed.read(w.org, w.project, after=0, limit=2)
    assert [e.sequence for e in page.events] == [1, 2] and page.more and page.next == 2
    rest = w.feed.read(w.org, w.project, after=page.next, limit=10)
    assert [e.sequence for e in rest.events] == [3, 4, 5] and not rest.more and rest.next == 5
    with pytest.raises(ValidationFailedError):
        w.feed.read(w.org, w.project, after=6)
    with pytest.raises(ValidationFailedError):
        w.feed.read(w.org, w.project, after=0, limit=501)
    # Retention: prune everything (backdate), then an old cursor has expired.
    with w.cp._store.engine.begin() as conn:
        from sqlalchemy import update

        from mak4i.identity import schema
        conn.execute(update(schema.change_events).values(created_at=schema.change_events.c.created_at - timedelta(days=30)))
    assert w.feed.prune(retention=timedelta(days=1)) == 5  # minimum retention is 7 days; these are 30 days old
    with pytest.raises(CursorExpiredError):
        w.feed.read(w.org, w.project, after=2)
    assert w.feed.read(w.org, w.project, after=5).events == []


def test_retention_is_at_least_seven_days(w):  # F6
    _create(w, "r", TYPES[0])
    assert w.feed.prune(retention=timedelta(seconds=0)) == 0  # fresh events survive any configured value below 7 days


# -- F8: change_sequence -------------------------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_read_current_then_follow_misses_nothing(w, artifact_type):  # F8
    _create(w, "red", artifact_type)
    package = w.engine.get_current(principal=w.owner, project=w.project)
    assert package.change_sequence == 1
    _create(w, "blue", artifact_type, key="other")
    batch = w.engine.get_changes(principal=w.owner, project=w.project, after=package.change_sequence)
    assert [e.artifact_id for e in batch.events] == ["blue"]
    # Full schema validation of `change_sequence` in a real (credential-
    # authenticated) result: tests/test_protocol_conformance.py.
    assert isinstance(package.model_dump(mode="json", by_alias=True)["change_sequence"], int)


def test_changes_require_read(w):  # F7
    with pytest.raises(AccessDeniedError):
        w.engine.get_changes(principal=w.stranger, project=w.project)


# -- F9–F14 over MCP --------------------------------------------------------------------------


def _as(principal):
    return current_principal.set(principal), current_auth.set(None)


def _reset(tokens):
    current_principal.reset(tokens[0])
    current_auth.reset(tokens[1])


def test_whoami_claims_the_change_feed_profile_and_tool_contract(w):  # profile, F7
    server = build_server(w.engine)
    tokens = _as(w.owner)
    try:
        async def run():
            async with Client(server=server) as client:
                who = (await client.call_tool("mak4i_whoami", {})).structured_content
                start = (await client.call_tool("mak4i_changes", {"project": w.project})).structured_content
                bad = (await client.call_tool("mak4i_changes", {"project": w.project, "after": 99})).structured_content
                return who, start, bad

        who, start, bad = anyio.run(run)
    finally:
        _reset(tokens)
    assert "server-change-feed" in who["protocol"]["profiles"]
    assert start == {"events": [], "next": 0, "watermark": 0, "more": False}
    assert bad["error"]["code"] == "validation_error"


@pytest.mark.parametrize("artifact_type", TYPES)
def test_resources_list_read_and_refusals(w, artifact_type):  # F9, F14
    _create(w, "red", artifact_type)
    server = build_server(w.engine)
    tokens = _as(w.reader)
    mine = current_uri(w.org, w.project)
    try:
        async def run():
            async with Client(server=server) as client:
                listed = [str(r.uri) for r in (await client.list_resources()).resources]
                templates = [t.uri_template for t in (await client.list_resource_templates()).resource_templates]
                current = json.loads((await client.read_resource(mine)).contents[0].text)
                record = json.loads((await client.read_resource(record_uri(w.org, w.project, "red"))).contents[0].text)
                errors = []
                for uri in (record_uri(w.org, w.project, "missing"), current_uri(w.other_org, w.other_project),
                            current_uri(w.org, w.other_project), "mak4i://organizations/x/projects/y/../current"):
                    try:
                        await client.read_resource(uri)
                        errors.append(None)
                    except Exception as exc:
                        errors.append(str(exc))
                return listed, templates, current, record, errors

        listed, templates, current, record, errors = anyio.run(run)
    finally:
        _reset(tokens)
    assert listed == [mine] and len(templates) == 3
    assert current["change_sequence"] == 1 and current["artifacts"][0]["artifact_id"] == "red"
    assert record["artifact_id"] == "red"
    assert len(set(errors)) == 1 and errors[0] is not None  # missing and unreadable look the same


@pytest.mark.parametrize("artifact_type", TYPES)
def test_listen_stream_notifies_with_uris_only(w, artifact_type):  # F10, F11 (2026-07-28)
    server = build_server(w.engine)
    tokens = _as(w.owner)
    uri = current_uri(w.org, w.project)
    try:
        async def run():
            async with Client(server=server) as client:
                async with client.listen(resource_subscriptions=[uri]) as sub:
                    await client.call_tool("mak4i_create", {"project": w.project, "artifact_id": "red",
                                                            "artifact_type": artifact_type, "title": "t", "content": "SECRET"})
                    with anyio.fail_after(10):
                        async for event in sub:
                            return event

        event = anyio.run(run)
    finally:
        _reset(tokens)
    assert event.uri == uri and "SECRET" not in repr(event)


def test_subscribing_to_an_unreadable_uri_is_refused(w):  # F10, F14
    server = build_server(w.engine)
    tokens = _as(w.owner)
    try:
        async def run():
            async with Client(server=server) as client:
                try:
                    async with client.listen(resource_subscriptions=[current_uri(w.other_org, w.other_project)]):
                        return "accepted"
                except Exception as exc:
                    return type(exc).__name__

        outcome = anyio.run(run)
    finally:
        _reset(tokens)
    assert outcome != "accepted"


@pytest.mark.parametrize("artifact_type", TYPES)
def test_legacy_subscribe_notifies(w, artifact_type):  # F10, F11 (2025-11-25)
    server = build_server(w.engine)
    tokens = _as(w.owner)
    uri = current_uri(w.org, w.project)
    received = []

    async def handler(message):
        root = getattr(message, "root", message)
        if getattr(root, "method", None) == "notifications/resources/updated":
            received.append(str(root.params.uri))

    try:
        async def run():
            async with Client(server=server, mode="legacy", message_handler=handler) as client:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    await client.subscribe_resource(uri)
                await client.call_tool("mak4i_create", {"project": w.project, "artifact_id": "red",
                                                        "artifact_type": artifact_type, "title": "t", "content": "c"})
                with anyio.fail_after(10):
                    while not received:
                        await anyio.sleep(0.05)

        anyio.run(run)
    finally:
        _reset(tokens)
    assert received[0] == uri


def test_delivery_rechecks_access_and_list_changed(w):  # F11, F12, F13
    server = build_server(w.engine)
    notifier = server._mak4i_resources.notifier
    sent = []

    async def send(kind, uri):
        sent.append((kind, uri))

    uris = {current_uri(w.org, w.project), conflict_uri(w.org, w.project, "cfl_" + "0" * 32)}
    notifier.subscribe(w.reader, None, send, uris, list_changed=True, sid="reader")
    _create(w, "red", TYPES[0])
    assert anyio.run(notifier.run_once) == 1 and sent == [("updated", current_uri(w.org, w.project))]
    # The reader loses its grant: nothing more about this project, and the
    # readable-project set changed.
    w.cp.revoke_grant(actor=w.owner, principal_id=w.reader.principal_id, project_id=w.project)
    sent.clear()
    _create(w, "blue", TYPES[1], key="other")
    anyio.run(notifier.run_once)
    assert sent == [("list_changed", None)]


def test_in_memory_feed_is_not_claimed(tmp_path):  # profile only with a durable feed
    from mak4i.identity.memory_store import InMemoryControlPlaneStore

    cp = ControlPlane(InMemoryControlPlaneStore())
    engine = MAK4IEngine(LocalJSONStore(tmp_path / "a"), authorizer=Authorizer(cp), control_plane=cp,
                         changes=InMemoryChangeFeed())
    assert not engine.change_feed.durable
    from mak4i.mcp_server import _deployment_profiles

    assert "server-change-feed" not in _deployment_profiles(change_feed=False)


def test_background_maintenance_decides_stale_pending_events(w):  # F5 (writer died in another process)
    import time

    from mak4i.mcp_server import start_change_feed_maintenance

    w.feed.begin(w.org, w.project, "record.created", verify={"type": "artifact_exists", "artifact_id": "ghost"},
                 artifact_id="ghost", lineage_id="ghost", artifact_type="t")
    start_change_feed_maintenance(w.engine, retention_days=7, interval=0.05, stale_after=0.0)
    deadline = time.monotonic() + 5
    while w.feed.pending_events() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not w.feed.pending_events()
    assert w.feed.watermark(w.org, w.project) == 1 and _events(w) == []  # abandoned, never visible


@pytest.mark.parametrize("artifact_type", TYPES)
def test_cli_changes_command_and_archive_event(tmp_path, monkeypatch, capsys, artifact_type):  # §10.3 mapping, F1
    from mak4i import cli

    monkeypatch.setenv("MAK4I_HOME", str(tmp_path / ".mak4i"))
    monkeypatch.setenv("MAK4I_STORE", "local")
    monkeypatch.setenv("MAK4I_LOCAL_STORE_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{tmp_path / 'cp.db'}")
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", "1")
    assert cli.main(["org", "create", "--name", "A", "--owner-display-name", "O"]) == 0
    org = json.loads(capsys.readouterr().out)
    owner = org["owner"]["principal_id"]
    assert cli.main(["project", "create", "--actor", owner, "--organization-id",
                     org["organization"]["organization_id"], "--name", "p"]) == 0
    project = json.loads(capsys.readouterr().out)["project_id"]
    assert cli.main(["grant", "create", "--actor", owner, "--principal-id", owner, "--project-id", project,
                     "--permissions", "read,write"]) == 0
    capsys.readouterr()
    assert cli.main(["create", "--principal", owner, "--project", project, "--artifact-id", "r1",
                     "--artifact-type", artifact_type, "--title", "t", "--content", "c"]) == 0
    capsys.readouterr()
    assert cli.main(["changes", "--principal", owner, "--project", project]) == 0
    start = json.loads(capsys.readouterr().out)
    assert start == {"events": [], "next": 1, "watermark": 1, "more": False}
    assert cli.main(["project", "archive", "--actor", owner, "--project-id", project]) == 0
    capsys.readouterr()
    assert cli.main(["changes", "--principal", owner, "--project", project, "--after", "0"]) == 1  # archived: no access
    from mak4i.config import build_change_feed, build_control_plane_from_env

    feed = build_change_feed(build_control_plane_from_env())
    events = feed.read(org["organization"]["organization_id"], project, after=0).events
    assert [e.kind for e in events] == ["record.created", "project.archived"]
    assert events[1].actor.principal_id == owner and events[1].actor.auth_method == "operator"
