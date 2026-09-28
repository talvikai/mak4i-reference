import json
import logging

import pytest

from mak4i.api import MAK4IEngine
from mak4i.audit import LOGGER_NAME, AuditLogger
from mak4i.identity import Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.store.local_json import LocalJSONStore


def _onboarded_world():
    """One org, one project, one principal with read+write on it — the
    minimal authorized world every engine test starts from. The
    Authorizer here carries no AuditLogger, so ACCESS_GRANTED/DENIED
    events (covered by tests/test_authz.py) never appear in these
    engine-lifecycle assertions."""
    control_plane = ControlPlane(InMemoryControlPlaneStore())
    org, owner = control_plane.onboard_organization(
        organization_name="WD Technology Solutions", owner_display_name="Owner"
    )
    project = control_plane.create_project(
        actor=owner, organization_id=org.organization_id, name="schedovia"
    )
    principal = control_plane.create_principal(
        actor=owner,
        organization_id=org.organization_id,
        type="human",
        display_name="Dev",
    )
    control_plane.grant(
        actor=owner,
        principal_id=principal.principal_id,
        project_id=project.project_id,
        permissions=["read", "write"],
    )
    return control_plane, org.organization_id, project.project_id, principal


@pytest.fixture
def store(tmp_path) -> LocalJSONStore:
    return LocalJSONStore(tmp_path / "artifacts")


@pytest.fixture
def world(store):
    control_plane, organization_id, project_id, principal = _onboarded_world()
    return {
        "control_plane": control_plane,
        "organization_id": organization_id,
        "project": project_id,
        "principal": principal,
    }


@pytest.fixture
def audited_engine(store, world) -> MAK4IEngine:
    return MAK4IEngine(
        store,
        audit=AuditLogger(),
        authorizer=Authorizer(world["control_plane"]),
        control_plane=world["control_plane"],
    )


def _records(caplog) -> list[dict]:
    return [json.loads(r.message) for r in caplog.records if r.name == LOGGER_NAME]


def test_create_artifact_logs_create_event(audited_engine, world, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    audited_engine.create_artifact(
        principal=world["principal"],
        project=world["project"],
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
    )

    events = [r["event"] for r in _records(caplog)]
    assert events == ["CREATE"]


def test_supersede_artifact_logs_supersede_event(audited_engine, world, caplog):
    audited_engine.create_artifact(
        principal=world["principal"],
        project=world["project"],
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
    )
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    caplog.clear()

    audited_engine.supersede_artifact(
        principal=world["principal"],
        project=world["project"],
        old_id="decision-cache-001",
        content="Use Valkey for application caching.",
        reason="License concerns with Redis.",
    )

    records = _records(caplog)
    assert [r["event"] for r in records] == ["SUPERSEDE"]
    assert records[0]["old_id"] == "decision-cache-001"
    assert records[0]["new_id"] == "decision-cache-002"


def test_supersede_rejected_logs_supersede_rejected_event(audited_engine, world, caplog):
    audited_engine.create_artifact(
        principal=world["principal"],
        project=world["project"],
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
    )
    audited_engine.supersede_artifact(
        principal=world["principal"],
        project=world["project"],
        old_id="decision-cache-001",
        content="Use Valkey for application caching.",
        reason="License concerns with Redis.",
    )
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    caplog.clear()

    from mak4i.api import ArtifactNotActiveError

    with pytest.raises(ArtifactNotActiveError):
        audited_engine.supersede_artifact(
            principal=world["principal"],
            project=world["project"],
            old_id="decision-cache-001",
            content="Use Memcached.",
            reason="Should be rejected.",
        )

    records = _records(caplog)
    assert [r["event"] for r in records] == ["SUPERSEDE_REJECTED"]


def test_get_current_logs_get_current_discover_resolve_inject_with_shared_correlation_id(
    audited_engine, world, caplog
):
    audited_engine.create_artifact(
        principal=world["principal"],
        project=world["project"],
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
        tags=["caching", "redis", "architecture"],
    )
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    caplog.clear()

    package = audited_engine.get_current(principal=world["principal"], project=world["project"])

    assert [a.artifact_id for a in package.artifacts] == ["decision-cache-001"]

    records = _records(caplog)
    assert [r["event"] for r in records] == ["GET_CURRENT", "DISCOVER", "RESOLVE", "INJECT"]
    correlation_ids = {r["correlation_id"] for r in records}
    assert correlation_ids == {package.resolution_trace_id}
    assert records[2]["resolved_ids"] == ["decision-cache-001"]
    assert records[3]["artifact_ids"] == ["decision-cache-001"]


def test_get_current_logs_conflict_event_when_conflicting(audited_engine, world, caplog):
    audited_engine.create_artifact(
        principal=world["principal"],
        project=world["project"],
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
        tags=["caching", "redis", "architecture"],
        subject_key="application-cache",
    )
    audited_engine.create_artifact(
        principal=world["principal"],
        project=world["project"],
        artifact_id="decision-cache-004",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Memcached for application caching.",
        tags=["caching", "memcached", "architecture"],
        subject_key="application-cache",
    )
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    caplog.clear()

    package = audited_engine.get_current(principal=world["principal"], project=world["project"])

    assert package.artifacts == []
    assert len(package.conflicts) == 1

    events = [r["event"] for r in _records(caplog)]
    assert events == ["GET_CURRENT", "DISCOVER", "RESOLVE", "CONFLICT", "INJECT"]


def test_get_current_logs_integrity_error_event(audited_engine, world, store, caplog):
    from datetime import datetime, timezone

    from mak4i.models import Artifact

    now = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
    store.put_new(
        Artifact(
            artifact_id="decision-cache-001",
            artifact_type="architecture_decision",
            organization_id=world["organization_id"],
            project=world["project"],
            title="Application caching technology",
            content="Use Redis for application caching.",
            rationale=None,
            status="superseded",
            version="1.0",
            created_by=world["principal"].principal_id,
            created_at=now,
            updated_at=now,
            lineage_id="decision-cache-001",
            supersedes=None,
            superseded_by="decision-cache-002",
            tags=["caching"],
        )
    )
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    caplog.clear()

    package = audited_engine.get_current(principal=world["principal"], project=world["project"])

    assert package.artifacts == []
    assert len(package.integrity_errors) == 1

    events = [r["event"] for r in _records(caplog)]
    assert events == ["GET_CURRENT", "DISCOVER", "RESOLVE", "INTEGRITY_ERROR", "INJECT"]


def test_engine_without_audit_logger_emits_nothing(store, world, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    engine = MAK4IEngine(
        store,
        authorizer=Authorizer(world["control_plane"]),
        control_plane=world["control_plane"],
    )  # audit=None, the default

    engine.create_artifact(
        principal=world["principal"],
        project=world["project"],
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
    )
    engine.get_current(principal=world["principal"], project=world["project"])

    assert _records(caplog) == []
