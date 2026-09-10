from __future__ import annotations

from mak4i.models import Artifact
from mak4i.store.base import ArtifactStore


class Discovery:
    """Deterministic candidate lookup.

    [PROTOCOL] per requirements doc §5: "find candidates using deterministic
    metadata filters first: project/scope, artifact type, tags/keywords and
    status. Semantic/vector search is optional and not required." This is a
    thin, intentionally dumb pass-through to ArtifactStore.query — no
    scenario-specific ranking or filtering logic belongs here. The
    `(organization_id, project)` scope is supplied by the engine after the
    Authorizer has confirmed the principal may read it.
    """

    def __init__(self, store: ArtifactStore):
        self._store = store

    def find_candidates(
        self,
        organization_id: str,
        project: str,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
    ) -> list[Artifact]:
        return self._store.query(
            organization_id=organization_id,
            project=project,
            artifact_type=artifact_type,
            tags=tags,
            status=status,
        )
