import json
import uuid
from datetime import datetime, timezone

from mak4i.context import STANDARD_INSTRUCTION, ContextBuilder
from mak4i.models import Artifact
from mak4i.resolution import Conflict, IntegrityError, ResolutionResult

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def _artifact(**overrides) -> Artifact:
    base = dict(
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        organization_id="org_test",
        project="schedovia",
        title="Application caching technology",
        content="Use Redis for application caching.",
        rationale="Selected for fast shared caching and established client support.",
        status="active",
        version="1.0",
        created_by="claude.ai",
        created_at=NOW,
        updated_at=NOW,
        lineage_id="decision-cache-001",
        supersedes=None,
        superseded_by=None,
        tags=["caching", "redis", "architecture"],
    )
    base.update(overrides)
    return Artifact(**base)


def test_build_wraps_resolution_result():
    artifact = _artifact()
    resolution = ResolutionResult(resolved=[artifact], trace=["step one", "step two"])

    package = ContextBuilder().build(resolution)

    assert package.artifacts == [artifact]
    assert package.conflicts == []
    assert package.integrity_errors == []
    assert package.instruction == STANDARD_INSTRUCTION
    assert package.resolution_trace == ["step one", "step two"]
    assert uuid.UUID(package.resolution_trace_id)  # valid uuid4 string


def test_build_carries_conflicts_and_integrity_errors():
    from mak4i.resolution import ConflictCandidate

    first, second = _artifact(), _artifact(artifact_id="decision-cache-004")
    conflict = Conflict(
        conflict_id="cfl_" + "1" * 32,
        state="open",
        generation=0,
        organization_id=first.organization_id,
        project=first.project,
        artifact_type="architecture_decision",
        subject_key="application-cache",
        identical_content=False,
        reason="2 lineage(s) claim the subject",
        candidates=[ConflictCandidate.of(first), ConflictCandidate.of(second)],
    )
    integrity_error = IntegrityError(
        lineage_id="decision-db-001", kind="zero_active", detail="no active member"
    )
    resolution = ResolutionResult(
        resolved=[], conflicts=[], integrity_errors=[integrity_error], trace=["x"]
    )

    package = ContextBuilder().build(resolution, conflicts=[conflict])

    assert package.conflicts == [conflict]
    assert package.integrity_errors == [integrity_error]


def test_package_is_json_serializable():
    resolution = ResolutionResult(resolved=[_artifact()], trace=["step"])
    package = ContextBuilder().build(resolution)

    payload = json.loads(package.model_dump_json())
    assert payload["artifacts"][0]["artifact_id"] == "decision-cache-001"
    assert payload["instruction"] == STANDARD_INSTRUCTION


def test_each_build_gets_a_fresh_trace_id():
    resolution = ResolutionResult(resolved=[_artifact()], trace=["step"])
    builder = ContextBuilder()

    first = builder.build(resolution)
    second = builder.build(resolution)

    assert first.resolution_trace_id != second.resolution_trace_id
