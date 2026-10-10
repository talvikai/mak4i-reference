"""Deterministic subject conflicts and explicit resolution (MAK-0004 Part A,
MAK-0005 Part A).

Each test names the CONFORMANCE.md criterion it covers (C1–C15, P2–P7).
Per CLAUDE.md's testing discipline every resolver path runs on two distinct
artifact types (a caching decision and a database decision) through
identical code.
"""

from __future__ import annotations

import threading

import pytest

from mak4i.api import MAK4IEngine, StaleHeadError
from mak4i.audit import AuditLogger
from mak4i.errors import (
    ConflictChangedError,
    ConflictNotOpenError,
    NotFoundError,
    SubjectCollisionError,
    SubjectInConflictError,
    ValidationFailedError,
)
from mak4i.identity import AccessDeniedError, Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.resolution.records import InMemoryResolutionStore
from mak4i.store.local_json import LocalJSONStore

TYPES = ["caching_decision", "database_decision"]


@pytest.fixture
def world(tmp_path):
    cp = ControlPlane(InMemoryControlPlaneStore())
    audit = AuditLogger()
    store = LocalJSONStore(tmp_path / "artifacts")
    resolutions = InMemoryResolutionStore()
    engine = MAK4IEngine(
        store, audit=audit, authorizer=Authorizer(cp, audit), control_plane=cp, resolutions=resolutions
    )
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P")

    def principal(name, permissions, *, agent_id=None):
        p = cp.create_principal(
            actor=owner,
            organization_id=org.organization_id,
            type="agent" if agent_id else "human",
            display_name=name,
            agent_id=agent_id,
        )
        cp.grant(actor=owner, principal_id=p.principal_id, project_id=project.project_id, permissions=permissions)
        return p

    return {
        "engine": engine,
        "store": store,
        "cp": cp,
        "owner": owner,
        "org": org.organization_id,
        "project": project.project_id,
        "resolutions": resolutions,
        "red_bot": principal("Red bot", ["read", "write"], agent_id="red-bot"),
        "yellow_bot": principal("Yellow bot", ["read", "write"], agent_id="yellow-bot"),
        "green_bot": principal("Green bot", ["read", "write"], agent_id="green-bot"),
        "human": principal("Alice", ["read", "write", "resolve"]),
        "writer_only": principal("Writer", ["read", "write"]),
        "resolve_only": principal("Resolve-only", ["resolve"]),
    }


def _create(w, who, artifact_id, artifact_type, *, subject_key="primary", content=None, tags=None):
    return w["engine"].create_artifact(
        principal=w[who],
        project=w["project"],
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        title=artifact_id,
        content=content or f"{artifact_id} content",
        tags=tags or [],
        subject_key=subject_key,
    )


def _current(w, who="human", **filters):
    return w["engine"].get_current(principal=w[who], project=w["project"], **filters)


def _resolve(w, conflict, who="human", **kwargs):
    kwargs.setdefault("candidate_artifact_ids", [c.artifact_id for c in conflict.candidates])
    kwargs.setdefault("reason", "decided by the team")
    return w["engine"].resolve_conflict(
        principal=w[who], project=w["project"], conflict_id=conflict.conflict_id, **kwargs
    )


def _red_yellow(w, artifact_type):
    _create(w, "red_bot", "red", artifact_type, content="Use RED")
    _create(w, "yellow_bot", "yellow", artifact_type, content="Use YELLOW")
    (conflict,) = _current(w).conflicts
    return conflict


# -- C1, C2: no conflict ---------------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_same_lineage_update_supersedes_without_conflict(world, artifact_type):  # C1 / P1
    _create(world, "red_bot", "red", artifact_type, content="Use RED")
    v2 = world["engine"].supersede_artifact(
        principal=world["red_bot"], project=world["project"], old_id="red", content="Use RED v2", reason="update"
    )
    package = _current(world)
    assert package.conflicts == [] and [a.artifact_id for a in package.artifacts] == [v2.artifact_id]
    assert v2.version == "2.0" and v2.subject_key == "primary"


