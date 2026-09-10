from datetime import datetime, timezone

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from mak4i.models import Artifact
from mak4i.store.base import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ArtifactStore,
    ConcurrentModificationError,
)
from mak4i.store.gcs import GCSArtifactStore

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)

ORG = "org_wd"
OTHER_ORG = "org_talvik"


class _FakeBlob:
    """Mimics the subset of google.cloud.storage.Blob GCSArtifactStore
    actually calls, backed by a _FakeBucket's in-memory object dict.
    Generation semantics match real GCS: a nonexistent object has an
    implicit generation of 0, and every successful write is assigned a
    brand-new generation number (never reused)."""

    def __init__(self, bucket: "_FakeBucket", name: str):
        self._bucket = bucket
        self.name = name

    @property
    def generation(self) -> int:
        entry = self._bucket.objects.get(self.name)
        if entry is None:
            raise NotFound(self.name)
        return entry[1]

    def download_as_bytes(self) -> bytes:
        entry = self._bucket.objects.get(self.name)
        if entry is None:
            raise NotFound(self.name)
        return entry[0]

    def upload_from_string(self, data, content_type=None, if_generation_match=None) -> None:
        entry = self._bucket.objects.get(self.name)
        current_generation = entry[1] if entry is not None else 0
        if if_generation_match is not None and if_generation_match != current_generation:
            raise PreconditionFailed(f"generation precondition failed for {self.name!r}")
        self._bucket.objects[self.name] = (data, self._bucket.next_generation())


class _FakeBucket:
    def __init__(self, name: str):
        self.name = name
        self.objects: dict[str, tuple[bytes, int]] = {}
        self._counter = 0

    def next_generation(self) -> int:
        self._counter += 1
        return self._counter

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)

    def get_blob(self, name: str) -> _FakeBlob | None:
        if name not in self.objects:
            return None
        return _FakeBlob(self, name)


class _FakeGCSClient:
    def __init__(self):
        self._buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self._buckets.setdefault(name, _FakeBucket(name))

    def list_blobs(self, bucket, prefix: str | None = None):
        name = bucket.name if hasattr(bucket, "name") else bucket
        real_bucket = self._buckets.get(name)
        if real_bucket is None:
            return
        for key in sorted(real_bucket.objects):
            if prefix is None or key.startswith(prefix):
                yield _FakeBlob(real_bucket, key)


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
def fake_client() -> _FakeGCSClient:
    return _FakeGCSClient()


@pytest.fixture
def store(fake_client) -> GCSArtifactStore:
    return GCSArtifactStore("example-mak4i-artifacts", client=fake_client)


def test_gcs_store_satisfies_artifact_store_protocol(store):
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


def test_put_new_stores_under_the_documented_path(store, fake_client):
    store.put_new(_artifact())
    bucket = fake_client.bucket("example-mak4i-artifacts")
    assert f"{ORG}/schedovia/decision-cache-001.json" in bucket.objects


def test_put_new_rejects_duplicate_id(store):
    store.put_new(_artifact())
    with pytest.raises(ArtifactAlreadyExistsError):
        store.put_new(_artifact())


def test_put_if_match_updates_and_bumps_token(store):
    original = _artifact()
    store.put_new(original)
    _, token = store.get(original.organization_id, original.project, original.artifact_id)

    updated = original.model_copy(
        update={"status": "superseded", "superseded_by": "decision-cache-002"}
    )
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

    # Someone else updates it first — this IS the GCS-native
    # ifGenerationMatch race-closing check from MVP_ARCHITECTURE.md §5.
    store.put_if_match(
        original.model_copy(update={"content": "Use Redis, updated."}),
        expected_version_token=stale_token,
    )

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
    same store code (no technology-specific branching) — see CLAUDE.md and
    MVP_ARCHITECTURE.md §20 — and the same rule as LocalJSONStore, since
    both share store/_filtering.py's filter_artifacts."""
    store.put_new(_artifact())
    store.put_new(_db_artifact())

    for artifact_id in ("decision-cache-001", "decision-db-002"):
        fetched, token = store.get(ORG, "schedovia", artifact_id)
        assert fetched.artifact_id == artifact_id
        # VersionToken is opaque (base.py) — GCS generations are not a
        # per-object counter like LocalJSONStore's, so only assert it's a
        # non-empty, round-trippable token, not a specific value.
        assert token
        assert store.get(ORG, "schedovia", artifact_id) == (fetched, token)


def test_same_artifact_id_coexists_across_different_orgs_projects(store):
    """The Developer Preview tenant-isolation boundary: the same
    artifact_id in two different organizations' projects never collides,
    and a scoped get/query/all for one never returns the other's
    artifact."""
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
