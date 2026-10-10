"""Issue #5: identity vs. classification in current/conflict resolution.

Tags are classification and search metadata only. Two current artifacts
from different lineages compete only when they share `artifact_type` AND a
non-empty `subject_key` (MVP_ARCHITECTURE.md §18). `subject_key` is stable
within a lineage and can only be given up via an explicit, audited
`release_subject_key` supersede.

Per CLAUDE.md, every resolution/supersession behavior here runs over two
distinct artifact types through the same code path.
"""

import json
import logging

import pytest

from mak4i.api import ArtifactNotActiveError, MAK4IEngine, SubjectKeyChangeError
from mak4i.audit import LOGGER_NAME, AuditLogger
from mak4i.identity import AccessDeniedError, Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.models import Artifact
from mak4i.store.local_json import LocalJSONStore

TYPES = ["requirements", "architecture_decision"]


@pytest.fixture
def world(tmp_path):
    """Two organizations. Org A has two projects; `writer` has read+write on
    both and `reader` read-only on the first. Org B has one project and its
    own writer. Nobody holds any grant across organizations."""
    control_plane = ControlPlane(InMemoryControlPlaneStore())
    audit = AuditLogger()
    store = LocalJSONStore(tmp_path / "artifacts")
    engine = MAK4IEngine(
        store, audit=audit, authorizer=Authorizer(control_plane, audit), control_plane=control_plane
    )

    org_a, owner_a = control_plane.onboard_organization(
        organization_name="Org A", owner_display_name="Owner A"
    )
    org_b, owner_b = control_plane.onboard_organization(
        organization_name="Org B", owner_display_name="Owner B"
    )
    a1 = control_plane.create_project(actor=owner_a, organization_id=org_a.organization_id, name="a1")
    a2 = control_plane.create_project(actor=owner_a, organization_id=org_a.organization_id, name="a2")
    b1 = control_plane.create_project(actor=owner_b, organization_id=org_b.organization_id, name="b1")

    def principal(owner, org, name):
        return control_plane.create_principal(
            actor=owner, organization_id=org.organization_id, type="human", display_name=name
        )

    writer = principal(owner_a, org_a, "Writer A")
    reader = principal(owner_a, org_a, "Reader A")
    writer_b = principal(owner_b, org_b, "Writer B")
    for pid in (a1.project_id, a2.project_id):
        control_plane.grant(actor=owner_a, principal_id=writer.principal_id, project_id=pid, permissions=["read", "write"])
    control_plane.grant(actor=owner_a, principal_id=reader.principal_id, project_id=a1.project_id, permissions=["read"])
    control_plane.grant(actor=owner_b, principal_id=writer_b.principal_id, project_id=b1.project_id, permissions=["read", "write"])

    return {
        "engine": engine,
        "control_plane": control_plane,
        "owner_a": owner_a,
        "store": store,
        "tmp_path": tmp_path,
        "org_a": org_a.organization_id,
        "org_b": org_b.organization_id,
        "a1": a1.project_id,
        "a2": a2.project_id,
        "b1": b1.project_id,
        "writer": writer,
        "reader": reader,
        "writer_b": writer_b,
    }


def _create(world, artifact_id, artifact_type, *, project=None, principal=None, tags=None, subject_key=None, content=None):
    return world["engine"].create_artifact(
        principal=principal or world["writer"],
        project=project or world["a1"],
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        title=artifact_id,
        content=content or f"content of {artifact_id}",
        tags=tags,
        subject_key=subject_key,
    )


def _current(world, *, project=None, principal=None, **filters):
    return world["engine"].get_current(
        principal=principal or world["writer"], project=project or world["a1"], **filters
    )


def _history(world, lineage_id, *, project=None):
    return world["engine"].get_history(
        principal=world["writer"], project=project or world["a1"], lineage_id=lineage_id
    )


def _records(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME]


# -- tags never establish identity -------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_same_type_and_identical_tags_without_subject_key_do_not_conflict(world, artifact_type):
    """The issue #5 reproduction: four independent requirement documents
    with identical classification tags all resolve as current."""
    tags = ["schedovia", "requirements", "pilot"]
    ids = ["req-auth", "req-services", "req-appointments", "req-notifications"]
    for artifact_id in ids:
        _create(world, artifact_id, artifact_type, tags=tags)

    package = _current(world)

    assert package.conflicts == []
    assert package.integrity_errors == []
    assert {a.artifact_id for a in package.artifacts} == set(ids)


@pytest.mark.parametrize("artifact_type", TYPES)
def test_partially_overlapping_tags_do_not_conflict(world, artifact_type):
    _create(world, "one", artifact_type, tags=["schedovia", "security", "auth"])
    _create(world, "two", artifact_type, tags=["schedovia", "security", "billing"])

    package = _current(world)

    assert package.conflicts == []
    assert {a.artifact_id for a in package.artifacts} == {"one", "two"}