@pytest.mark.parametrize("artifact_type", TYPES)
def test_different_subjects_and_shared_tags_coexist(world, artifact_type):  # C2
    _create(world, "red_bot", "a", artifact_type, subject_key="primary", tags=["cache"])
    _create(world, "yellow_bot", "b", artifact_type, subject_key="secondary", tags=["cache"])
    _create(world, "green_bot", "c", artifact_type, subject_key=None, tags=["cache"])
    other_type = [t for t in TYPES if t != artifact_type][0]
    _create(world, "green_bot", "d", other_type, subject_key="primary", tags=["cache"])
    package = _current(world)
    assert package.conflicts == []
    assert {a.artifact_id for a in package.artifacts} == {"a", "b", "c", "d"}


# -- C3–C6: detection and retrieval --------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_competing_red_and_yellow_records_form_an_explicit_conflict(world, artifact_type):  # C3
    conflict = _red_yellow(world, artifact_type)
    package = _current(world)
    assert package.artifacts == []  # never quietly the newest
    assert conflict.state == "open" and conflict.generation == 0
    assert conflict.conflict_id.startswith("cfl_") and len(conflict.conflict_id) == 36
    assert conflict.identical_content is False and conflict.hidden_candidate_count == 0
    assert [c.artifact_id for c in conflict.candidates] == ["red", "yellow"]
    red, yellow = conflict.candidates
    assert (red.content, yellow.content) == ("Use RED", "Use YELLOW")
    assert (red.provenance.agent_id, yellow.provenance.agent_id) == ("red-bot", "yellow-bot")
    assert red.created_by == world["red_bot"].principal_id
    assert "2 lineage(s) claim" in conflict.reason
    assert "resolve_conflict" in package.instruction


@pytest.mark.parametrize("artifact_type", TYPES)
def test_a_third_contributor_extends_the_same_conflict(world, artifact_type):  # C4
    conflict = _red_yellow(world, artifact_type)
    _create(world, "green_bot", "green", artifact_type, content="Use GREEN")
    (extended,) = _current(world).conflicts
    assert extended.conflict_id == conflict.conflict_id
    assert [c.artifact_id for c in extended.candidates] == ["green", "red", "yellow"]


@pytest.mark.parametrize("artifact_type", TYPES)
def test_identical_content_still_conflicts_and_says_so(world, artifact_type):  # C5
    _create(world, "red_bot", "one", artifact_type, content="Same")
    _create(world, "yellow_bot", "two", artifact_type, content="Same")
    (conflict,) = _current(world).conflicts
    assert conflict.identical_content is True


@pytest.mark.parametrize("artifact_type", TYPES)
def test_filters_never_hide_one_side_of_a_conflict(world, artifact_type):  # C6
    _create(world, "red_bot", "red", artifact_type, tags=["fast"])
    _create(world, "yellow_bot", "yellow", artifact_type, tags=["cheap"])
    package = _current(world, tags=["fast"])
    assert package.artifacts == []  # before v2, "red" was returned here
    assert [c.artifact_id for c in package.conflicts[0].candidates] == ["red", "yellow"]
    other_type = [t for t in TYPES if t != artifact_type][0]
    assert _current(world, artifact_type=other_type).conflicts == []
    assert len(_current(world, artifact_type=artifact_type).conflicts) == 1


# -- C7: the three resolutions ----------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_select_winner_withdraws_the_others_and_keeps_the_winner(world, artifact_type):  # C7
    conflict = _red_yellow(world, artifact_type)
    winner_before = world["store"].get(world["org"], world["project"], "red")[0]
    record = _resolve(world, conflict, action="select_winner", winner_artifact_id="red")
    assert record.state == "completed" and record.permission == "resolve"
    assert record.actor.principal_id == world["human"].principal_id
    assert [(e.artifact_id, e.effect) for e in record.effects] == [("red", "unchanged"), ("yellow", "withdrawn")]
    assert [(h.subject_key, h.artifact_id) for h in record.resulting_heads] == [("primary", "red")]
    package = _current(world)
    assert package.conflicts == [] and [a.artifact_id for a in package.artifacts] == ["red"]
    assert world["store"].get(world["org"], world["project"], "red")[0] == winner_before
    yellow = world["store"].get(world["org"], world["project"], "yellow")[0]
    assert yellow.status == "withdrawn" and yellow.resolution_id == record.resolution_id
    assert yellow.content == "Use YELLOW" and yellow.subject_key == "primary"  # original preserved
    assert package.integrity_errors == []  # a withdrawn lineage is not an integrity error


