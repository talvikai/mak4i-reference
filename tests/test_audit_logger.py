import json
import logging
from datetime import datetime, timezone

import pytest

from mak4i.audit import LOGGER_NAME, AuditContext, AuditLogger, estimate_tokens
from mak4i.context import ContextBuilder
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


@pytest.fixture
def audit(caplog) -> AuditLogger:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    return AuditLogger()


def _records(caplog) -> list[dict]:
    return [json.loads(r.message) for r in caplog.records if r.name == LOGGER_NAME]


def test_log_emits_one_json_line_with_standard_fields(audit, caplog):
    result = audit.log("RESPONSE", correlation_id="corr-1", actor="claude.ai", summary="ok")

    records = _records(caplog)
    assert len(records) == 1
    assert records[0] == result
    assert records[0]["event"] == "RESPONSE"
    assert records[0]["correlation_id"] == "corr-1"
    assert records[0]["actor"] == "claude.ai"
    assert records[0]["summary"] == "ok"
    assert "ts" in records[0]


def test_log_create(audit, caplog):
    audit.log_create(correlation_id="corr-1", actor="claude.ai", artifact=_artifact())
    record = _records(caplog)[0]
    assert record["event"] == "CREATE"
    assert record["artifact_id"] == "decision-cache-001"
    assert record["lineage_id"] == "decision-cache-001"
    assert record["version"] == "1.0"


def test_log_supersede(audit, caplog):
    new_artifact = _artifact(artifact_id="decision-cache-002", supersedes="decision-cache-001", version="2.0")
    audit.log_supersede(
        correlation_id="corr-1",
        actor="claude.ai",
        old_id="decision-cache-001",
        new_artifact=new_artifact,
        reason="License concerns.",
    )
    record = _records(caplog)[0]
    assert record["event"] == "SUPERSEDE"
    assert record["old_id"] == "decision-cache-001"
    assert record["new_id"] == "decision-cache-002"
    assert record["reason"] == "License concerns."


def test_log_supersede_rejected(audit, caplog):
    audit.log_supersede_rejected(
        correlation_id="corr-1", actor="claude.ai", old_id="decision-cache-001", reason="not active"
    )
    record = _records(caplog)[0]
    assert record["event"] == "SUPERSEDE_REJECTED"
    assert record["old_id"] == "decision-cache-001"


def test_log_discover(audit, caplog):
    audit.log_discover(
        correlation_id="corr-1",
        actor="chatgpt",
        project="schedovia",
        artifact_type="architecture_decision",
        tags=["caching"],
        candidate_ids=["decision-cache-001"],
    )
    record = _records(caplog)[0]
    assert record["event"] == "DISCOVER"
    assert record["candidate_ids"] == ["decision-cache-001"]


def test_log_resolve(audit, caplog):
    resolution = ResolutionResult(resolved=[_artifact()], trace=["step one"])
    audit.log_resolve(correlation_id="corr-1", actor="chatgpt", resolution=resolution)
    record = _records(caplog)[0]
    assert record["event"] == "RESOLVE"
    assert record["resolved_ids"] == ["decision-cache-001"]
    assert record["conflict_count"] == 0
    assert record["integrity_error_count"] == 0
    assert record["trace"] == ["step one"]


def test_log_conflict(audit, caplog):
    conflict = Conflict(
        artifact_type="architecture_decision",
        lineage_ids=["decision-cache-001", "decision-cache-004"],
        artifacts=[_artifact(), _artifact(artifact_id="decision-cache-004")],
    )
    audit.log_conflict(correlation_id="corr-1", actor="chatgpt", conflict=conflict)
    record = _records(caplog)[0]
    assert record["event"] == "CONFLICT"
    assert record["candidates"] == ["decision-cache-001", "decision-cache-004"]


def test_log_integrity_error(audit, caplog):
    error = IntegrityError(lineage_id="decision-db-001", kind="zero_active", detail="no active member")
    audit.log_integrity_error(correlation_id="corr-1", actor="chatgpt", integrity_error=error)
    record = _records(caplog)[0]
    assert record["event"] == "INTEGRITY_ERROR"
    assert record["kind"] == "zero_active"