@pytest.mark.parametrize("artifact_type", TYPES)
def test_search_by_shared_tag_returns_multiple_independent_artifacts(world, artifact_type):
    _create(world, "req-a", artifact_type, tags=["schedovia", "requirements"], subject_key="auth")
    _create(world, "req-b", artifact_type, tags=["schedovia", "requirements"], subject_key="billing")
    _create(world, "req-c", artifact_type, tags=["schedovia", "requirements"])

    found = world["engine"].search(principal=world["writer"], project=world["a1"], tags=["requirements"])

    assert {a.artifact_id for a in found} == {"req-a", "req-b", "req-c"}


# -- explicit subject identity -------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_same_type_and_subject_key_in_different_lineages_conflict(world, artifact_type, caplog):
    _create(world, "use-redis", artifact_type, tags=["caching"], subject_key="session-cache")
    _create(world, "use-memcached", artifact_type, tags=["other"], subject_key="session-cache")
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    caplog.clear()

    package = _current(world)

    assert package.artifacts == []
    assert len(package.conflicts) == 1
    conflict = package.conflicts[0]
    assert conflict.artifact_type == artifact_type
    assert conflict.subject_key == "session-cache"
    assert {c.lineage_id for c in conflict.candidates} == {"use-redis", "use-memcached"}
    assert conflict.state == "open" and conflict.conflict_id.startswith("cfl_")
    assert any("subject_key='session-cache'" in step for step in package.resolution_trace)
    conflict_events = [r for r in _records(caplog) if r["event"] == "CONFLICT"]
    assert conflict_events and conflict_events[0]["subject_key"] == "session-cache"


def test_same_subject_key_but_different_artifact_types_do_not_conflict(world):
    _create(world, "decision", TYPES[0], subject_key="session-cache")
    _create(world, "note", TYPES[1], subject_key="session-cache")

    package = _current(world)

    assert package.conflicts == []
    assert {a.artifact_id for a in package.artifacts} == {"decision", "note"}


@pytest.mark.parametrize("artifact_type", TYPES)
def test_same_subject_key_in_different_projects_does_not_conflict(world, artifact_type):
    _create(world, "x", artifact_type, project=world["a1"], subject_key="session-cache")
    _create(world, "y", artifact_type, project=world["a2"], subject_key="session-cache")

    for project, expected in ((world["a1"], "x"), (world["a2"], "y")):
        package = _current(world, project=project)
        assert package.conflicts == []
        assert [a.artifact_id for a in package.artifacts] == [expected]


@pytest.mark.parametrize("artifact_type", TYPES)
def test_same_subject_key_in_different_organizations_does_not_conflict(world, artifact_type):
    _create(world, "x", artifact_type, subject_key="session-cache")
    _create(world, "y", artifact_type, project=world["b1"], principal=world["writer_b"], subject_key="session-cache")

    a = _current(world)
    b = _current(world, project=world["b1"], principal=world["writer_b"])

    assert a.conflicts == [] and [x.artifact_id for x in a.artifacts] == ["x"]
    assert b.conflicts == [] and [x.artifact_id for x in b.artifacts] == ["y"]
    # And neither side can even see the other's project.
    with pytest.raises(AccessDeniedError):
        _current(world, project=world["b1"])


@pytest.mark.parametrize("artifact_type", TYPES)
def test_subject_key_is_trimmed_and_blank_is_rejected(world, artifact_type):
    created = _create(world, "k", artifact_type, subject_key="  session-cache  ")
    assert created.subject_key == "session-cache"

    with pytest.raises(ValueError):
        _create(world, "blank", artifact_type, subject_key="   ")
    assert world["store"].get(world["org_a"], world["a1"], "blank") is None


# -- supersession within a lineage --------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_explicit_supersede_changes_only_its_lineage_and_never_self_conflicts(world, artifact_type):
    _create(world, "cache", artifact_type, tags=["t"], subject_key="session-cache")
    other = _create(world, "other", artifact_type, tags=["t"], subject_key="database")

    successor = world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id="cache",
        content="updated", reason="refined",
    )

    package = _current(world)
    assert package.conflicts == []
    assert {a.artifact_id for a in package.artifacts} == {successor.artifact_id, "other"}
    # Only the intended lineage changed.
    assert world["store"].get(world["org_a"], world["a1"], "other")[0] == other
    history = _history(world, "cache")
    assert [a.version for a in history] == ["1.0", "2.0"]
    assert history[0].status == "superseded" and history[0].superseded_by == successor.artifact_id
    assert history[1].status == "active" and history[1].supersedes == "cache"


