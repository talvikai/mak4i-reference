"""Crash safety of conflict resolution (MAK-0004 §A8.2).

A resolution is a pending record, then several artifact-store writes, then
the record is completed. This test kills the "process" after *every* single
store write of every resolution action and checks, at each crash point:

1. authoritative retrieval (`get_current`) never presents the subject — or
   any subject a separation is moving candidates to — as resolved while the
   resolution is pending: it is reported as a conflict in state
   `resolving`, with no candidate returned as a current record;
2. no lineage is left broken (no integrity errors);
3. recovery completes the resolution to exactly the result an
   uninterrupted resolution produces.

Runs on two artifact types (CLAUDE.md testing discipline).
"""

from __future__ import annotations

import pytest

from mak4i.api import MAK4IEngine
from mak4i.identity import Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.resolution.records import InMemoryResolutionStore
from mak4i.store.local_json import LocalJSONStore

TYPES = ["caching_decision", "database_decision"]


class Crash(BaseException):
    """Simulated process death (not an Exception, so nothing catches it)."""


class CrashingStore:
    """Delegates to a real store; raises Crash after the N-th write."""

    def __init__(self, inner):
        self.inner = inner
        self.writes = 0
        self.crash_after: int | None = None

    def _write(self, fn, *args, **kwargs):
        result = fn(*args, **kwargs)
        self.writes += 1
        if self.crash_after is not None and self.writes == self.crash_after:
            raise Crash()
        return result

    def put_new(self, *a, **k):
        return self._write(self.inner.put_new, *a, **k)

    def put_if_match(self, *a, **k):
        return self._write(self.inner.put_if_match, *a, **k)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _world(tmp_path, artifact_type):
    cp = ControlPlane(InMemoryControlPlaneStore())
    store = CrashingStore(LocalJSONStore(tmp_path / "artifacts"))
    resolutions = InMemoryResolutionStore()
    engine = MAK4IEngine(store, authorizer=Authorizer(cp), control_plane=cp, resolutions=resolutions)
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P")
    cp.grant(actor=owner, principal_id=owner.principal_id, project_id=project.project_id,
             permissions=["read", "write", "resolve"])
    for artifact_id in ("a", "b", "c"):
        engine.create_artifact(principal=owner, project=project.project_id, artifact_id=artifact_id,
                               artifact_type=artifact_type, title=artifact_id, content=f"{artifact_id} content",
                               subject_key="primary")
    return engine, store, owner, project.project_id


ACTIONS = {
    "select_winner": {"action": "select_winner", "winner_artifact_id": "b"},
    "merge": {"action": "merge", "into_artifact_id": "a", "content": "merged"},
    "separate_subjects": {"action": "separate_subjects", "assignments": [
        {"artifact_id": "a", "subject_key": "primary"},
        {"artifact_id": "b", "subject_key": "second"},
        {"artifact_id": "c", "subject_key": "third"}]},
}


def _resolve(engine, owner, project, params):
    (conflict,) = engine.get_current(principal=owner, project=project).conflicts
    return engine.resolve_conflict(principal=owner, project=project, conflict_id=conflict.conflict_id,
                                   candidate_artifact_ids=["a", "b", "c"], reason="decided", **params)


def _final_state(engine, owner, project):
    package = engine.get_current(principal=owner, project=project)
    return (
        sorted((a.subject_key, a.lineage_id, a.content) for a in package.artifacts),
        package.conflicts,
        package.integrity_errors,
    )


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("action", sorted(ACTIONS))
def test_crash_after_every_write_never_exposes_a_partial_result(tmp_path, artifact_type, action):
    params = ACTIONS[action]
    # Reference run: uninterrupted.
    engine, store, owner, project = _world(tmp_path / "reference", artifact_type)
    before = store.writes
    _resolve(engine, owner, project, params)
    total_writes = store.writes - before
    expected = _final_state(engine, owner, project)
    assert expected[1] == [] and expected[2] == []
    assert total_writes >= 2

    for crash_point in range(1, total_writes + 1):
        engine, store, owner, project = _world(tmp_path / f"crash-{crash_point}", artifact_type)
        store.crash_after = store.writes + crash_point
        with pytest.raises(Crash):
            _resolve(engine, owner, project, params)
        store.crash_after = None

        package = engine.get_current(principal=owner, project=project)
        assert package.integrity_errors == [], (crash_point, package.integrity_errors)
        reported = {c.subject_key: c for c in package.conflicts}
        assert "primary" in reported and reported["primary"].state == "resolving", crash_point
        current_subjects = {a.subject_key for a in package.artifacts}
        moving_to = {a["subject_key"] for a in params.get("assignments", [])}
        assert not (current_subjects & ({"primary"} | moving_to)), (crash_point, current_subjects)

        (record,) = engine.recover_pending_resolutions()
        assert record.state == "completed", crash_point
        assert _final_state(engine, owner, project) == expected, crash_point
