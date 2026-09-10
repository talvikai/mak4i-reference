from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ArtifactStatus = Literal["active", "superseded"]


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