@pytest.mark.parametrize("artifact_type", TYPES)
def test_merge_creates_one_authoritative_version(world, artifact_type):  # C7
    conflict = _red_yellow(world, artifact_type)
    record = _resolve(
        world, conflict, action="merge", into_artifact_id="yellow",
        content="RED in production, YELLOW in development", title="Cache policy",
    )
    (head,) = record.resulting_heads
    merged = world["store"].get(world["org"], world["project"], head.artifact_id)[0]
    assert merged.lineage_id == "yellow" and merged.version == "2.0"
    assert merged.content == "RED in production, YELLOW in development" and merged.title == "Cache policy"
    assert merged.merged_from == ["red", "yellow"] and merged.resolution_id == record.resolution_id
    assert merged.provenance.principal_id == world["human"].principal_id
    assert merged.subject_key == "primary"
    package = _current(world)
    assert [a.artifact_id for a in package.artifacts] == [merged.artifact_id]
    assert world["store"].get(world["org"], world["project"], "red")[0].status == "withdrawn"
    assert world["store"].get(world["org"], world["project"], "yellow")[0].status == "superseded"


@pytest.mark.parametrize("artifact_type", TYPES)
def test_separate_subjects_assigns_every_candidate(world, artifact_type):  # C7
    conflict = _red_yellow(world, artifact_type)
    record = _resolve(
        world, conflict, action="separate_subjects",
        assignments=[{"artifact_id": "red", "subject_key": "primary"},
                     {"artifact_id": "yellow", "subject_key": " session "}],
    )
    assert {(h.subject_key, h.artifact_id.startswith("yellow")) for h in record.resulting_heads} == {
        ("primary", False), ("session", True)}
    package = _current(world)
    assert package.conflicts == []
    by_key = {a.subject_key: a for a in package.artifacts}
    assert by_key["primary"].artifact_id == "red"
    assert by_key["session"].lineage_id == "yellow" and by_key["session"].content == "Use YELLOW"
    history = world["engine"].get_history(principal=world["human"], project=world["project"], lineage_id="yellow")
    assert [v.subject_key for v in history] == ["primary", "session"]  # historical assignment kept


@pytest.mark.parametrize("artifact_type", TYPES)
def test_separate_subjects_must_assign_not_dismiss(world, artifact_type):  # C8
    conflict = _red_yellow(world, artifact_type)
    _create(world, "green_bot", "other", artifact_type, subject_key="taken")
    (conflict,) = _current(world).conflicts
    bad = [
        ([{"artifact_id": "red", "subject_key": "x"}], ValidationFailedError),  # missing a candidate
        ([{"artifact_id": "red", "subject_key": "x"}, {"artifact_id": "yellow", "subject_key": "x"}], ValidationFailedError),
        ([{"artifact_id": "red", "subject_key": "primary"}, {"artifact_id": "yellow", "subject_key": "primary"}], ValidationFailedError),
        ([{"artifact_id": "red", "subject_key": "x"}, {"artifact_id": "yellow", "subject_key": "   "}], ValidationFailedError),
        ([{"artifact_id": "red", "subject_key": "x"}, {"artifact_id": "yellow", "subject_key": "taken"}], SubjectCollisionError),
    ]
    for assignments, error in bad:
        with pytest.raises(error):
            _resolve(world, conflict, action="separate_subjects", assignments=assignments)
    assert len(_current(world).conflicts) == 1  # nothing changed


# -- C9: permission ---------------------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_resolution_needs_resolve_and_read(world, artifact_type):  # C9
    conflict = _red_yellow(world, artifact_type)
    for who in ("writer_only", "resolve_only"):
        with pytest.raises(AccessDeniedError):
            _resolve(world, conflict, who=who, action="select_winner", winner_artifact_id="red")
    assert len(_current(world).conflicts) == 1
    assert world["resolutions"].for_project(world["org"], world["project"]) == []


def test_an_agent_may_resolve_when_granted(world):  # C9 (no human-only rule)
    conflict = _red_yellow(world, TYPES[0])
    world["cp"].grant(
        actor=world["owner"], principal_id=world["green_bot"].principal_id,
        project_id=world["project"], permissions=["read", "write", "resolve"],
    )
    record = _resolve(world, conflict, who="green_bot", action="select_winner", winner_artifact_id="yellow")
    assert record.actor.agent_id == "green-bot"


