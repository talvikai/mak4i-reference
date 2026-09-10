from datetime import datetime, timezone

import pytest

from mak4i.models import Artifact
from mak4i.store.base import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ArtifactStore,
    ConcurrentModificationError,
)
from mak4i.store.local_json import LocalJSONStore

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)

ORG = "org_wd"
OTHER_ORG = "org_talvik"


def _artifact(**overrides) -> Artifact:
    base = dict(
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        organization_id=ORG,
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
        organization_id=ORG,
        project="schedovia",
        title="Primary database technology",
        content="Use MySQL for the primary application database.",
        rationale="Team familiarity and managed-hosting availability.",
        status="active",
        version="2.0",
        created_by="claude.ai",
        created_at=NOW,
        updated_at=NOW,
        lineage_id="decision-db-001",
        supersedes="decision-db-001",
        superseded_by=None,
        tags=["database", "mysql", "architecture"],
    )
    base.update(overrides)
    return Artifact(**base)


@pytest.fixture
def store(tmp_path) -> LocalJSONStore:
    return LocalJSONStore(tmp_path / "artifacts")


def test_local_json_store_satisfies_artifact_store_protocol(store):
    assert isinstance(store, ArtifactStore)


def test_get_missing_returns_none(store):
    assert store.get(ORG, "schedovia", "does-not-exist") is None


def test_put_new_then_get_round_trips(store):
    artifact = _artifact()
    store.put_new(artifact)

    result = store.get(artifact.organization_id, artifact.project, artifact.artifact_id)
    assert result is not None
    fetched, token = result
    assert fetched == artifact
    assert token == "1"


def test_put_new_rejects_duplicate_id(store):
    store.put_new(_artifact())
    with pytest.raises(ArtifactAlreadyExistsError):
        store.put_new(_artifact())


def test_put_if_match_updates_and_bumps_token(store):
    original = _artifact()
    store.put_new(original)
    _, token = store.get(original.organization_id, original.project, original.artifact_id)

    updated = original.model_copy(update={"status": "superseded", "superseded_by": "decision-cache-002"})
    store.put_if_match(updated, expected_version_token=token)

    fetched, new_token = store.get(original.organization_id, original.project, original.artifact_id)
    assert fetched.status == "superseded"
    assert fetched.superseded_by == "decision-cache-002"
    assert new_token == "2"
    assert new_token != token


def test_put_if_match_rejects_stale_token(store):
    original = _artifact()
    store.put_new(original)
    _, stale_token = store.get(original.organization_id, original.project, original.artifact_id)

    # Someone else updates it first.
    store.put_if_match(
        original.model_copy(update={"content": "Use Redis, updated."}),
        expected_version_token=stale_token,
    )

    # Our write, still holding the now-stale token, must be rejected —
    # this is the GCS-native race-closing check from MVP_ARCHITECTURE.md §5.
    with pytest.raises(ConcurrentModificationError):
        store.put_if_match(
            original.model_copy(update={"content": "Use Redis, conflicting write."}),
            expected_version_token=stale_token,
        )


def test_put_if_match_on_nonexistent_artifact_raises(store):
    with pytest.raises(ArtifactNotFoundError):
        store.put_if_match(_artifact(), expected_version_token="1")


def test_query_filters_by_org_project_type_tags_status(store):
    store.put_new(_artifact())
    store.put_new(_db_artifact())
    store.put_new(_artifact(artifact_id="decision-cache-002", status="superseded", supersedes=None))

    active_caching = store.query(ORG, "schedovia", tags=["caching"], status="active")
    assert [a.artifact_id for a in active_caching] == ["decision-cache-001"]

    all_active = store.query(ORG, "schedovia", status="active")
    assert {a.artifact_id for a in all_active} == {"decision-cache-001", "decision-db-002"}

    any_status = store.query(ORG, "schedovia", status=None)
    assert len(any_status) == 3

    other_project = store.query(ORG, "other-project")
    assert other_project == []

    other_org = store.query(OTHER_ORG, "schedovia")
    assert other_org == []


def test_query_many_concatenates_across_scopes(store):
    store.put_new(_artifact())
    store.put_new(_db_artifact(organization_id=OTHER_ORG, project="talvik-mak4i"))

    results = store.query_many(
        [(ORG, "schedovia"), (OTHER_ORG, "talvik-mak4i")], status=None
    )
    assert {a.artifact_id for a in results} == {"decision-cache-001", "decision-db-002"}

    assert store.query_many([]) == []


def test_all_returns_every_status_optionally_filtered_by_org_and_project(store):
    store.put_new(_artifact())
    store.put_new(_artifact(artifact_id="decision-cache-002", status="superseded"))

    assert len(store.all()) == 2
    assert len(store.all(ORG, "schedovia")) == 2
    assert store.all(ORG, "other-project") == []
    assert store.all(OTHER_ORG) == []


def test_generic_store_path_for_caching_and_database_artifacts(store):
    """A caching artifact and a database artifact must exercise the exact
    same store code (no technology-specific branching) — see CLAUDE.md
    and MVP_ARCHITECTURE.md §20."""
    store.put_new(_artifact())
    store.put_new(_db_artifact())

    for artifact_id in ("decision-cache-001", "decision-db-002"):
        fetched, token = store.get(ORG, "schedovia", artifact_id)
        assert fetched.artifact_id == artifact_id
        assert token == "1"


def test_same_artifact_id_coexists_across_different_orgs_projects(store):
    """The Developer Preview tenant-isolation boundary: two organizations
    (or two projects) can independently use the same artifact_id without
    collision, and a scoped get/query/all for one never returns the
    other's artifact."""
    wd = _artifact(organization_id=ORG, project="schedovia")
    talvik = _artifact(organization_id=OTHER_ORG, project="schedovia")
    store.put_new(wd)
    store.put_new(talvik)

    fetched_wd, _ = store.get(ORG, "schedovia", "decision-cache-001")
    fetched_talvik, _ = store.get(OTHER_ORG, "schedovia", "decision-cache-001")
    assert fetched_wd.organization_id == ORG
    assert fetched_talvik.organization_id == OTHER_ORG

    assert [a.artifact_id for a in store.query(ORG, "schedovia")] == ["decision-cache-001"]
    assert [a.artifact_id for a in store.query(OTHER_ORG, "schedovia")] == ["decision-cache-001"]
    assert len(store.all()) == 2
