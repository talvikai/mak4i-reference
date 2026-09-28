from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from mak4i.models import Artifact, dump_for_storage
from mak4i.store._filtering import filter_artifacts
from mak4i.store.base import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ConcurrentModificationError,
    Scope,
    VersionToken,
)


class LocalJSONStore:
    """Filesystem-backed ArtifactStore for unit tests and offline dev only.

    [MVP CHOICE]: this is NOT where demo data lives — GCSArtifactStore is
    the MVP's persistent store (docs/MVP_ARCHITECTURE.md §4). Nothing
    outside this module (and the tests that construct it directly) may
    import LocalJSONStore; core components depend on the ArtifactStore
    protocol in store/base.py only.

    Layout: `<root>/<organization_id>/<project>/<artifact_id>.json`,
    containing `{"generation": int, "artifact": {...}}`. `generation` is a
    monotonically bumped integer that stands in for GCS's object
    generation number, giving the same create-if-absent /
    update-if-unchanged-since-read contract without real GCS credentials.
    An `fcntl` exclusive lock per artifact serializes the
    read-check-write sequence so put_new/put_if_match are atomic with
    respect to concurrent callers on this machine.
    """

    def __init__(self, root: Path | str):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def get(
        self, organization_id: str, project: str, artifact_id: str
    ) -> tuple[Artifact, VersionToken] | None:
        record = self._read_record(organization_id, project, artifact_id)
        if record is None:
            return None
        artifact, generation = record
        return artifact, str(generation)

    def put_new(self, artifact: Artifact) -> None:
        with self._locked(artifact):
            if self._path_for(artifact).exists():
                raise ArtifactAlreadyExistsError(artifact.artifact_id)
            self._write_record(artifact, generation=1)

    def put_if_match(
        self, artifact: Artifact, expected_version_token: VersionToken
    ) -> None:
        with self._locked(artifact):
            record = self._read_record(
                artifact.organization_id, artifact.project, artifact.artifact_id
            )
            if record is None:
                raise ArtifactNotFoundError(artifact.artifact_id)
            _current_artifact, current_generation = record
            if str(current_generation) != expected_version_token:
                raise ConcurrentModificationError(artifact.artifact_id)
            self._write_record(artifact, generation=current_generation + 1)

    def query(
        self,
        organization_id: str,
        project: str,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
    ) -> list[Artifact]:
        return filter_artifacts(
            self._iter_all(organization_id, project),
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
        return [
            artifact
            for artifact in self._iter_all(organization_id, project)
            if (organization_id is None or artifact.organization_id == organization_id)
            and (project is None or artifact.project == project)
        ]

    # -- internals --------------------------------------------------------

    def _dir_for(self, organization_id: str, project: str) -> Path:
        return self._root / organization_id / project

    def _path_for(self, artifact: Artifact) -> Path:
        return (
            self._dir_for(artifact.organization_id, artifact.project)
            / f"{artifact.artifact_id}.json"
        )

    def _path(self, organization_id: str, project: str, artifact_id: str) -> Path:
        return self._dir_for(organization_id, project) / f"{artifact_id}.json"

    def _locked(self, artifact: Artifact):
        lock_dir = self._dir_for(artifact.organization_id, artifact.project)
        lock_dir.mkdir(parents=True, exist_ok=True)
        return _FileLock(lock_dir / f"{artifact.artifact_id}.lock")

    def _read_record(
        self, organization_id: str, project: str, artifact_id: str
    ) -> tuple[Artifact, int] | None:
        path = self._path(organization_id, project, artifact_id)
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        return Artifact.model_validate(record["artifact"]), record["generation"]

    def _write_record(self, artifact: Artifact, generation: int) -> None:
        path = self._path_for(artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        record = {
            "generation": generation,
            "artifact": json.loads(dump_for_storage(artifact)),
        }
        tmp_path.write_text(json.dumps(record, indent=2))
        os.replace(tmp_path, path)  # atomic within the same filesystem

    def _iter_all(
        self, organization_id: str | None = None, project: str | None = None
    ):
        if organization_id is not None and project is not None:
            glob = f"{organization_id}/{project}/*.json"
        elif organization_id is not None:
            glob = f"{organization_id}/*/*.json"
        else:
            glob = "*/*/*.json"
        for path in sorted(self._root.glob(glob)):
            if path.name.endswith(".json.tmp"):
                continue
            record = json.loads(path.read_text())
            yield Artifact.model_validate(record["artifact"])


class _FileLock:
    """Exclusive fcntl lock on a sidecar file, held for one store operation."""

    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._handle = None

    def __enter__(self) -> None:
        self._handle = open(self._lock_path, "a+")
        fcntl.flock(self._handle, fcntl.LOCK_EX)

    def __exit__(self, *exc_info) -> None:
        fcntl.flock(self._handle, fcntl.LOCK_UN)
        self._handle.close()
