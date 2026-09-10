from __future__ import annotations

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage

from mak4i.models import Artifact
from mak4i.store._filtering import filter_artifacts
from mak4i.store.base import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ConcurrentModificationError,
    Scope,
    VersionToken,
)


class GCSArtifactStore:
    """Default MVP persistent store (MVP_ARCHITECTURE.md §4): one JSON
    object per artifact in a private GCS bucket, using GCS generation
    preconditions for the same create-if-absent / update-if-unchanged
    contract LocalJSONStore emulates with a file lock — except here the
    atomicity is real and GCS-native, not something we fake.

    Layout (Developer Preview): `gs://<bucket>/<organization_id>/<project>/
    <artifact_id>.json`. Every read is scoped by `(organization_id,
    project)`, so artifacts from different projects — or from a different
    organization's project of the same name — never collide and are never
    enumerated together.

    `client` is injectable so tests can supply a fake/mocked
    google.cloud.storage.Client without touching real GCS; left unset, it
    picks up Application Default Credentials the normal way (a Cloud Run
    container's attached service account, or a developer's own `gcloud
    auth application-default login`, per requirements §15.1 — never a
    committed key file).
    """

    def __init__(
        self,
        bucket_name: str,
        *,
        project: str | None = None,
        client: storage.Client | None = None,
    ):
        self._client = client or storage.Client(project=project)
        self._bucket = self._client.bucket(bucket_name)

    def get(
        self, organization_id: str, project: str, artifact_id: str
    ) -> tuple[Artifact, VersionToken] | None:
        blob = self._bucket.get_blob(self._blob_name(organization_id, project, artifact_id))
        if blob is None:
            return None
        content = blob.download_as_bytes()
        return Artifact.model_validate_json(content), str(blob.generation)

    def put_new(self, artifact: Artifact) -> None:
        blob = self._bucket.blob(self._blob_name_for(artifact))
        try:
            # ifGenerationMatch=0 is GCS's create-if-absent precondition:
            # the write only succeeds if no live object exists yet.
            blob.upload_from_string(
                _serialize(artifact), content_type="application/json", if_generation_match=0
            )
        except PreconditionFailed as exc:
            raise ArtifactAlreadyExistsError(artifact.artifact_id) from exc

    def put_if_match(self, artifact: Artifact, expected_version_token: VersionToken) -> None:
        blob = self._bucket.blob(self._blob_name_for(artifact))
        try:
            # ifGenerationMatch=<the generation `get()` returned> is GCS's
            # update-if-unchanged-since-read precondition — this IS the
            # concurrency control MVP_ARCHITECTURE.md §5 calls for; no
            # separate lock is needed, the check happens atomically on
            # GCS's side.
            blob.upload_from_string(
                _serialize(artifact),
                content_type="application/json",
                if_generation_match=int(expected_version_token),
            )
        except PreconditionFailed as exc:
            # The write was already correctly rejected, atomically, by
            # GCS. This extra read only decides which of our two documented
            # exceptions best explains why, for the caller — it does not
            # change what got written (nothing did).
            if self._bucket.get_blob(self._blob_name_for(artifact)) is None:
                raise ArtifactNotFoundError(artifact.artifact_id) from exc
            raise ConcurrentModificationError(artifact.artifact_id) from exc

    def query(
        self,
        organization_id: str,
        project: str,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
    ) -> list[Artifact]:
        return filter_artifacts(
            self._iter_prefix(f"{organization_id}/{project}/"),
            organization_id,
            project,
            artifact_type,
            tags,
            status,
        )

    def query_many(
        self,
        scopes: list[Scope],
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
    ) -> list[Artifact]:
        results: list[Artifact] = []
        for organization_id, project in scopes:
            results.extend(
                self.query(organization_id, project, artifact_type, tags, status)
            )
        return results

    def all(
        self,
        organization_id: str | None = None,
        project: str | None = None,
    ) -> list[Artifact]:
        prefix = f"{organization_id}/" if organization_id is not None else None
        return [
            a
            for a in self._iter_prefix(prefix)
            if (organization_id is None or a.organization_id == organization_id)
            and (project is None or a.project == project)
        ]

    # -- internals --------------------------------------------------------

    @staticmethod
    def _blob_name(organization_id: str, project: str, artifact_id: str) -> str:
        return f"{organization_id}/{project}/{artifact_id}.json"

    def _blob_name_for(self, artifact: Artifact) -> str:
        return self._blob_name(
            artifact.organization_id, artifact.project, artifact.artifact_id
        )

    def _iter_prefix(self, prefix: str | None):
        for blob in self._client.list_blobs(self._bucket, prefix=prefix):
            content = blob.download_as_bytes()
            yield Artifact.model_validate_json(content)


def _serialize(artifact: Artifact) -> bytes:
    return artifact.model_dump_json().encode("utf-8")