@pytest.mark.parametrize("artifact_type", TYPES)
def test_supersede_preserves_subject_key_when_omitted_or_restated(world, artifact_type):
    _create(world, "cache", artifact_type, subject_key="session-cache")
    v2 = world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id="cache", content="v2", reason="r",
    )
    v3 = world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id=v2.artifact_id, content="v3",
        reason="r", subject_key=" session-cache ",
    )

    assert v2.subject_key == "session-cache" and v2.released_subject_key is None
    assert v3.subject_key == "session-cache"


@pytest.mark.parametrize("artifact_type", TYPES)
def test_supersede_cannot_change_subject_key_and_writes_nothing(world, artifact_type, caplog):
    original = _create(world, "cache", artifact_type, subject_key="session-cache")
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    caplog.clear()

    with pytest.raises(SubjectKeyChangeError):
        world["engine"].supersede_artifact(
            principal=world["writer"], project=world["a1"], old_id="cache", content="v2",
            reason="r", subject_key="database",
        )

    assert _history(world, "cache") == [original]
    rejected = [r for r in _records(caplog) if r["event"] == "SUPERSEDE_REJECTED"]
    assert rejected and "not allowed within a lineage" in rejected[0]["reason"]


@pytest.mark.parametrize("artifact_type", TYPES)
def test_lineage_without_subject_key_cannot_acquire_one(world, artifact_type):
    original = _create(world, "plain", artifact_type)

    with pytest.raises(SubjectKeyChangeError):
        world["engine"].supersede_artifact(
            principal=world["writer"], project=world["a1"], old_id="plain", content="v2",
            reason="r", subject_key="session-cache",
        )
    assert _history(world, "plain") == [original]


@pytest.mark.parametrize("artifact_type", TYPES)
def test_superseding_an_already_superseded_version_still_fails_clearly(world, artifact_type):
    _create(world, "cache", artifact_type, subject_key="session-cache")
    world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id="cache", content="v2", reason="r",
    )
    with pytest.raises(ArtifactNotActiveError):
        world["engine"].supersede_artifact(
            principal=world["writer"], project=world["a1"], old_id="cache", content="v3",
            reason="r", release_subject_key=True,
        )


# -- explicit subject release ---------------------------------------------------


@pytest.mark.parametrize("artifact_type", TYPES)
def test_release_during_an_open_conflict_is_rejected_and_resolution_settles_it(world, artifact_type, caplog):
    """v2 (MAK-0004 §A3.7): releasing a candidate's key no longer resolves a
    conflict behind the resolver's back; it is rejected and the conflict is
    resolved explicitly, which leaves the winner unchanged."""
    from mak4i.errors import SubjectInConflictError

    winner = _create(world, "use-redis", artifact_type, subject_key="session-cache", content="Use Redis")
    _create(world, "use-memcached", artifact_type, subject_key="session-cache", content="Use Memcached")
    conflict = _current(world).conflicts[0]
    with pytest.raises(SubjectInConflictError):
        world["engine"].supersede_artifact(
            principal=world["writer"], project=world["a1"], old_id="use-memcached",
            content="x", reason="y", release_subject_key=True,
        )
    assert len(_history(world, "use-memcached")) == 1  # nothing written

    world["control_plane"].grant(
        actor=world["owner_a"], principal_id=world["writer"].principal_id,
        project_id=world["a1"], permissions=["read", "write", "resolve"],
    )
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    caplog.clear()
    record = world["engine"].resolve_conflict(
        principal=world["writer"], project=world["a1"], conflict_id=conflict.conflict_id,
        candidate_artifact_ids=["use-memcached", "use-redis"], action="select_winner",
        winner_artifact_id="use-redis", reason="Redis selected for session-cache.",
    )
    assert record.state == "completed"
    package = _current(world)
    assert package.conflicts == []
    assert [a.artifact_id for a in package.artifacts] == ["use-redis"]
    assert world["store"].get(world["org_a"], world["a1"], "use-redis")[0] == winner
    loser = _history(world, "use-memcached")
    assert len(loser) == 1 and loser[0].status == "withdrawn"
    assert loser[0].subject_key == "session-cache"  # its claim stays in history
    events = {r["event"] for r in _records(caplog)}
    assert {"RESOLUTION_STARTED", "RESOLUTION_COMPLETED"} <= events


@pytest.mark.parametrize("artifact_type", TYPES)
def test_release_outside_a_conflict_still_works(world, artifact_type):
    _create(world, "solo", artifact_type, subject_key="session-cache")
    released = world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id="solo",
        content="no longer the session cache decision", reason="scope changed",
        release_subject_key=True,
    )
    assert released.subject_key is None and released.released_subject_key == "session-cache"


