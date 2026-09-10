"""Integration test against the real MAK4I GCS bucket.

Skipped by default — `uv run pytest` never touches real GCP infrastructure
unless a human explicitly opts in by setting MAK4I_RUN_GCS_INTEGRATION_TEST.
This is deliberate two-layer gating: the target project/bucket/service
account have sensible defaults (the values settled on for this MVP), but
nothing runs against them without the explicit opt-in flag, so a routine
`uv run pytest` (e.g. in CI later) always stays offline.

Authenticates via short-lived impersonated credentials built from the
runner's own `gcloud auth application-default login` session — this does
NOT touch or overwrite the machine's global ADC file, and no key file is
ever written or read (matches requirements §15.1: no committed
service-account keys). The base identity needs roles/iam.serviceAccount
TokenCreator on the target service account; the service account itself
needs roles/storage.objectAdmin scoped to the bucket — see the repo's
README/PR notes for the exact `gcloud` commands used to set this up.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MAK4I_RUN_GCS_INTEGRATION_TEST") != "1",
    reason="set MAK4I_RUN_GCS_INTEGRATION_TEST=1 to run this against real GCS",
)

PROJECT = os.environ.get("MAK4I_GCS_TEST_PROJECT", "example-mak4i-project")
BUCKET = os.environ.get("MAK4I_GCS_TEST_BUCKET", "example-mak4i-artifacts")
SERVICE_ACCOUNT = os.environ.get(
    "MAK4I_GCS_TEST_SERVICE_ACCOUNT",
    "mak4i-ci@example-mak4i-project.iam.gserviceaccount.com",
)
TEST_ORG = os.environ.get("MAK4I_GCS_TEST_ORG", "org_integration_test")


@pytest.fixture(scope="module")
def gcs_client():
    import google.auth
    from google.auth import impersonated_credentials
    from google.cloud import storage

    source_credentials, _ = google.auth.default()
    # quota_project_id is set explicitly here (rather than via `gcloud auth
    # application-default set-quota-project`) so this test never mutates
    # the runner's global ADC file, regardless of what quota project their
    # personal gcloud session happens to be configured with.
    source_credentials = source_credentials.with_quota_project(PROJECT)
    target_credentials = impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=SERVICE_ACCOUNT,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return storage.Client(project=PROJECT, credentials=target_credentials)


@pytest.fixture
def store(gcs_client):
    from mak4i.store.gcs import GCSArtifactStore

    return GCSArtifactStore(BUCKET, client=gcs_client)


@pytest.fixture
def test_artifact_id():
    """A unique id per test run so concurrent/repeated runs never collide,
    and so cleanup can target exactly what this test wrote."""
    return f"test-integration-{uuid.uuid4().hex[:12]}"


@pytest.fixture(autouse=True)
def cleanup(gcs_client, test_artifact_id):
    yield
    # Best-effort cleanup so the real bucket never accumulates test litter,
    # even if an assertion above failed mid-test.
    bucket = gcs_client.bucket(BUCKET)
    blob = bucket.blob(f"{TEST_ORG}/schedovia/{test_artifact_id}.json")
    if blob.exists():
        blob.delete()


def _artifact(artifact_id: str, **overrides):
    from mak4i.models import Artifact

    now = datetime.now(timezone.utc)
    base = dict(
        artifact_id=artifact_id,
        artifact_type="architecture_decision",
        organization_id=TEST_ORG,
        project="schedovia",
        title="Integration test artifact — safe to delete",
        content="This artifact is written by an automated integration test.",
        rationale=None,
        status="active",
        version="1.0",
        created_by="integration-test",
        created_at=now,
        updated_at=now,
        lineage_id=artifact_id,
        supersedes=None,
        superseded_by=None,
        tags=["integration-test"],
    )
    base.update(overrides)
    return Artifact(**base)


def test_create_if_absent_precondition_against_real_gcs(store, test_artifact_id):
    from mak4i.store.base import ArtifactAlreadyExistsError

    artifact = _artifact(test_artifact_id)
    store.put_new(artifact)

    result = store.get(TEST_ORG, "schedovia", test_artifact_id)
    assert result is not None
    fetched, token = result
    assert fetched.artifact_id == test_artifact_id
    assert token  # a real GCS generation number, opaque to us

    with pytest.raises(ArtifactAlreadyExistsError):
        store.put_new(artifact)


def test_conditional_update_precondition_against_real_gcs(store, test_artifact_id):
    from mak4i.store.base import ConcurrentModificationError

    original = _artifact(test_artifact_id)
    store.put_new(original)
    _, token = store.get(TEST_ORG, "schedovia", test_artifact_id)

    updated = original.model_copy(update={"content": "Updated by the integration test."})
    store.put_if_match(updated, expected_version_token=token)

    fetched, new_token = store.get(TEST_ORG, "schedovia", test_artifact_id)
    assert fetched.content == "Updated by the integration test."
    assert new_token != token

    # The stale token must now be rejected — this is the real GCS
    # ifGenerationMatch race-closing check, not the in-memory fake's
    # emulation of it.
    with pytest.raises(ConcurrentModificationError):
        store.put_if_match(
            original.model_copy(update={"content": "Should be rejected."}),
            expected_version_token=token,
        )


def test_put_if_match_on_nonexistent_artifact_raises_against_real_gcs(store, test_artifact_id):
    from mak4i.store.base import ArtifactNotFoundError

    with pytest.raises(ArtifactNotFoundError):
        store.put_if_match(_artifact(test_artifact_id), expected_version_token="1")


def test_query_and_all_against_real_gcs(store, test_artifact_id):
    store.put_new(_artifact(test_artifact_id, tags=["integration-test", "caching"]))

    results = store.query(TEST_ORG, "schedovia", tags=["integration-test"], status="active")
    assert test_artifact_id in {a.artifact_id for a in results}

    all_artifacts = store.all(TEST_ORG, "schedovia")
    assert test_artifact_id in {a.artifact_id for a in all_artifacts}
