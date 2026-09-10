from __future__ import annotations

from typing import Protocol, runtime_checkable

from mak4i.models import Artifact

VersionToken = str
"""Opaque optimistic-concurrency token returned alongside a read.

A GCS object `generation` number for GCSArtifactStore, a monotonically
bumped integer stamp for LocalJSONStore. Callers must treat it as opaque —
compare it for equality only, never parse or order it.
"""

Scope = tuple[str, str]
"""An (organization_id, project_id) pair — the unit of tenant isolation.
Every artifact belongs to exactly one scope."""


class ArtifactStoreError(Exception):
    """Base class for all ArtifactStore errors."""


class ArtifactAlreadyExistsError(ArtifactStoreError):
    def __init__(self, artifact_id: str):
        super().__init__(f"artifact already exists: {artifact_id}")
        self.artifact_id = artifact_id


class ArtifactNotFoundError(ArtifactStoreError):
    def __init__(self, artifact_id: str):
        super().__init__(f"artifact not found: {artifact_id}")
        self.artifact_id = artifact_id


class ConcurrentModificationError(ArtifactStoreError):
    def __init__(self, artifact_id: str):
        super().__init__(
            f"artifact changed since the given version token was read: {artifact_id}"
        )
        self.artifact_id = artifact_id


@runtime_checkable
class ArtifactStore(Protocol):
    """Storage abstraction for MAK4I artifacts.

    [PROTOCOL]: this interface, and only this interface, is what
    discovery/, resolution/, context/, audit/, and api.py may depend on.
    No core component may import a concrete implementation (LocalJSONStore,
    GCSArtifactStore) directly — a store is constructed once at the
    composition root (CLI/tests/MCP server startup) and passed in.

    Every read is scoped by `(organization_id, project)` — the Developer
    Preview tenant-isolation boundary. `put_new` / `put_if_match` derive the
    scope from the artifact's own `organization_id` / `project` fields.
    """

    def get(
        self, organization_id: str, project: str, artifact_id: str
    ) -> tuple[Artifact, VersionToken] | None:
        """Return (artifact, version_token), or None if no artifact with
        that id exists in that organization's project."""
        ...

    def put_new(self, artifact: Artifact) -> None:
        """Create-if-absent. Raises ArtifactAlreadyExistsError on collision
        within the artifact's own (organization_id, project) scope."""
        ...

    def put_if_match(
        self, artifact: Artifact, expected_version_token: VersionToken
    ) -> None:
        """Conditional update. Raises ArtifactNotFoundError if the artifact
        does not exist, or ConcurrentModificationError if it changed since
        `expected_version_token` was read."""
        ...

    def query(
        self,
        organization_id: str,
        project: str,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
    ) -> list[Artifact]:
        """Deterministic metadata filter within one (organization_id,
        project) scope: optional type, optional tags (all must be present on
        the artifact), optional status (None means any status)."""
        ...

    def query_many(
        self,
        scopes: list[Scope],
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
    ) -> list[Artifact]:
        """Same filter as `query`, applied across several scopes and
        concatenated — the storage primitive behind authorized
        cross-project search. An empty `scopes` list returns `[]`."""
        ...

    def all(
        self,
        organization_id: str | None = None,
        project: str | None = None,
    ) -> list[Artifact]:
        """Every artifact, any status, optionally narrowed by organization
        and/or project. Used by the resolver (always scoped) and by
        operator/migration tooling (may be unscoped)."""
        ...