# -- C10, C11: stale, retries, concurrency ----------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_stale_candidate_set_is_rejected(world, artifact_type):  # C10
    conflict = _red_yellow(world, artifact_type)
    _create(world, "green_bot", "green", artifact_type)
    with pytest.raises(ConflictChangedError):
        _resolve(world, conflict, action="select_winner", winner_artifact_id="red")  # saw only red/yellow
    with pytest.raises(ConflictChangedError):
        _resolve(world, conflict, action="select_winner", winner_artifact_id="red",
                 candidate_artifact_ids=["red", "yellow", "green", "extra"])


@pytest.mark.parametrize("artifact_type", TYPES)
def test_retries_are_idempotent_and_second_resolutions_refused(world, artifact_type):  # C10
    conflict = _red_yellow(world, artifact_type)
    first = _resolve(world, conflict, action="select_winner", winner_artifact_id="red", idempotency_key="key-00000001")
    again = _resolve(world, conflict, action="select_winner", winner_artifact_id="red", idempotency_key="key-00000001")
    assert again == first
    with pytest.raises(ConflictNotOpenError):
        _resolve(world, conflict, action="select_winner", winner_artifact_id="yellow", idempotency_key="key-00000002")
    with pytest.raises(ConflictNotOpenError):
        _resolve(world, conflict, action="select_winner", winner_artifact_id="red")  # no key
    with pytest.raises(NotFoundError):
        world["engine"].get_conflict(principal=world["human"], project=world["project"], conflict_id="cfl_" + "f" * 32)


