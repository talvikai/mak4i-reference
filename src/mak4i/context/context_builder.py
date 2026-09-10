from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict

from mak4i.models import Artifact
from mak4i.resolution.types import Conflict, IntegrityError, ResolutionResult

STANDARD_INSTRUCTION = (
    "Use only the artifacts below as current project knowledge for this task. "
    "If `conflicts` is non-empty, do not silently pick one — surface the "
    "conflict to the user and ask them to clarify; a follow-up supersede is "
    "the normal resolution. If `integrity_errors` is non-empty, treat the "
    "affected lineage as having no current answer and say so explicitly "
    "rather than guessing."
)


class ContextPackage(BaseModel):
    """Compact, model-neutral result handed back through the MCP tool call.

    [PROTOCOL] shape per MVP_ARCHITECTURE.md §8: artifacts, conflicts,
    integrity_errors, instruction, resolution_trace_id. `resolution_trace`
    is carried alongside for the Audit Logger (§9) to log against the same
    correlation id — it is not part of the minimal tool-result shape itself,
    but every field here must stay small: this is deliberately the
    *smallest useful set* of current applicable artifacts, not the whole
    project (§8).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifacts: list[Artifact]
    conflicts: list[Conflict]
    integrity_errors: list[IntegrityError]
    instruction: str
    resolution_trace_id: str
    resolution_trace: list[str]


class ContextBuilder:
    """Turns a ResolutionResult into the outward ContextPackage."""

    def build(
        self, resolution: ResolutionResult, resolution_trace_id: str | None = None
    ) -> ContextPackage:
        """`resolution_trace_id` may be supplied by the caller so it can
        match the `correlation_id` the engine already used to log DISCOVER
        and RESOLVE for the same task (§2: "with a correlation_id") —
        DISCOVER/RESOLVE/INJECT must share one id. Left unset, a fresh
        uuid4 is generated, as before."""
        return ContextPackage(
            artifacts=resolution.resolved,
            conflicts=resolution.conflicts,
            integrity_errors=resolution.integrity_errors,
            instruction=STANDARD_INSTRUCTION,
            resolution_trace_id=resolution_trace_id or str(uuid.uuid4()),
            resolution_trace=resolution.trace,
        )
