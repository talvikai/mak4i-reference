from mak4i.store.base import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ArtifactStore,
    ArtifactStoreError,
    ConcurrentModificationError,
    Scope,
    VersionToken,
)

__all__ = [
    "ArtifactAlreadyExistsError",
    "ArtifactNotFoundError",
    "ArtifactStore",
    "ArtifactStoreError",
    "ConcurrentModificationError",
    "Scope",
    "VersionToken",
]

# Deliberately not exported here: LocalJSONStore, GCSArtifactStore.
# Core components (discovery/, resolution/, context/, audit/, api.py)
# depend on the ArtifactStore protocol above only. Concrete stores are
# imported directly (mak4i.store.local_json / mak4i.store.gcs) at the
# composition root (tests, CLI, MCP server startup) and injected in.