@pytest.mark.parametrize("artifact_type", TYPES)
def test_concurrent_resolutions_complete_exactly_once(world, artifact_type):  # C11
    conflict = _red_yellow(world, artifact_type)
    outcomes, barrier = [], threading.Barrier(4)

    def attempt(winner):
        barrier.wait()
        try:
            outcomes.append(("ok", _resolve(world, conflict, action="select_winner", winner_artifact_id=winner)))
        except (ConflictNotOpenError, ConflictChangedError) as exc:
            outcomes.append(("refused", type(exc).__name__))

    threads = [threading.Thread(target=attempt, args=(w,)) for w in ("red", "yellow", "red", "yellow")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    completed = [o for o in outcomes if o[0] == "ok"]
    assert len(completed) == 1, outcomes
    winner = completed[0][1].parameters["winner_artifact_id"]
    package = _current(world)
    assert package.conflicts == [] and [a.artifact_id for a in package.artifacts] == [winner]


@pytest.mark.parametrize("artifact_type", TYPES)
def test_concurrent_supersessions_of_one_head(world, artifact_type):  # P3
    _create(world, "red_bot", "red", artifact_type)
    outcomes, barrier = [], threading.Barrier(5)

    def attempt(i):
        barrier.wait()
        try:
            world["engine"].supersede_artifact(
                principal=world["red_bot"], project=world["project"], old_id="red", content=f"v{i}", reason="r"
            )
            outcomes.append("ok")
        except (StaleHeadError, Exception) as exc:  # noqa: BLE001 - classify below
            outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert outcomes.count("ok") == 1, outcomes
    assert set(outcomes) - {"ok"} <= {"StaleHeadError", "ArtifactNotActiveError", "ArtifactAlreadyExistsError"}
    package = _current(world)
    assert len(package.artifacts) == 1 and package.integrity_errors == []


@pytest.mark.parametrize("artifact_type", TYPES)
def test_candidate_change_mid_resolution_aborts_and_is_recorded(world, artifact_type, monkeypatch):  # §A8.3
    conflict = _red_yellow(world, artifact_type)
    engine = world["engine"]
    original = engine._withdraw

    def interfering_withdraw(org, project, artifact_id, record):
        # A concurrent supersede of this candidate wins the race (it got past
        # the pending-resolution check before the record was written).
        artifact, token = engine._store.get(org, project, artifact_id)
        engine._store.put_if_match(
            artifact.model_copy(update={"status": "superseded", "superseded_by": artifact_id + "-002"}),
            expected_version_token=token,
        )
        engine._store.put_new(artifact.model_copy(update={
            "artifact_id": artifact_id + "-002", "version": "2.0", "supersedes": artifact_id}))
        return original(org, project, artifact_id, record)

    monkeypatch.setattr(engine, "_withdraw", interfering_withdraw)
    with pytest.raises(ConflictChangedError):
        _resolve(world, conflict, action="select_winner", winner_artifact_id="red")
    (record,) = world["resolutions"].for_project(world["org"], world["project"])
    assert record.state == "aborted"
    monkeypatch.setattr(engine, "_withdraw", original)
    (still,) = _current(world).conflicts
    assert still.conflict_id == conflict.conflict_id  # aborted resolutions don't advance the generation
    assert [c.artifact_id for c in still.candidates] == ["red", "yellow-002"]
    assert _resolve(world, still, action="select_winner", winner_artifact_id="red").state == "completed"


@pytest.mark.parametrize("artifact_type", TYPES)
def test_interrupted_resolution_is_seen_as_resolving_and_recovered(world, artifact_type, monkeypatch):  # §A8.2
    _create(world, "red_bot", "a", artifact_type)
    _create(world, "yellow_bot", "b", artifact_type)
    _create(world, "green_bot", "c", artifact_type)
    (conflict,) = _current(world).conflicts
    engine = world["engine"]
    original = engine._withdraw
    calls = {"n": 0}

    def crash_after_first(org, project, artifact_id, record):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt("process died")
        return original(org, project, artifact_id, record)

    monkeypatch.setattr(engine, "_withdraw", crash_after_first)
    with pytest.raises(KeyboardInterrupt):
        _resolve(world, conflict, action="select_winner", winner_artifact_id="a")
    monkeypatch.setattr(engine, "_withdraw", original)

    # Half applied: only "a" and "c" are heads, but readers still see the
    # subject as conflicted, not as resolved to the survivors.
    (resolving,) = _current(world).conflicts
    assert resolving.state == "resolving" and resolving.resolution.state == "pending"
    with pytest.raises(ConflictChangedError):
        world["engine"].supersede_artifact(
            principal=world["red_bot"], project=world["project"], old_id="a", content="x", reason="y"
        )
    (finished,) = engine.recover_pending_resolutions()
    assert finished.state == "completed"
    package = _current(world)
    assert package.conflicts == [] and [a.artifact_id for a in package.artifacts] == ["a"]


# -- C12–C14: after resolution -------------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_new_contribution_after_resolution_is_a_new_conflict(world, artifact_type):  # C12
    conflict = _red_yellow(world, artifact_type)
    _resolve(world, conflict, action="select_winner", winner_artifact_id="red")
    _create(world, "green_bot", "green", artifact_type, content="Use GREEN")
    (again,) = _current(world).conflicts
    assert again.conflict_id != conflict.conflict_id and again.generation == 1
    assert [c.artifact_id for c in again.candidates] == ["green", "red"]


@pytest.mark.parametrize("artifact_type", TYPES)
def test_release_during_open_conflict_is_rejected(world, artifact_type):  # C13
    _red_yellow(world, artifact_type)
    with pytest.raises(SubjectInConflictError):
        world["engine"].supersede_artifact(
            principal=world["yellow_bot"], project=world["project"], old_id="yellow",
            content="x", reason="y", release_subject_key=True,
        )


@pytest.mark.parametrize("artifact_type", TYPES)
def test_complete_history_is_preserved(world, artifact_type):  # C14
    conflict = _red_yellow(world, artifact_type)
    record = _resolve(world, conflict, action="merge", into_artifact_id="red", content="merged")
    resolved = world["engine"].get_conflict(principal=world["human"], project=world["project"], conflict_id=conflict.conflict_id)
    assert resolved.state == "resolved" and resolved.resolution == record
    assert {c.content for c in resolved.candidates} == {"Use RED", "Use YELLOW"}
    listed = world["engine"].list_conflicts(principal=world["human"], project=world["project"], state="all")
    assert [c.state for c in listed] == ["resolved"]
    for lineage in ("red", "yellow"):
        versions = world["engine"].get_history(principal=world["human"], project=world["project"], lineage_id=lineage)
        assert versions[0].content in ("Use RED", "Use YELLOW")


# -- C15: another client --------------------------------------------------------------


def test_conflict_and_resolution_seen_identically_from_another_client(tmp_path):  # C15
    """An agent writes over a credential, another agent over OAuth; a human
    resolves through a third connection; each sees the same state."""
    from tests.test_oauth import World

    w = World(tmp_path)
    with w.client:
        def agent(agent_id):
            p = w.cp.create_principal(
                actor=w.owner, organization_id=w.org.organization_id, type="agent",
                display_name=agent_id, agent_id=agent_id,
            )
            w.cp.grant(actor=w.owner, principal_id=p.principal_id, project_id=w.project.project_id, permissions=["read", "write"])
            return p

        red, yellow = agent("red-bot"), agent("yellow-bot")
        _cred, red_token = w.cp.issue_credential(actor=w.owner, principal_id=red.principal_id)
        yellow_token = w.tokens(principal_id=yellow.principal_id)["access_token"]
        for token, artifact_id in ((red_token, "red"), (yellow_token, "yellow")):
            _h, rpc = w.call_tool(token, "mak4i_create", {
                "project": w.project.project_id, "artifact_id": artifact_id, "artifact_type": "caching_decision",
                "title": artifact_id, "content": artifact_id.upper(), "subject_key": "primary"})
            assert rpc["result"]["isError"] is False

        views = []
        for token in (red_token, yellow_token):
            _h, rpc = w.call_tool(token, "mak4i_get_current", {"project": w.project.project_id})
            views.append(rpc["result"]["structuredContent"]["conflicts"])
        assert views[0] == views[1] and len(views[0]) == 1
        conflict = views[0][0]
        assert {c["provenance"]["auth_method"] for c in conflict["candidates"]} == {"credential", "oauth"}

        w.cp.grant(actor=w.owner, principal_id=w.owner.principal_id, project_id=w.project.project_id,
                   permissions=["read", "write", "resolve"])
        human_token = w.tokens(scope="mak4i:read mak4i:resolve")["access_token"]
        _h, rpc = w.call_tool(human_token, "mak4i_resolve_conflict", {
            "project": w.project.project_id, "conflict_id": conflict["conflict_id"],
            "candidate_artifact_ids": ["red", "yellow"], "action": "select_winner",
            "winner_artifact_id": "yellow", "reason": "human decision"})
        assert rpc["result"]["isError"] is False
        assert rpc["result"]["structuredContent"]["actor"]["principal_type"] == "human"

        _h, rpc = w.call_tool(red_token, "mak4i_get_current", {"project": w.project.project_id})
        current = rpc["result"]["structuredContent"]
        assert current["conflicts"] == [] and [a["artifact_id"] for a in current["artifacts"]] == ["yellow"]

        # Structured errors (MAK-0008 §9) for a stale and a repeated resolution.
        _h, rpc = w.call_tool(human_token, "mak4i_resolve_conflict", {
            "project": w.project.project_id, "conflict_id": conflict["conflict_id"],
            "candidate_artifact_ids": ["red", "yellow"], "action": "select_winner",
            "winner_artifact_id": "red", "reason": "again"})
        assert rpc["result"]["isError"] is True
        assert rpc["result"]["structuredContent"]["error"]["code"] == "conflict_not_open"
        _h, rpc = w.call_tool(human_token, "mak4i_resolve_conflict", {
            "project": w.project.project_id, "conflict_id": conflict["conflict_id"],
            "candidate_artifact_ids": ["red"], "action": "teleport", "reason": "x"})
        assert rpc["result"]["structuredContent"]["error"]["code"] in ("validation_error", "conflict_not_open")


# -- legacy and integrity -----------------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_legacy_records_without_subject_key_never_conflict(world, artifact_type):  # P6
    _create(world, "red_bot", "legacy-1", artifact_type, subject_key=None)
    _create(world, "yellow_bot", "legacy-2", artifact_type, subject_key=None)
    assert _current(world).conflicts == []


def test_subject_keys_are_nfc_normalized():  # P5
    from mak4i.models import normalize_subject_key

    composed, decomposed = "café", "café"
    assert normalize_subject_key(decomposed) == normalize_subject_key(composed) == composed
    assert normalize_subject_key("  Primary  ") == "Primary"  # no case folding
    with pytest.raises(ValueError):
        normalize_subject_key("x" * 513)


def test_audit_records_resolution_lifecycle(world, caplog):  # §A9
    import json
    import logging

    from mak4i.audit.logger import LOGGER_NAME

    conflict = _red_yellow(world, TYPES[0])
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    with pytest.raises(AccessDeniedError):
        _resolve(world, conflict, who="writer_only", action="select_winner", winner_artifact_id="red")
    _resolve(world, conflict, action="select_winner", winner_artifact_id="red")
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME]
    names = [e["event"] for e in events]
    assert "ACCESS_DENIED" in names and "RESOLUTION_STARTED" in names and "RESOLUTION_COMPLETED" in names
    completed = next(e for e in events if e["event"] == "RESOLUTION_COMPLETED")
    assert completed["conflict_id"] == conflict.conflict_id
