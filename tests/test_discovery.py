from datetime import datetime, timezone

import pytest

from mak4i.discovery import Discovery
from mak4i.models import Artifact
from mak4i.store.local_json import LocalJSONStore

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
ORG = "org_test"


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


@pytest.fixture
def discovery(tmp_path) -> Discovery:
    store = LocalJSONStore(tmp_path / "schedovia")
    store.put_new(_artifact())
    store.put_new(
        _artifact(
            artifact_id="decision-db-002",
            artifact_type="architecture_decision",
            title="Primary database technology",
            content="Use MySQL for the primary application database.",
            lineage_id="decision-db-001",
            tags=["database", "mysql", "architecture"],
        )
    )
    store.put_new(
        _artifact(
            artifact_id="decision-cache-000",
            status="superseded",
            superseded_by="decision-cache-001",
            version="0.9",
            tags=["caching", "memcached", "architecture"],
        )
    )
    return Discovery(store)


def test_default_status_filters_to_active(discovery):
    results = discovery.find_candidates(ORG, "schedovia")
    assert {a.artifact_id for a in results} == {"decision-cache-001", "decision-db-002"}


def test_filters_by_type_and_tags(discovery):
    results = discovery.find_candidates(
        ORG, "schedovia", artifact_type="architecture_decision", tags=["redis"]
    )
    assert [a.artifact_id for a in results] == ["decision-cache-001"]


def test_status_none_returns_all_statuses(discovery):
    results = discovery.find_candidates(ORG, "schedovia", status=None)
    assert len(results) == 3


def test_no_match_returns_empty_list(discovery):
    assert discovery.find_candidates(ORG, "schedovia", tags=["nonexistent"]) == []


def test_generic_lookup_for_caching_and_database_types(discovery):
    caching = discovery.find_candidates(ORG, "schedovia", tags=["caching"])
    database = discovery.find_candidates(ORG, "schedovia", tags=["database"])
    assert [a.artifact_id for a in caching] == ["decision-cache-001"]
    assert [a.artifact_id for a in database] == ["decision-db-002"]