def test_log_inject_computes_bytes_and_token_estimate(audit, caplog):
    resolution = ResolutionResult(resolved=[_artifact()], trace=["step"])
    package = ContextBuilder().build(resolution, resolution_trace_id="corr-1")

    audit.log_inject(correlation_id="corr-1", actor="chatgpt", context_package=package)

    record = _records(caplog)[0]
    assert record["event"] == "INJECT"
    assert record["artifact_ids"] == ["decision-cache-001"]
    expected_bytes = len(package.model_dump_json().encode("utf-8"))
    assert record["context_result_bytes"] == expected_bytes
    assert record["context_tokens_estimate"] == estimate_tokens(package.model_dump_json())
    assert record["measurement_mode"] == "mak4i_context"
    assert record["client_exposed_token_count"] is None


def test_log_inject_can_carry_a_real_client_exposed_token_count(audit, caplog):
    resolution = ResolutionResult(resolved=[_artifact()], trace=["step"])
    package = ContextBuilder().build(resolution, resolution_trace_id="corr-1")

    audit.log_inject(
        correlation_id="corr-1",
        actor="claude.ai",
        context_package=package,
        client_exposed_token_count=87,
    )

    record = _records(caplog)[0]
    assert record["client_exposed_token_count"] == 87


@pytest.mark.parametrize(
    ("text", "expected"),
    [("a" * 4, 1), ("a" * 40, 10), ("a" * 41, 11), ("", 1)],
)
def test_estimate_tokens_heuristic(text, expected):
    assert estimate_tokens(text) == expected


# -- Developer Preview: identity context + auth/authz events -----------------


def test_audit_context_only_emits_set_fields(audit, caplog):
    ctx = AuditContext(principal_id="prn_1", auth_method="credential")
    audit.log("CREATE", correlation_id="c", actor="prn_1", context=ctx, artifact_id="a")
    record = _records(caplog)[0]
    assert record["principal_id"] == "prn_1"
    assert record["auth_method"] == "credential"
    assert "organization_id" not in record
    assert "project_id" not in record


def test_log_create_threads_identity_context(audit, caplog):
    ctx = AuditContext(
        principal_id="prn_1",
        organization_id="org_1",
        project_id="prj_1",
        auth_method="operator_impersonation",
    )
    audit.log_create(
        correlation_id="c", actor="prn_1", artifact=_artifact(), context=ctx
    )
    record = _records(caplog)[0]
    assert record["organization_id"] == "org_1"
    assert record["project_id"] == "prj_1"
    assert record["auth_method"] == "operator_impersonation"


def test_log_authenticate_success_and_failure(audit, caplog):
    audit.log_authenticate(correlation_id="c", success=True, principal_id="prn_1")
    audit.log_authenticate(
        correlation_id="c", success=False, reason="revoked"
    )
    events = [r["event"] for r in _records(caplog)]
    assert events == ["AUTHENTICATE_SUCCESS", "AUTHENTICATE_FAILURE"]
    failure = _records(caplog)[1]
    assert failure["reason"] == "revoked"
    assert failure["actor"] == "unauthenticated"


def test_log_authenticate_never_carries_token_material(audit, caplog):
    audit.log_authenticate(correlation_id="c", success=False, reason="unknown")
    blob = json.dumps(_records(caplog)[0])
    assert "token" not in blob and "hash" not in blob


def test_log_access_granted_and_denied(audit, caplog):
    ctx = AuditContext(principal_id="prn_1", organization_id="org_1", project_id="prj_1")
    audit.log_access_granted(correlation_id="c", context=ctx, permission="read")
    audit.log_access_denied(correlation_id="c", context=ctx, permission="write")
    records = _records(caplog)
    assert records[0]["event"] == "ACCESS_GRANTED"
    assert records[0]["permission"] == "read"
    assert records[1]["event"] == "ACCESS_DENIED"
    assert records[1]["permission"] == "write"
    assert records[1]["project_id"] == "prj_1"


def test_log_read_marker_events(audit, caplog):
    audit.log_read("SEARCH", correlation_id="c", actor="prn_1", project_ids=["prj_1"])
    record = _records(caplog)[0]
    assert record["event"] == "SEARCH"
    assert record["project_ids"] == ["prj_1"]
