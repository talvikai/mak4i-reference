from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ArtifactStatus = Literal["active", "superseded"]


class ClientMetadata(BaseModel):
    """What the connecting client *says* it is (MCP `clientInfo`, OAuth
    client name). Never used for authorization or authorship; always
    `verified: false` (MAK-0006 §7.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    version: str | None = None
    verified: Literal[False] = False


class Provenance(BaseModel):
    """Authenticated provenance recorded by the server at write time
    (MAK-0006 §7). Every field except `client` is derived from the verified
    credential / access token and the stored principal — never from the
    request. Immutable: renaming or deactivating the principal never
    rewrites it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str
    principal_type: Literal["human", "service", "agent"]
    agent_id: str | None
    display_name: str
    auth_method: Literal["credential", "oauth", "operator"]
    credential_id: str | None = None
    oauth_client_id: str | None = None
    client: ClientMetadata | None = None
    recorded_at: datetime


class Artifact(BaseModel):
    """A single MAK4I artifact.

    Field set matches the requirements doc's minimal example
    (MAK-0001-aligned), plus `organization_id` for the Developer Preview:
    `project` carries the canonical `project_id` and `organization_id` its
    owning organization. Both are opaque generic identifiers — no field
    here is named after or scoped to a technology/scenario (see CLAUDE.md).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    artifact_type: str
    organization_id: str
    project: str
    title: str
    content: str
    rationale: str | None = None
    status: ArtifactStatus
    version: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    lineage_id: str
    supersedes: str | None = None
    superseded_by: str | None = None
    tags: list[str] = Field(default_factory=list)
    # Optional, stable, machine-readable key naming the logical subject this
    # artifact decides (e.g. "session-cache"). Two current artifacts from
    # DIFFERENT lineages compete only when they share artifact_type AND the
    # same subject_key (see resolution/resolver.py). Tags are classification
    # and search metadata only and never establish identity or conflicts.
    # Never derived from title/content/tags; absent on pre-RC3 artifacts.
    subject_key: str | None = None
    # Provenance for an explicit subject release: set ONLY on the version
    # created by `supersede(..., release_subject_key=True)`, recording which
    # subject_key this lineage gave up (its `subject_key` is then None).
    released_subject_key: str | None = None
    # MAK-0006 §7: authenticated author, agent identity and how the write was
    # authenticated. Absent on versions written before v2.0.0-rc.1 (never
    # back-filled — MAK-0006 §7.5); `created_by` is the legacy attribution.
    provenance: Provenance | None = None

    @field_validator(
        "artifact_id", "artifact_type", "organization_id", "project", "title",
        "content", "version", "created_by", "lineage_id",
    )
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @field_validator("tags")
    @classmethod
    def _tags_are_nonempty_strings(cls, value: list[str]) -> list[str]:
        for tag in value:
            if not isinstance(tag, str) or not tag.strip():
                raise ValueError("tags must be non-empty strings")
        return value

    @field_validator("subject_key", "released_subject_key")
    @classmethod
    def _subject_key_normalized(cls, value: str | None) -> str | None:
        return normalize_subject_key(value)


# Fields added after v0.1.0-rc.2. Stores omit them while unset so artifacts
# that don't use them stay readable by an RC2 install (whose model rejects
# unknown fields) — only artifacts that actually carry a subject_key are
# RC3-only on disk.
_OMIT_WHEN_UNSET = ("subject_key", "released_subject_key", "provenance")


def dump_for_storage(artifact: Artifact) -> str:
    """JSON for persisting `artifact`, omitting post-RC2 fields while unset."""
    exclude = {name for name in _OMIT_WHEN_UNSET if getattr(artifact, name) is None}
    return artifact.model_dump_json(exclude=exclude)


def normalize_subject_key(value: str | None) -> str | None:
    """Minimal, predictable normalization for `subject_key`: `None` stays
    `None`; otherwise surrounding whitespace is trimmed and an empty result
    is rejected. Nothing else (no case folding, no derivation)."""
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("subject_key must not be empty; omit it instead")
    return trimmed


def bump_version(version: str) -> str:
    """Increment a version string's integer component, e.g. "1.0" -> "2.0".

    Every example in the requirements/architecture docs uses whole-number
    versions ("1.0", "2.0", ...); this MVP does not need finer-grained
    versioning than that.
    """
    try:
        major_str, _, _minor = version.partition(".")
        major = int(major_str)
    except ValueError as exc:
        raise ValueError(f"cannot bump non-numeric version: {version!r}") from exc
    return f"{major + 1}.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
