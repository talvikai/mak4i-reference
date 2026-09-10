"""Unit tests for scripts/migrate_gcs_layout.py against a fake in-memory
GCS client (same fake used by tests/test_gcs_store.py) — no real GCS
infrastructure is touched by this test module, matching the Deployment
gate: M8 only exercises the dry-run/execute/validate logic, never live
Cloud Storage."""

import json
from datetime import datetime, timezone

from migrate_gcs_layout import execute_migration, main, plan_migration, validate_migration

from mak4i.models import Artifact

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)

ORG = "org_wd"
PROJECT = "prj_schedovia"


class _FakeBlob:
    def __init__(self, bucket, name):
        self._bucket = bucket
        self.name = name

    def download_as_bytes(self) -> bytes:
        return self._bucket.objects[self.name]

    def upload_from_string(self, data, content_type=None) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._bucket.objects[self.name] = data


class _FakeBucket:
    def __init__(self, name):
        self.name = name
        self.objects: dict[str, bytes] = {}

    def blob(self, name):
        return _FakeBlob(self, name)

    def get_blob(self, name):
        if name not in self.objects:
            return None
        return _FakeBlob(self, name)


class _FakeClient:
    def list_blobs(self, bucket, prefix=""):
        for key in sorted(bucket.objects):
            if key.startswith(prefix):
                yield _FakeBlob(bucket, key)


def _legacy_artifact_json(artifact_id: str, **overrides) -> bytes:
    """Old-layout record: no organization_id at all (pre-M4 schema), and
    project is still the literal "schedovia" string."""
    base = dict(
        artifact_id=artifact_id,
        artifact_type="architecture_decision",
        project="schedovia",
        title="Application caching technology",
        content="Use Redis for application caching.",
        rationale=None,
        status="active",
        version="1.0",
        created_by="claude.ai",
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        lineage_id=artifact_id,
        supersedes=None,
        superseded_by=None,
        tags=["caching"],
    )
    base.update(overrides)
    return json.dumps(base).encode("utf-8")


def _seeded_bucket(*artifact_ids: str) -> _FakeBucket:
    bucket = _FakeBucket("example-mak4i-artifacts")
    for artifact_id in artifact_ids:
        bucket.objects[f"schedovia/{artifact_id}.json"] = _legacy_artifact_json(artifact_id)
    return bucket


def test_plan_migration_maps_every_old_object_with_no_writes():
    client = _FakeClient()
    bucket = _seeded_bucket("decision-cache-001", "decision-db-002")

    plan = plan_migration(client, bucket, ORG, PROJECT)

    assert {item.artifact_id for item in plan} == {"decision-cache-001", "decision-db-002"}
    assert all(item.new_name == f"{ORG}/{PROJECT}/{item.artifact_id}.json" for item in plan)
    # No writes happened — the source objects are the only objects present.
    assert set(bucket.objects) == {
        "schedovia/decision-cache-001.json",
        "schedovia/decision-db-002.json",
    }


def test_execute_migration_copies_and_stamps_organization_id_leaving_source_untouched():
    client = _FakeClient()
    bucket = _seeded_bucket("decision-cache-001")
    original_source = bytes(bucket.objects["schedovia/decision-cache-001.json"])

    written = execute_migration(client, bucket, ORG, PROJECT)

    assert len(written) == 1
    new_record = json.loads(bucket.objects[f"{ORG}/{PROJECT}/decision-cache-001.json"])
    assert new_record["organization_id"] == ORG
    assert new_record["project"] == PROJECT
    assert new_record["artifact_id"] == "decision-cache-001"
    # The new record validates as a full, current Artifact.
    Artifact.model_validate(new_record)
    # The source object is byte-for-byte untouched.
    assert bucket.objects["schedovia/decision-cache-001.json"] == original_source


def test_execute_migration_is_idempotent_on_rerun():
    client = _FakeClient()
    bucket = _seeded_bucket("decision-cache-001", "decision-db-002")

    first = execute_migration(client, bucket, ORG, PROJECT)
    assert len(first) == 2

    second = execute_migration(client, bucket, ORG, PROJECT)
    assert second == []  # both destinations already hold identical content

    # Still exactly one destination object per source object — no
    # duplication or corruption from the rerun.
    new_names = [k for k in bucket.objects if k.startswith(f"{ORG}/{PROJECT}/")]
    assert len(new_names) == 2


def test_validate_migration_passes_after_a_clean_execute():
    client = _FakeClient()
    bucket = _seeded_bucket("decision-cache-001", "decision-db-002")
    execute_migration(client, bucket, ORG, PROJECT)

    report = validate_migration(client, bucket, ORG, PROJECT)

    assert report.ok
    assert report.old_count == 2
    assert report.new_count == 2
    assert report.content_mismatches == []
    assert report.integrity_errors == []


def test_validate_migration_reports_a_missing_destination_object():
    client = _FakeClient()
    bucket = _seeded_bucket("decision-cache-001", "decision-db-002")
    execute_migration(client, bucket, ORG, PROJECT)

    # Simulate a partial/failed copy by deleting one destination object.
    del bucket.objects[f"{ORG}/{PROJECT}/decision-db-002.json"]

    report = validate_migration(client, bucket, ORG, PROJECT)

    assert not report.ok
    assert report.new_count == 1
    assert any("decision-db-002" in m for m in report.content_mismatches)


def test_validate_migration_reports_a_zero_active_lineage_after_migration():
    """A lineage with no active member (e.g. a dangling superseded-only
    chain in the legacy data) must surface as an integrity error, not a
    silent pass — the same fail-closed rule the resolver enforces
    elsewhere (MVP_ARCHITECTURE.md §5)."""
    client = _FakeClient()
    bucket = _FakeBucket("b")
    bucket.objects["schedovia/decision-cache-001.json"] = _legacy_artifact_json(
        "decision-cache-001", status="superseded", superseded_by="decision-cache-002"
    )
    execute_migration(client, bucket, ORG, PROJECT)

    report = validate_migration(client, bucket, ORG, PROJECT)

    assert not report.ok
    assert any("zero_active" in e for e in report.integrity_errors)


def test_main_dry_run_makes_no_writes(capsys):
    client = _FakeClient()
    bucket = _seeded_bucket("decision-cache-001")

    exit_code = main(
        ["--bucket", "b", "--organization-id", ORG, "--project-id", PROJECT],
        client=client,
        bucket=bucket,
    )

    assert exit_code == 0
    assert set(bucket.objects) == {"schedovia/decision-cache-001.json"}
    assert "Dry run only" in capsys.readouterr().out


def test_main_execute_writes_and_validates_successfully(capsys):
    client = _FakeClient()
    bucket = _seeded_bucket("decision-cache-001", "decision-db-002")

    exit_code = main(
        ["--bucket", "b", "--organization-id", ORG, "--project-id", PROJECT, "--execute"],
        client=client,
        bucket=bucket,
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Copied 2 object(s)" in out
    assert "OK — every validation passed" in out
    assert set(bucket.objects) == {
        "schedovia/decision-cache-001.json",
        "schedovia/decision-db-002.json",
        f"{ORG}/{PROJECT}/decision-cache-001.json",
        f"{ORG}/{PROJECT}/decision-db-002.json",
    }
