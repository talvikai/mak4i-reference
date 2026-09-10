from __future__ import annotations

from typing import Iterable

from mak4i.models import Artifact


def filter_artifacts(
    artifacts: Iterable[Artifact],
    organization_id: str,
    project: str,
    artifact_type: str | None = None,
    tags: list[str] | None = None,
    status: str | None = "active",
) -> list[Artifact]:
    """Shared ArtifactStore.query filter logic.

    Internal to store/ — not part of the public ArtifactStore surface.
    Both LocalJSONStore and GCSArtifactStore fetch their own candidate set
    (a directory scan vs. a GCS listing) and then apply this exact same
    deterministic rule, so "same code path regardless of backend" is
    provable rather than just asserted. `organization_id` + `project`
    together scope the query to one project of one organization.
    """
    results = []
    for artifact in artifacts:
        if artifact.organization_id != organization_id:
            continue
        if artifact.project != project:
            continue
        if artifact_type is not None and artifact.artifact_type != artifact_type:
            continue
        if tags and not set(tags).issubset(artifact.tags):
            continue
        if status is not None and artifact.status != status:
            continue
        results.append(artifact)
    return results
