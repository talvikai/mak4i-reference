from datetime import datetime, timezone

import pytest

from mak4i.models import Artifact
from mak4i.resolution import Resolver

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def _artifact(**overrides) -> Artifact:
    base = dict(
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        organization_id="org_test",
        project="schedovia",
        title="Application caching technology",
        content="Use Redis for application caching.",
        rationale="Selected for fast shared caching and established client support.",
        status="active",
        version="1.0",
        created_by="claude.ai",
        created_at=NOW,
        updated_at=NOW,
        lineage_id="decision-cache-001",
        supersedes=None,
        superseded_by=None,
        tags=["caching", "redis", "architecture"],
    )
    base.update(overrides)
    return Artifact(**base)


def _db_artifact(**overrides) -> Artifact:
    base = dict(
        artifact_id="decision-db-002",
        artifact_type="architecture_decision",
        organization_id="org_test",
        project="schedovia",
        title="Primary database technology",
        content="Use MySQL for the primary application database.",
        rationale="Team familiarity and managed-hosting availability.",
        status="active",
        version="2.0",
        created_by="claude.ai",
        created_at=NOW,
        updated_at=NOW,
        lineage_id="decision-db-002",
        supersedes=None,
        superseded_by=None,
        tags=["database", "mysql", "architecture"],
    )
    base.update(overrides)
    return Artifact(**base)


@pytest.fixture
def resolver() -> Resolver:
    return Resolver()


def test_single_active_artifact_resolves(resolver):
    result = resolver.resolve([_artifact()])
    assert [a.artifact_id for a in result.resolved] == ["decision-cache-001"]
    assert result.conflicts == []
    assert result.integrity_errors == []
    assert result.trace  # explainable


def test_proper_supersession_chain_resolves_to_active_only(resolver):
    old = _artifact(
        artifact_id="decision-cache-000",
        status="superseded",
        superseded_by="decision-cache-001",
        version="0.9",
    )
    new = _artifact(supersedes="decision-cache-000")
    result = resolver.resolve([old, new])

    assert [a.artifact_id for a in result.resolved] == ["decision-cache-001"]
    assert result.integrity_errors == []


def test_zero_active_lineage_fails_closed(resolver):
    only_superseded = _artifact(status="superseded", superseded_by="decision-cache-002")
    result = resolver.resolve([only_superseded])

    assert result.resolved == []
    assert len(result.integrity_errors) == 1
    assert result.integrity_errors[0].kind == "zero_active"
    assert result.integrity_errors[0].lineage_id == "decision-cache-001"


def test_multiple_active_lineage_fails_closed(resolver):
    active_a = _artifact(artifact_id="decision-cache-001a")
    active_b = _artifact(artifact_id="decision-cache-001b")
    result = resolver.resolve([active_a, active_b])

    assert result.resolved == []
    assert len(result.integrity_errors) == 1
    assert result.integrity_errors[0].kind == "multiple_active"


def test_dangling_supersedes_pointer_fails_closed(resolver):
    # `active.supersedes` points at an id that doesn't exist in the lineage.
    dangling = _artifact(supersedes="decision-cache-000-never-written")
    result = resolver.resolve([dangling])

    assert result.resolved == []
    assert result.integrity_errors[0].kind == "dangling_pointer"


def test_dangling_superseded_by_pointer_fails_closed(resolver):
    old = _artifact(
        artifact_id="decision-cache-000",
        status="superseded",
        superseded_by="decision-cache-999-never-written",
        version="0.9",
    )
    new = _artifact(supersedes="decision-cache-000")
    result = resolver.resolve([old, new])

    assert result.resolved == []
    assert result.integrity_errors[0].kind == "dangling_pointer"


def test_applicability_filters_by_type_and_tags(resolver):
    result = resolver.resolve(
        [_artifact(), _db_artifact()], artifact_type="architecture_decision", tags=["redis"]
    )
    assert [a.artifact_id for a in result.resolved] == ["decision-cache-001"]


def test_cross_lineage_conflict_detected_and_excluded_from_resolved(resolver):
    redis = _artifact()
    memcached = _artifact(
        artifact_id="decision-cache-004",
        content="Use Memcached for application caching.",
        lineage_id="decision-cache-004",
        supersedes=None,
        superseded_by=None,
    )
    result = resolver.resolve([redis, memcached])

    assert result.resolved == []
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert set(conflict.lineage_ids) == {"decision-cache-001", "decision-cache-004"}
    assert conflict.artifact_type == "architecture_decision"


def test_unrelated_active_artifacts_do_not_conflict(resolver):
    result = resolver.resolve([_artifact(), _db_artifact()])
    assert result.conflicts == []
    assert {a.artifact_id for a in result.resolved} == {"decision-cache-001", "decision-db-002"}


@pytest.mark.parametrize("build_artifact", [_artifact, _db_artifact])
def test_generic_resolution_path_for_any_scenario(resolver, build_artifact):
    """A caching lineage and a database lineage must exercise the exact
    same resolver code (no technology-specific branching)."""
    artifact = build_artifact()
    result = resolver.resolve([artifact])
    assert [a.artifact_id for a in result.resolved] == [artifact.artifact_id]
    assert result.integrity_errors == []
    assert result.conflicts == []
