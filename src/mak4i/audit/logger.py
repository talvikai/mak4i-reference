from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from mak4i.context import ContextPackage
    from mak4i.models import Artifact
    from mak4i.resolution import Conflict, IntegrityError, ResolutionResult

LOGGER_NAME = "mak4i.audit"

EventType = Literal[
    "CREATE",
    "SUPERSEDE",
    "SUPERSEDE_REJECTED",
    "DISCOVER",
    "RESOLVE",
    "CONFLICT",
    "INTEGRITY_ERROR",
    "INJECT",
    "RESPONSE",
    "AUTHENTICATE_SUCCESS",
    "AUTHENTICATE_FAILURE",
    "ACCESS_GRANTED",
    "ACCESS_DENIED",
    "GET_CURRENT",
    "SEARCH",
    "HISTORY",
    # OAuth authorization service (MAK-0008); never carry a secret.
    "OAUTH_SIGN_IN_CODE_ISSUED",
    "OAUTH_SIGN_IN",
    "OAUTH_AUTHORIZATION_GRANTED",
    "OAUTH_AUTHORIZATION_DENIED",
    "OAUTH_TOKEN_ISSUED",
    "OAUTH_TOKEN_REJECTED",
    "OAUTH_CODE_REUSE",
    "OAUTH_REFRESH_REPLAY",
    "OAUTH_REVOKED",
    "OAUTH_CLIENT_REGISTERED",
    "OAUTH_CLIENT_DISABLED",
]


@dataclass(frozen=True)
class AuditContext:
    """The authenticated-identity fields threaded onto every audit record
    for a Developer Preview operation (MVP spec §6). Built once per engine
    call and passed to every helper, so `principal_id` / `organization_id`
    / `project_id` / `auth_method` appear consistently.

    `auth_method` distinguishes `"credential"` (a real bearer token
    authenticated on the hosted path) from `"operator_impersonation"` (the
    trusted local CLI acting as a principal by id) — the two must never be
    confused in the trail.
    """

    principal_id: str | None = None
    organization_id: str | None = None
    project_id: str | None = None
    auth_method: str | None = None

    def fields(self) -> dict[str, str]:
        pairs = {
            "principal_id": self.principal_id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "auth_method": self.auth_method,
        }
        return {k: v for k, v in pairs.items() if v is not None}


def estimate_tokens(text: str) -> int:
    """Rough ~4-characters-per-token heuristic.

    Used only as a fallback when the calling client doesn't expose a real
    token count (requirements §19: "an estimate where it does not" —
    `client_exposed_token_count` staying None on the same event is what
    marks this as an estimate rather than a measured number).
    """
    return max(1, math.ceil(len(text) / 4))