@pytest.mark.parametrize("artifact_type", TYPES)
def test_omitting_the_flag_never_releases(world, artifact_type):
    _create(world, "a", artifact_type, subject_key="session-cache")
    _create(world, "b", artifact_type, subject_key="session-cache")

    world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id="b", content="changed", reason="r",
    )

    assert len(_current(world).conflicts) == 1


@pytest.mark.parametrize("artifact_type", TYPES)
def test_ambiguous_or_no_op_releases_are_rejected(world, artifact_type):
    _create(world, "plain", artifact_type)
    _create(world, "keyed", artifact_type, subject_key="session-cache")

    with pytest.raises(SubjectKeyChangeError, match="has no subject_key"):
        world["engine"].supersede_artifact(
            principal=world["writer"], project=world["a1"], old_id="plain", content="v2",
            reason="r", release_subject_key=True,
        )
    with pytest.raises(SubjectKeyChangeError, match="cannot be combined"):
        world["engine"].supersede_artifact(
            principal=world["writer"], project=world["a1"], old_id="keyed", content="v2",
            reason="r", subject_key="session-cache", release_subject_key=True,
        )


@pytest.mark.parametrize("artifact_type", TYPES)
def test_released_lineage_cannot_reclaim_its_subject(world, artifact_type):
    _create(world, "b", artifact_type, subject_key="session-cache")
    released = world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id="b", content="v2",
        reason="r", release_subject_key=True,
    )

    with pytest.raises(SubjectKeyChangeError):
        world["engine"].supersede_artifact(
            principal=world["writer"], project=world["a1"], old_id=released.artifact_id,
            content="v3", reason="r", subject_key="session-cache",
        )
    # A later ordinary supersede keeps it released (no key, no new release marker).
    v3 = world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id=released.artifact_id,
        content="v3", reason="r",
    )
    assert v3.subject_key is None and v3.released_subject_key is None


@pytest.mark.parametrize("artifact_type", TYPES)
def test_unauthorized_principals_cannot_release(world, artifact_type):
    _create(world, "a", artifact_type, subject_key="session-cache")
    _create(world, "b", artifact_type, subject_key="session-cache")

    for principal in (world["reader"], world["writer_b"]):
        with pytest.raises(AccessDeniedError):
            world["engine"].supersede_artifact(
                principal=principal, project=world["a1"], old_id="b", content="v2",
                reason="r", release_subject_key=True,
            )

    assert len(_current(world).conflicts) == 1
    assert len(_history(world, "b")) == 1


# -- RC2 compatibility -----------------------------------------------------------


def _write_rc2_record(world, artifact_id, artifact_type, tags):
    """An artifact exactly as v0.1.0-rc.2 persisted it: no subject_key or
    released_subject_key keys at all."""
    artifact = {
        "artifact_id": artifact_id, "artifact_type": artifact_type,
        "organization_id": world["org_a"], "project": world["a1"],
        "title": artifact_id, "content": "stored by RC2", "rationale": None,
        "status": "active", "version": "1.0", "created_by": world["writer"].principal_id,
        "created_at": "2026-09-25T12:00:00Z", "updated_at": "2026-09-25T12:00:00Z",
        "lineage_id": artifact_id, "supersedes": None, "superseded_by": None, "tags": tags,
    }
    path = world["tmp_path"] / "artifacts" / world["org_a"] / world["a1"] / f"{artifact_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generation": 1, "artifact": artifact}))


@pytest.mark.parametrize("artifact_type", TYPES)
def test_rc2_artifacts_without_subject_key_load_resolve_and_supersede(world, artifact_type):
    _write_rc2_record(world, "rc2-a", artifact_type, ["schedovia", "requirements"])
    _write_rc2_record(world, "rc2-b", artifact_type, ["schedovia", "requirements"])

    package = _current(world)
    assert package.conflicts == []  # shared tags no longer conflict
    assert all(a.subject_key is None for a in package.artifacts)

    successor = world["engine"].supersede_artifact(
        principal=world["writer"], project=world["a1"], old_id="rc2-a", content="updated", reason="r",
    )
    assert successor.subject_key is None
    assert [a.version for a in _history(world, "rc2-a")] == ["1.0", "2.0"]


def test_unset_new_fields_are_omitted_from_storage_so_rc2_can_still_read_them(world):
    _create(world, "plain", TYPES[0])
    _create(world, "keyed", TYPES[1], subject_key="session-cache")
    root = world["tmp_path"] / "artifacts" / world["org_a"] / world["a1"]

    plain = json.loads((root / "plain.json").read_text())["artifact"]
    keyed = json.loads((root / "keyed.json").read_text())["artifact"]

    assert "subject_key" not in plain and "released_subject_key" not in plain
    assert keyed["subject_key"] == "session-cache"
    # Round-trips through the model unchanged.
    assert Artifact.model_validate(plain).subject_key is None
