from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from mak4i.models import Artifact, Provenance

IntegrityErrorKind = Literal["zero_active", "multiple_active", "dangling_pointer"]


class IntegrityError(BaseModel):
    """A lineage that failed closed rather than being guessed at.

    Distinct from Conflict: an integrity error is the store contradicting
    its own invariants within one lineage; a conflict is two legitimate
    decisions in different lineages disagreeing (MVP_ARCHITECTURE.md §18).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    lineage_id: str
    kind: IntegrityErrorKind
    detail: str


class DetectedConflict(BaseModel):
    """Resolver output: the current heads of two or more lineages that claim
    the same explicit subject — the same `artifact_type` and the same
    normalized `subject_key` (MAK-0004 §A2–§A3). Shared tags alone never
    form one. The engine turns this into the outward `Conflict`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str
    subject_key: str
    lineage_ids: list[str]
    artifacts: list[Artifact]


ConflictState = Literal["open", "resolving", "resolved"]
ResolutionAction = Literal["select_winner", "merge", "separate_subjects"]


class ConflictCandidate(BaseModel):
    """One contribution to a conflict (MAK-0004 §A5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    lineage_id: str
    version: str
    title: str
    created_at: datetime
    created_by: str
    provenance: Provenance | None = None
    content_sha256: str
    content: str | None = None
    tags: list[str] = []

    @classmethod
    def of(cls, artifact: Artifact, *, readable: bool = True) -> ConflictCandidate:
        return cls(
            artifact_id=artifact.artifact_id,
            lineage_id=artifact.lineage_id,
            version=artifact.version,
            title=artifact.title,
            created_at=artifact.created_at,
            created_by=artifact.created_by,
            provenance=artifact.provenance,
            content_sha256=content_sha256(artifact.content),
            content=artifact.content if readable else None,
            tags=list(artifact.tags),
        )


class ResolutionEffect(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    effect: Literal["withdrawn", "superseded", "unchanged"]
    successor_artifact_id: str | None = None


class ResultingHead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_key: str
    artifact_id: str


class ResolutionRecord(BaseModel):
    """The durable record of one resolution (MAK-0004 §A7.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolution_id: str
    conflict_id: str
    organization_id: str
    project: str
    artifact_type: str
    subject_key: str
    generation: int
    action: ResolutionAction
    parameters: dict[str, Any]
    reason: str
    candidate_artifact_ids: list[str]
    actor: Provenance
    permission: Literal["resolve"] = "resolve"
    effects: list[ResolutionEffect] = []
    resulting_heads: list[ResultingHead] = []
    state: Literal["pending", "completed", "aborted"]
    idempotency_key: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class Conflict(BaseModel):
    """A subject conflict as reported to callers (MAK-0004 §A4–§A5;
    protocol schema `conflict.schema.json`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    conflict_id: str
    state: ConflictState
    generation: int
    organization_id: str
    project: str
    artifact_type: str
    subject_key: str
    identical_content: bool
    reason: str
    candidates: list[ConflictCandidate]
    hidden_candidate_count: int = 0
    resolution: ResolutionRecord | None = None


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def conflict_id_for(
    organization_id: str, project: str, artifact_type: str, subject_key: str, generation: int
) -> str:
    """MAK-0004 §A4.1: stable while candidates come and go, new after each
    completed resolution."""
    material = "\n".join((organization_id, project, artifact_type, subject_key, str(generation)))
    return "cfl_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ResolutionResult:
    """Internal resolver output. `trace` makes every decision explainable
    (requirements doc §17); the engine turns this into the outward
    ContextPackage."""

    resolved: list[Artifact] = field(default_factory=list)
    conflicts: list[DetectedConflict] = field(default_factory=list)
    integrity_errors: list[IntegrityError] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
