from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from mak4i.models import Artifact

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


class Conflict(BaseModel):
    """Two or more genuinely independent active artifacts, in distinct
    lineages, that are both applicable to the same scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str
    lineage_ids: list[str]
    artifacts: list[Artifact]


@dataclass(frozen=True)
class ResolutionResult:
    """Internal resolver output. `trace` makes every decision explainable
    (requirements doc §17); ContextBuilder turns this into the outward
    ContextPackage."""

    resolved: list[Artifact] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    integrity_errors: list[IntegrityError] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