class AuditLogger:
    """Emits one structured JSON log line per event via Python's logging
    module.

    [MVP CHOICE]: MVP_ARCHITECTURE.md §21 treats the Cloud Run service's
    own logs (Cloud Logging) as the authoritative record of what was
    actually called — "more durable than relying on a chat transcript
    alone." No separate persistent audit store is introduced; standard
    logging is the one seam, and it comes with Cloud Logging capture for
    free once this runs on Cloud Run (M8).
    """

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger(LOGGER_NAME)

    def log(
        self,
        event: EventType,
        *,
        correlation_id: str,
        actor: str,
        context: AuditContext | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        record = {
            "ts": _utc_now_iso(),
            "event": event,
            "correlation_id": correlation_id,
            "actor": actor,
        }
        if context is not None:
            record.update(context.fields())
        record.update(fields)
        self._logger.info(json.dumps(record, default=str))
        return record

    # -- authentication / authorization (Developer Preview) --------------

    def log_authenticate(
        self,
        *,
        correlation_id: str,
        success: bool,
        principal_id: str | None = None,
        reason: str | None = None,
        auth_method: str = "credential",
    ) -> dict[str, Any]:
        """Never receives or logs the raw token or its hash — only the
        outcome, the resolved `principal_id` on success, and an internal
        `reason` string on failure (`unknown` / `revoked` / `expired` / …).
        """
        extra = {"reason": reason} if reason is not None else {}
        return self.log(
            "AUTHENTICATE_SUCCESS" if success else "AUTHENTICATE_FAILURE",
            correlation_id=correlation_id,
            actor=principal_id or "unauthenticated",
            context=AuditContext(principal_id=principal_id, auth_method=auth_method),
            **extra,
        )

    def log_access_granted(
        self, *, correlation_id: str, context: AuditContext, permission: str
    ) -> dict[str, Any]:
        return self.log(
            "ACCESS_GRANTED",
            correlation_id=correlation_id,
            actor=context.principal_id or "unknown",
            context=context,
            permission=permission,
        )

    def log_access_denied(
        self, *, correlation_id: str, context: AuditContext, permission: str
    ) -> dict[str, Any]:
        return self.log(
            "ACCESS_DENIED",
            correlation_id=correlation_id,
            actor=context.principal_id or "unknown",
            context=context,
            permission=permission,
        )

    # -- artifact lifecycle ---------------------------------------------------

    def log_create(
        self,
        *,
        correlation_id: str,
        actor: str,
        artifact: "Artifact",
        context: AuditContext | None = None,
    ) -> dict[str, Any]:
        return self.log(
            "CREATE",
            correlation_id=correlation_id,
            actor=actor,
            context=context,
            artifact_id=artifact.artifact_id,
            lineage_id=artifact.lineage_id,
            version=artifact.version,
        )

    def log_supersede(
        self,
        *,
        correlation_id: str,
        actor: str,
        old_id: str,
        new_artifact: "Artifact",
        reason: str,
        context: AuditContext | None = None,
    ) -> dict[str, Any]:
        return self.log(
            "SUPERSEDE",
            correlation_id=correlation_id,
            actor=actor,
            context=context,
            old_id=old_id,
            new_id=new_artifact.artifact_id,
            lineage_id=new_artifact.lineage_id,
            version=new_artifact.version,
            reason=reason,
            subject_key=new_artifact.subject_key,
            released_subject_key=new_artifact.released_subject_key,
        )

    def log_supersede_rejected(
        self,
        *,
        correlation_id: str,
        actor: str,
        old_id: str,
        reason: str,
        context: AuditContext | None = None,
    ) -> dict[str, Any]:
        return self.log(
            "SUPERSEDE_REJECTED",
            correlation_id=correlation_id,
            actor=actor,
            context=context,
            old_id=old_id,
            reason=reason,
        )

    def log_discover(
        self,
        *,
        correlation_id: str,
        actor: str,
        project: str,
        artifact_type: str | None,
        tags: list[str] | None,
        candidate_ids: list[str],
        context: AuditContext | None = None,
    ) -> dict[str, Any]:
        return self.log(
            "DISCOVER",
            correlation_id=correlation_id,
            actor=actor,
            context=context,
            project=project,
            artifact_type=artifact_type,
            tags=tags,
            candidate_ids=candidate_ids,
        )

    def log_resolve(
        self,
        *,
        correlation_id: str,
        actor: str,
        resolution: "ResolutionResult",
        context: AuditContext | None = None,
    ) -> dict[str, Any]:
        return self.log(
            "RESOLVE",
            correlation_id=correlation_id,
            actor=actor,
            context=context,
            resolved_ids=[a.artifact_id for a in resolution.resolved],
            conflict_count=len(resolution.conflicts),
            integrity_error_count=len(resolution.integrity_errors),
            trace=resolution.trace,
        )

    def log_conflict(
        self,
        *,
        correlation_id: str,
        actor: str,
        conflict: "Conflict",
        context: AuditContext | None = None,
    ) -> dict[str, Any]:
        return self.log(
            "CONFLICT",
            correlation_id=correlation_id,
            actor=actor,
            context=context,
            artifact_type=conflict.artifact_type,
            subject_key=conflict.subject_key,
            lineage_ids=conflict.lineage_ids,
            candidates=[a.artifact_id for a in conflict.artifacts],
        )

    def log_integrity_error(
        self,
        *,
        correlation_id: str,
        actor: str,
        integrity_error: "IntegrityError",
        context: AuditContext | None = None,
    ) -> dict[str, Any]:
        return self.log(
            "INTEGRITY_ERROR",
            correlation_id=correlation_id,
            actor=actor,
            context=context,
            lineage_id=integrity_error.lineage_id,
            kind=integrity_error.kind,
            detail=integrity_error.detail,
        )

    def log_inject(
        self,
        *,
        correlation_id: str,
        actor: str,
        context_package: "ContextPackage",
        measurement_mode: str = "mak4i_context",
        client_exposed_token_count: int | None = None,
        context: AuditContext | None = None,
    ) -> dict[str, Any]:
        serialized = context_package.model_dump_json()
        return self.log(
            "INJECT",
            correlation_id=correlation_id,
            actor=actor,
            context=context,
            artifact_ids=[a.artifact_id for a in context_package.artifacts],
            context_result_bytes=len(serialized.encode("utf-8")),
            context_tokens_estimate=estimate_tokens(serialized),
            measurement_mode=measurement_mode,
            client_exposed_token_count=client_exposed_token_count,
        )

    def log_read(
        self,
        event: Literal["GET_CURRENT", "SEARCH", "HISTORY"],
        *,
        correlation_id: str,
        actor: str,
        context: AuditContext | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """The read-operation marker events new in the Developer Preview
        (MVP spec §6). DISCOVER/RESOLVE/INJECT still fire underneath for
        `get_current`; this names the operation the client actually asked
        for."""
        return self.log(
            event, correlation_id=correlation_id, actor=actor, context=context, **fields
        )

    def log_response(
        self, *, correlation_id: str, actor: str, summary: str, **extra: Any
    ) -> dict[str, Any]:
        return self.log("RESPONSE", correlation_id=correlation_id, actor=actor, summary=summary, **extra)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
