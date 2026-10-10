from __future__ import annotations

import re
import uuid

from mak4i.audit import AuditContext, AuditLogger
from mak4i.context import ContextBuilder, ContextPackage
from mak4i.discovery import Discovery
from mak4i.identity import AuthorizedProject, ControlPlane, Organization, Principal
from mak4i.identity.auth_context import AuthContext
from mak4i.identity.authz import Authorizer, apply_ceiling
from mak4i.models import (
    Artifact,
    ClientMetadata,
    Provenance,
    bump_version,
    normalize_subject_key,
    utc_now,
)
from mak4i.errors import (
    ConflictChangedError,
    ConflictNotOpenError,
    MAK4IError,
    NotFoundError,
    SubjectCollisionError,
    SubjectInConflictError,
    ValidationFailedError,
)
from mak4i.resolution import IntegrityError, Resolver
from mak4i.resolution.records import InMemoryResolutionStore, ResolutionStore, SlotTakenError
from mak4i.resolution.types import (
    Conflict,
    ConflictCandidate,
    DetectedConflict,
    ResolutionEffect,
    ResolutionRecord,
    ResultingHead,
    conflict_id_for,
)
from mak4i.store.base import ArtifactNotFoundError, ArtifactStore, ConcurrentModificationError

_MAX_SUCCESSOR_ID_ATTEMPTS = 1000

CREDENTIAL = "credential"
OPERATOR_IMPERSONATION = "operator_impersonation"


class ArtifactNotActiveError(MAK4IError):
    """Raised by supersede_artifact when the target isn't currently active.

    A supersede must never be applied to an already-superseded artifact —
    MVP_ARCHITECTURE.md §5 step 2: "Confirm old.status == active. If not,
    reject." Nothing is written when this is raised.
    """

    code = "artifact_not_active"

    def __init__(self, artifact_id: str, status: str):
        super().__init__(
            f"cannot supersede {artifact_id!r}: status is {status!r}, not 'active'"
        )
        self.artifact_id = artifact_id
        self.status = status


class SubjectKeyChangeError(MAK4IError):
    """Raised by supersede_artifact when a supersede would alter the
    lineage's `subject_key` other than by an explicit release: supplying a
    different key, acquiring a key for a lineage that has none (including
    one that previously released its key), or an ambiguous/no-op release.

    `subject_key` is stable within a lineage: a supersede may omit it
    (carried forward) or restate it, and may give it up only via
    `release_subject_key=True`. Nothing is written when this is raised.
    """

    code = "subject_key_change"

    def __init__(
        self,
        artifact_id: str,
        current: str | None,
        requested: str | None,
        *,
        detail: str | None = None,
    ):
        super().__init__(
            f"cannot supersede {artifact_id!r}: "
            + (detail or f"subject_key {current!r} cannot be changed to {requested!r}")
            + " — subject_key is stable within a lineage; use "
            "release_subject_key=true to give it up, or create a new lineage"
        )
        self.artifact_id = artifact_id
        self.current = current
        self.requested = requested


class StaleHeadError(ConcurrentModificationError):
    """MAK-0005 §A4.2: another write replaced the head between read and
    write; nothing was written. Re-read and retry. (A
    `ConcurrentModificationError`, so existing callers keep working.)"""

    code = "stale_head"
    retryable = True

    def __init__(self, artifact_id: str):
        super().__init__(artifact_id)
        self.message = (
            f"{artifact_id!r} changed while this supersede was in progress; nothing was "
            "written — re-read the lineage and retry"
        )

    def __str__(self) -> str:
        return self.message


class MAK4IEngine:
    """The one library surface — callable identically by tests, the CLI,
    and the MCP server (requirements §9). Depends on the ArtifactStore
    protocol plus a Developer Preview `Authorizer` / `ControlPlane`: every
    operation resolves an authenticated `Principal` to an authorization
    decision *before* touching artifacts (spec §3), and authoritative
    provenance (`created_by`, audit `actor`) always comes from that
    principal — never from a caller-supplied value.

    `auth_method` is threaded through so the audit trail distinguishes a
    real credential (`"credential"`, the hosted MCP path) from the trusted
    operator CLI acting as a principal by id (`"operator_impersonation"`).
    """

    def __init__(
        self,
        store: ArtifactStore,
        audit: AuditLogger | None = None,
        *,
        authorizer: Authorizer,
        control_plane: ControlPlane,
        resolutions: ResolutionStore | None = None,
    ):
        self._resolutions = resolutions or InMemoryResolutionStore()
        self._store = store
        self._audit = audit
        self._authorizer = authorizer
        self._control_plane = control_plane
        self._discovery = Discovery(store)
        self._resolver = Resolver()
        self._context_builder = ContextBuilder()

    # -- writes ------------------------------------------------------------

    def create_artifact(
        self,
        *,
        principal: Principal,
        project: str,
        artifact_id: str,
        artifact_type: str,
        title: str,
        content: str,
        rationale: str | None = None,
        tags: list[str] | None = None,
        subject_key: str | None = None,
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
        client: ClientMetadata | None = None,
    ) -> Artifact:
        """Create a brand-new lineage in `project`. The principal must hold
        WRITE on it or `AccessDeniedError` is raised (and audited) before
        anything is written. `created_by` is the authenticated principal;
        any value the caller might have supplied is irrelevant."""
        auth_method, ceiling = _resolve_auth(auth, auth_method)
        correlation_id = str(uuid.uuid4())
        outcome = self._authorizer.require(
            principal, project, "write",
            correlation_id=correlation_id, auth_method=auth_method,
            ceiling=ceiling,
        )
        context = self._authorizer.audit_context(outcome, auth_method=auth_method)
        subject_key = normalize_subject_key(subject_key)  # ValueError if blank

        now = utc_now()
        artifact = Artifact(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            organization_id=outcome.organization_id,
            project=project,
            title=title,
            content=content,
            rationale=rationale,
            status="active",
            version="1.0",
            created_by=principal.principal_id,
            created_at=now,
            updated_at=now,
            lineage_id=artifact_id,
            supersedes=None,
            superseded_by=None,
            tags=tags or [],
            subject_key=subject_key,
            provenance=_provenance(principal, auth, auth_method, client, now),
        )
        self._store.put_new(artifact)
        if self._audit is not None:
            self._audit.log_create(
                correlation_id=correlation_id,
                actor=principal.principal_id,
                artifact=artifact,
                context=context,
            )
        return artifact

    def supersede_artifact(
        self,
        *,
        principal: Principal,
        project: str,
        old_id: str,
        content: str,
        reason: str,
        title: str | None = None,
        tags: list[str] | None = None,
        subject_key: str | None = None,
        release_subject_key: bool = False,
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
        client: ClientMetadata | None = None,
    ) -> Artifact:
        """Corrected write sequence per MVP_ARCHITECTURE.md §5, now
        project-scoped. WRITE on `project` is required. `old_id` is looked
        up *within* `(organization_id, project)`, so an artifact in another
        project can never be superseded through this call (it is simply not
        found) — see tests/test_security_dev_preview.py."""
        auth_method, ceiling = _resolve_auth(auth, auth_method)
        correlation_id = str(uuid.uuid4())
        outcome = self._authorizer.require(
            principal, project, "write",
            correlation_id=correlation_id, auth_method=auth_method,
            ceiling=ceiling,
        )
        context = self._authorizer.audit_context(outcome, auth_method=auth_method)
        organization_id = outcome.organization_id

        record = self._store.get(organization_id, project, old_id)
        if record is None:
            raise ArtifactNotFoundError(old_id)
        old, old_token = record

        if old.status != "active":
            if self._audit is not None:
                self._audit.log_supersede_rejected(
                    correlation_id=correlation_id,
                    actor=principal.principal_id,
                    old_id=old_id,
                    reason=f"old status is {old.status!r}, not 'active'",
                    context=context,
                )
            raise ArtifactNotActiveError(old.artifact_id, old.status)

        # subject_key is stable within a lineage (MVP_ARCHITECTURE.md §18):
        #   omitted            → carried forward unchanged
        #   restated, same     → carried forward unchanged
        #   release requested  → new version gives up the key (explicit only)
        #   anything else      → rejected, nothing written
        requested_subject_key = normalize_subject_key(subject_key)
        rejection: str | None = None
        if release_subject_key:
            if requested_subject_key is not None:
                rejection = "release_subject_key cannot be combined with a subject_key"
            elif old.subject_key is None:
                rejection = "release_subject_key requested but this lineage has no subject_key"
        elif requested_subject_key is not None and requested_subject_key != old.subject_key:
            rejection = (
                f"subject_key change {old.subject_key!r} -> {requested_subject_key!r} "
                "is not allowed within a lineage"
            )
        if rejection is not None:
            if self._audit is not None:
                self._audit.log_supersede_rejected(
                    correlation_id=correlation_id,
                    actor=principal.principal_id,
                    old_id=old_id,
                    reason=rejection,
                    context=context,
                )
            raise SubjectKeyChangeError(
                old.artifact_id,
                old.subject_key,
                None if release_subject_key else requested_subject_key,
                detail=rejection,
            )
        new_subject_key = None if release_subject_key else old.subject_key
        released_subject_key = old.subject_key if release_subject_key else None

        if old.subject_key is not None:
            subject = (old.artifact_type, old.subject_key)
            if subject in self._resolving_subjects(organization_id, project):
                # MAK-0004 §A8.3: don't move a candidate under a resolution.
                self._reject_supersede(correlation_id, principal, old_id, context, "a resolution of this subject is in progress")
                raise ConflictChangedError(
                    "a resolution of this subject is in progress; nothing was written — re-read and retry"
                )
            if release_subject_key and self._subject_in_conflict(organization_id, project, subject):
                self._reject_supersede(correlation_id, principal, old_id, context, "release during open conflict")
                raise SubjectInConflictError(
                    f"subject_key {old.subject_key!r} is in an open conflict; resolve it with "
                    "mak4i_resolve_conflict (select a winner, merge, or separate the subjects) "
                    "instead of releasing the key"
                )

        new_id = self._next_available_id(organization_id, project, old_id)
        now = utc_now()

        old_marked_superseded = old.model_copy(
            update={"status": "superseded", "superseded_by": new_id, "updated_at": now}
        )
        # Step 4: the GCS-native race-closing check — if `old` changed since
        # we read it, reject the whole operation here and write nothing else
        # (MAK-0005 §A4.2), and record the rejection (§A4.4).
        try:
            self._store.put_if_match(old_marked_superseded, expected_version_token=old_token)
        except ConcurrentModificationError as exc:
            self._reject_supersede(correlation_id, principal, old_id, context, "stale head")
            raise StaleHeadError(old_id) from exc

        new_artifact = Artifact(
            artifact_id=new_id,
            artifact_type=old.artifact_type,
            organization_id=old.organization_id,
            project=old.project,
            title=title if title is not None else old.title,
            content=content,
            rationale=reason,
            status="active",
            version=bump_version(old.version),
            created_by=principal.principal_id,
            created_at=now,
            updated_at=now,
            lineage_id=old.lineage_id,
            supersedes=old_id,
            superseded_by=None,
            tags=tags if tags is not None else old.tags,
            subject_key=new_subject_key,
            released_subject_key=released_subject_key,
            provenance=_provenance(principal, auth, auth_method, client, now),
        )
        # Step 5: on the (rare, racy) chance this id was claimed between
        # `_next_available_id`'s check and here, this raises
        # ArtifactAlreadyExistsError and propagates — the lineage is left
        # with zero active members, the "honest failure mode" §5 accepts.
        self._store.put_new(new_artifact)
        if self._audit is not None:
            self._audit.log_supersede(
                correlation_id=correlation_id,
                actor=principal.principal_id,
                old_id=old_id,
                new_artifact=new_artifact,
                reason=reason,
                context=context,
            )
        return new_artifact

    # -- reads -----------------------------------------------------------

    def get_current(
        self,
        *,
        principal: Principal,
        project: str,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
    ) -> ContextPackage:
        """Read flow per MVP_ARCHITECTURE.md §2/§8, gated by READ on
        `project`. One correlation_id ties GET_CURRENT, DISCOVER, RESOLVE,
        INJECT (plus a CONFLICT/INTEGRITY_ERROR per anomaly) together."""
        auth_method, ceiling = _resolve_auth(auth, auth_method)
        correlation_id = str(uuid.uuid4())
        outcome = self._authorizer.require(
            principal, project, "read",
            correlation_id=correlation_id, auth_method=auth_method,
            ceiling=ceiling,
        )
        context = self._authorizer.audit_context(outcome, auth_method=auth_method)
        organization_id = outcome.organization_id

        if self._audit is not None:
            self._audit.log_read(
                "GET_CURRENT",
                correlation_id=correlation_id,
                actor=principal.principal_id,
                context=context,
                project=project,
                artifact_type=artifact_type,
                tags=tags,
            )

        candidates = self._discovery.find_candidates(
            organization_id, project, artifact_type=artifact_type, tags=tags, status=None
        )
        if self._audit is not None:
            self._audit.log_discover(
                correlation_id=correlation_id,
                actor=principal.principal_id,
                project=project,
                artifact_type=artifact_type,
                tags=tags,
                candidate_ids=[c.artifact_id for c in candidates],
                context=context,
            )

        all_artifacts = self._store.all(organization_id, project)
        pending = self._pending_by_subject(organization_id, project)
        resolution = self._resolver.resolve(
            all_artifacts, artifact_type=artifact_type, tags=tags, resolving=set(pending)
        )
        conflicts = [
            self._to_conflict(organization_id, project, detected, pending)
            for detected in resolution.conflicts
        ]
        if self._audit is not None:
            self._audit.log_resolve(
                correlation_id=correlation_id,
                actor=principal.principal_id,
                resolution=resolution,
                context=context,
            )
            for conflict in conflicts:
                self._audit.log_conflict(
                    correlation_id=correlation_id,
                    actor=principal.principal_id,
                    conflict=conflict,
                    context=context,
                )
            for integrity_error in resolution.integrity_errors:
                self._audit.log_integrity_error(
                    correlation_id=correlation_id,
                    actor=principal.principal_id,
                    integrity_error=integrity_error,
                    context=context,
                )

        package = self._context_builder.build(
            resolution, resolution_trace_id=correlation_id, conflicts=conflicts
        )
        if self._audit is not None:
            self._audit.log_inject(
                correlation_id=correlation_id,
                actor=principal.principal_id,
                context_package=package,
                context=context,
            )
        return package

    def search(
        self,
        *,
        principal: Principal,
        project: str,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
    ) -> list[Artifact]:
        """Raw deterministic candidate lookup in one project. READ required."""
        return self.search_authorized(
            principal=principal,
            project=project,
            artifact_type=artifact_type,
            tags=tags,
            status=status,
            auth_method=auth_method,
            auth=auth,
        )

    def search_authorized(
        self,
        *,
        principal: Principal,
        project: str | None = None,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
    ) -> list[Artifact]:
        """Deterministic candidate lookup across projects.

        With `project` set: READ on that one project is required (denied
        loudly otherwise). With `project` omitted: search every project the
        principal holds a READ grant on — projects it cannot see contribute
        nothing and are never named in an error, so no metadata leaks
        (spec §3, tests 7/8).
        """
        auth_method, ceiling = _resolve_auth(auth, auth_method)
        correlation_id = str(uuid.uuid4())

        if project is not None:
            outcome = self._authorizer.require(
                principal, project, "read",
                correlation_id=correlation_id, auth_method=auth_method,
                ceiling=ceiling,
            )
            scopes = [(outcome.organization_id, project)]
            context = self._authorizer.audit_context(outcome, auth_method=auth_method)
        else:
            authorized = [
                ap
                for ap in self._control_plane.list_authorized_projects(principal, "read")
                if "read" in apply_ceiling(ap.permissions, ceiling)
            ]
            scopes = [
                (ap.project.organization_id, ap.project.project_id) for ap in authorized
            ]
            context = AuditContext(
                principal_id=principal.principal_id, auth_method=auth_method
            )

        results = self._store.query_many(
            scopes, artifact_type=artifact_type, tags=tags, status=status
        )
        if self._audit is not None:
            self._audit.log_read(
                "SEARCH",
                correlation_id=correlation_id,
                actor=principal.principal_id,
                context=context,
                project_ids=[p for _org, p in scopes],
                artifact_type=artifact_type,
                tags=tags,
                candidate_ids=[a.artifact_id for a in results],
            )
        return results

    def get_history(
        self,
        *,
        principal: Principal,
        project: str,
        lineage_id: str,
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
    ) -> list[Artifact]:
        """Every member of a lineage within `project`, oldest first by
        version — the explicit tool for history/migration/rationale
        queries (§8). READ required."""
        auth_method, ceiling = _resolve_auth(auth, auth_method)
        correlation_id = str(uuid.uuid4())
        outcome = self._authorizer.require(
            principal, project, "read",
            correlation_id=correlation_id, auth_method=auth_method,
            ceiling=ceiling,
        )
        context = self._authorizer.audit_context(outcome, auth_method=auth_method)

        all_artifacts = self._store.all(outcome.organization_id, project)
        members = sorted(
            (a for a in all_artifacts if a.lineage_id == lineage_id),
            key=lambda a: _version_sort_key(a.version),
        )
        if self._audit is not None:
            self._audit.log_read(
                "HISTORY",
                correlation_id=correlation_id,
                actor=principal.principal_id,
                context=context,
                project=project,
                lineage_id=lineage_id,
                member_ids=[m.artifact_id for m in members],
            )
        return members

    def list_projects(
        self, *, principal: Principal, auth: AuthContext | None = None
    ) -> list[AuthorizedProject]:
        """Every project the authenticated principal may act on, with its
        *effective* permissions (grant ∩ ceiling, MAK-0006 §5.3). Projects
        outside the principal's grants — or left with no permission after
        the ceiling — are simply absent (spec §3, test 6)."""
        ceiling = auth.ceiling if auth is not None else None
        out = []
        for ap in self._control_plane.list_authorized_projects(principal):
            permissions = apply_ceiling(ap.permissions, ceiling)
            if permissions:
                out.append(ap.model_copy(update={"permissions": permissions}))
        return out

    def organization_of(self, *, principal: Principal) -> Organization | None:
        """The organization the authenticated principal belongs to — part of
        the identity a client shows a user before acting on a connection
        (issues #8/#9). Only the caller's own organization is ever returned."""
        return self._control_plane.get_organization(principal.organization_id)

    def check_integrity(
        self, *, organization_id: str, project: str
    ) -> list[IntegrityError]:
        """`mak4i doctor` — groups by lineage_id and flags zero/multiple
        active members and dangling pointers (§5). CLI-only, never
        model-callable."""
        all_artifacts = self._store.all(organization_id, project)
        result = self._resolver.resolve(all_artifacts)
        return result.integrity_errors

    # -- conflicts (MAK-0004 Part A) ------------------------------------------

    def list_conflicts(
        self,
        *,
        principal: Principal,
        project: str,
        artifact_type: str | None = None,
        state: str = "open",
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
    ) -> list[Conflict]:
        """Open (incl. resolving) conflicts, resolved ones from their
        records, or both. READ required."""
        if state not in ("open", "resolved", "all"):
            raise ValidationFailedError("state must be 'open', 'resolved' or 'all'")
        auth_method, ceiling = _resolve_auth(auth, auth_method)
        correlation_id = str(uuid.uuid4())
        outcome = self._authorizer.require(
            principal, project, "read",
            correlation_id=correlation_id, auth_method=auth_method,
            ceiling=ceiling,
        )
        organization_id = outcome.organization_id
        out: list[Conflict] = []
        if state in ("open", "all"):
            out.extend(self._open_conflicts(organization_id, project).values())
        if state in ("resolved", "all"):
            out.extend(
                self._resolved_view(record)
                for record in self._resolutions.for_project(organization_id, project, state="completed")
            )
        if artifact_type is not None:
            out = [c for c in out if c.artifact_type == artifact_type]
        return out

    def get_conflict(
        self,
        *,
        principal: Principal,
        project: str,
        conflict_id: str,
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
    ) -> Conflict:
        auth_method, ceiling = _resolve_auth(auth, auth_method)
        correlation_id = str(uuid.uuid4())
        outcome = self._authorizer.require(
            principal, project, "read",
            correlation_id=correlation_id, auth_method=auth_method,
            ceiling=ceiling,
        )
        organization_id = outcome.organization_id
        open_conflicts = self._open_conflicts(organization_id, project)
        if conflict_id in open_conflicts:
            return open_conflicts[conflict_id]
        for record in self._resolutions.by_conflict(conflict_id):
            if (record.organization_id, record.project) == (organization_id, project) and record.state == "completed":
                return self._resolved_view(record)
        raise NotFoundError("no conflict with this id in this project")

    def resolve_conflict(
        self,
        *,
        principal: Principal,
        project: str,
        conflict_id: str,
        candidate_artifact_ids: list[str],
        action: str,
        reason: str,
        winner_artifact_id: str | None = None,
        into_artifact_id: str | None = None,
        content: str | None = None,
        title: str | None = None,
        rationale: str | None = None,
        assignments: list[dict] | None = None,
        idempotency_key: str | None = None,
        auth_method: str = CREDENTIAL,
        auth: AuthContext | None = None,
        client: ClientMetadata | None = None,
    ) -> ResolutionRecord:
        """MAK-0004 §A7–§A8. Requires `read` and `resolve` (any principal
        type). Fails with `conflict_changed` if the candidates differ from
        `candidate_artifact_ids`, `conflict_not_open` if another resolution
        of this generation exists, and is idempotent for an identical retry
        with the same `idempotency_key`."""
        auth_method, ceiling = _resolve_auth(auth, auth_method)
        correlation_id = str(uuid.uuid4())
        outcome = self._authorizer.require(
            principal, project, "read",
            correlation_id=correlation_id, auth_method=auth_method,
            ceiling=ceiling,
        )
        self._authorizer.require(
            principal, project, "resolve",
            correlation_id=correlation_id, auth_method=auth_method,
            ceiling=ceiling,
        )
        context = self._authorizer.audit_context(outcome, auth_method=auth_method)
        organization_id = outcome.organization_id
        if not (reason or "").strip():
            raise ValidationFailedError("reason is required: a resolution without a reason is not explainable")
        parameters = _resolution_parameters(
            action,
            winner_artifact_id=winner_artifact_id,
            into_artifact_id=into_artifact_id,
            content=content,
            title=title,
            rationale=rationale,
            assignments=assignments,
        )

        # §A8.4: an identical retry returns (or finishes) the existing record.
        for record in self._resolutions.by_conflict(conflict_id):
            if (record.organization_id, record.project) != (organization_id, project) or record.state == "aborted":
                continue
            same_request = (
                idempotency_key is not None
                and record.idempotency_key == idempotency_key
                and record.action == action
                and record.parameters == parameters
                and sorted(record.candidate_artifact_ids) == sorted(candidate_artifact_ids)
            )
            if not same_request:
                self._log_resolution("RESOLUTION_REJECTED", correlation_id, principal, context, conflict_id=conflict_id, reason="conflict_not_open")
                raise ConflictNotOpenError("this conflict was already resolved or is being resolved")
            return self._apply_resolution(record, correlation_id, context) if record.state == "pending" else record

        open_conflicts = self._open_conflicts(organization_id, project)
        conflict = open_conflicts.get(conflict_id)
        if conflict is None:
            raise NotFoundError("no open conflict with this id in this project")
        current = sorted(c.artifact_id for c in conflict.candidates)
        if sorted(set(candidate_artifact_ids)) != current or len(candidate_artifact_ids) != len(current):
            self._log_resolution("RESOLUTION_REJECTED", correlation_id, principal, context, conflict_id=conflict_id, reason="conflict_changed")
            raise ConflictChangedError(
                "the conflict's candidates changed since they were read; re-read the conflict and retry"
            )
        self._validate_resolution(organization_id, project, conflict, action, parameters)

        now = utc_now()
        record = ResolutionRecord(
            resolution_id="res_" + uuid.uuid4().hex,
            conflict_id=conflict_id,
            organization_id=organization_id,
            project=project,
            artifact_type=conflict.artifact_type,
            subject_key=conflict.subject_key,
            generation=conflict.generation,
            action=action,
            parameters=parameters,
            reason=reason.strip(),
            candidate_artifact_ids=current,
            actor=_provenance(principal, auth, auth_method, client, now),
            state="pending",
            idempotency_key=idempotency_key,
            created_at=now,
        )
        try:
            self._resolutions.create_pending(record)
        except SlotTakenError as exc:
            self._log_resolution("RESOLUTION_REJECTED", correlation_id, principal, context, conflict_id=conflict_id, reason="conflict_not_open")
            raise ConflictNotOpenError("this conflict is already being resolved") from exc
        self._log_resolution(
            "RESOLUTION_STARTED", correlation_id, principal, context,
            resolution_id=record.resolution_id, conflict_id=conflict_id, action=action,
            candidates=current,
        )
        return self._apply_resolution(record, correlation_id, context)

    def recover_pending_resolutions(self) -> list[ResolutionRecord]:
        """MAK-0004 §A8.2: drive every pending resolution to completed (or
        aborted). Called at server start; safe to call any time."""
        finished = []
        for record in self._resolutions.all_pending():
            try:
                finished.append(self._apply_resolution(record, str(uuid.uuid4()), None))
            except ConflictChangedError:
                finished.append(self._resolutions.get(record.resolution_id))
        return finished

    # -- conflict internals ------------------------------------------------------

    def _apply_resolution(self, record: ResolutionRecord, correlation_id: str, context) -> ResolutionRecord:
        org, project = record.organization_id, record.project
        params = record.parameters
        candidates = sorted(record.candidate_artifact_ids)
        effects: list[ResolutionEffect] = []
        heads: list[ResultingHead] = []
        try:
            if record.action == "select_winner":
                winner = params["winner_artifact_id"]
                for artifact_id in candidates:
                    if artifact_id == winner:
                        effects.append(ResolutionEffect(artifact_id=artifact_id, effect="unchanged"))
                    else:
                        self._withdraw(org, project, artifact_id, record)
                        effects.append(ResolutionEffect(artifact_id=artifact_id, effect="withdrawn"))
                heads.append(ResultingHead(subject_key=record.subject_key, artifact_id=winner))
            elif record.action == "merge":
                into = params["into_artifact_id"]
                successor = self._resolution_successor(
                    org, project, into, record,
                    content=params["content"],
                    title=params.get("title"),
                    rationale=params.get("rationale") or record.reason,
                    subject_key=record.subject_key,
                    merged_from=candidates,
                )
                effects.append(ResolutionEffect(artifact_id=into, effect="superseded", successor_artifact_id=successor))
                for artifact_id in candidates:
                    if artifact_id != into:
                        self._withdraw(org, project, artifact_id, record)
                        effects.append(ResolutionEffect(artifact_id=artifact_id, effect="withdrawn"))
                heads.append(ResultingHead(subject_key=record.subject_key, artifact_id=successor))
            else:  # separate_subjects
                for assignment in sorted(params["assignments"], key=lambda a: a["artifact_id"]):
                    artifact_id, key = assignment["artifact_id"], assignment["subject_key"]
                    if key == record.subject_key:
                        effects.append(ResolutionEffect(artifact_id=artifact_id, effect="unchanged"))
                        heads.append(ResultingHead(subject_key=key, artifact_id=artifact_id))
                    else:
                        successor = self._resolution_successor(
                            org, project, artifact_id, record,
                            content=None, title=None, rationale=record.reason, subject_key=key,
                            merged_from=None,
                        )
                        effects.append(ResolutionEffect(artifact_id=artifact_id, effect="superseded", successor_artifact_id=successor))
                        heads.append(ResultingHead(subject_key=key, artifact_id=successor))
        except (ConflictChangedError, ArtifactNotFoundError) as exc:
            aborted = record.model_copy(update={"state": "aborted", "effects": effects, "completed_at": utc_now()})
            self._resolutions.update(aborted)
            self._log_resolution(
                "RESOLUTION_ABORTED", correlation_id, None, context,
                resolution_id=record.resolution_id, conflict_id=record.conflict_id,
                applied_effects=[e.model_dump() for e in effects],
            )
            raise ConflictChangedError(
                "a candidate changed while the resolution was being applied; it was aborted "
                "(effects already applied are recorded) — re-read the conflict and retry"
            ) from exc
        completed = record.model_copy(
            update={"state": "completed", "effects": effects, "resulting_heads": heads, "completed_at": utc_now()}
        )
        self._resolutions.update(completed)
        self._log_resolution(
            "RESOLUTION_COMPLETED", correlation_id, None, context,
            resolution_id=record.resolution_id, conflict_id=record.conflict_id,
            action=record.action, effects=[e.model_dump() for e in effects],
            resulting_heads=[h.model_dump() for h in heads], actor_principal_id=record.actor.principal_id,
        )
        return completed

    def _withdraw(self, org: str, project: str, artifact_id: str, record: ResolutionRecord) -> None:
        found = self._store.get(org, project, artifact_id)
        if found is None:
            raise ArtifactNotFoundError(artifact_id)
        artifact, token = found
        if artifact.status == "withdrawn" and artifact.resolution_id == record.resolution_id:
            return  # already applied (retry / recovery)
        if artifact.status != "active":
            raise ConflictChangedError(f"{artifact_id!r} is no longer a current head")
        withdrawn = artifact.model_copy(
            update={"status": "withdrawn", "resolution_id": record.resolution_id, "updated_at": utc_now()}
        )
        try:
            self._store.put_if_match(withdrawn, expected_version_token=token)
        except ConcurrentModificationError as exc:
            raise ConflictChangedError(f"{artifact_id!r} changed during the resolution") from exc

    def _resolution_successor(
        self,
        org: str,
        project: str,
        head_id: str,
        record: ResolutionRecord,
        *,
        content: str | None,
        title: str | None,
        rationale: str,
        subject_key: str,
        merged_from: list[str] | None,
    ) -> str:
        """Supersede `head_id` on behalf of a resolution (the MAK-0005 §A5.3
        exception: the subject key may change here). Idempotent."""
        found = self._store.get(org, project, head_id)
        if found is None:
            raise ArtifactNotFoundError(head_id)
        old, token = found
        if old.status == "superseded" and old.superseded_by:
            successor = self._store.get(org, project, old.superseded_by)
            if successor is not None and successor[0].resolution_id == record.resolution_id:
                return successor[0].artifact_id  # already applied
        if old.status != "active":
            raise ConflictChangedError(f"{head_id!r} is no longer a current head")
        new_id = self._next_available_id(org, project, head_id)
        now = utc_now()
        try:
            self._store.put_if_match(
                old.model_copy(update={"status": "superseded", "superseded_by": new_id, "updated_at": now}),
                expected_version_token=token,
            )
        except ConcurrentModificationError as exc:
            raise ConflictChangedError(f"{head_id!r} changed during the resolution") from exc
        successor = Artifact(
            artifact_id=new_id,
            artifact_type=old.artifact_type,
            organization_id=old.organization_id,
            project=old.project,
            title=title if title is not None else old.title,
            content=content if content is not None else old.content,
            rationale=rationale,
            status="active",
            version=bump_version(old.version),
            created_by=record.actor.principal_id,
            created_at=now,
            updated_at=now,
            lineage_id=old.lineage_id,
            supersedes=head_id,
            superseded_by=None,
            tags=list(old.tags),
            subject_key=subject_key,
            provenance=record.actor.model_copy(update={"recorded_at": now}),
            resolution_id=record.resolution_id,
            merged_from=merged_from,
        )
        self._store.put_new(successor)
        return new_id

    def _validate_resolution(self, org, project, conflict: Conflict, action: str, params: dict) -> None:
        candidate_ids = {c.artifact_id for c in conflict.candidates}
        if action == "select_winner":
            if params["winner_artifact_id"] not in candidate_ids:
                raise ValidationFailedError("winner_artifact_id must be one of the conflict's candidates")
        elif action == "merge":
            if params["into_artifact_id"] not in candidate_ids:
                raise ValidationFailedError("into_artifact_id must be one of the conflict's candidates")
        else:
            assigned = [a["artifact_id"] for a in params["assignments"]]
            if sorted(assigned) != sorted(candidate_ids):
                raise ValidationFailedError(
                    "separate_subjects needs exactly one assignment for every candidate "
                    "(it assigns or confirms subjects; it doesn't dismiss the conflict)"
                )
            keys = [a["subject_key"] for a in params["assignments"]]
            if len(set(keys)) != len(keys):
                raise ValidationFailedError("assigned subject keys must be distinct")
            if keys.count(conflict.subject_key) > 1:
                raise ValidationFailedError("at most one candidate may keep the conflicted subject key")
            others = {
                head.subject_key
                for head in self._resolver.heads(self._store.all(org, project)).values()
                if head.artifact_type == conflict.artifact_type
                and head.subject_key is not None
                and head.artifact_id not in candidate_ids
            }
            clash = sorted(set(keys) & others)
            if clash:
                raise SubjectCollisionError(
                    f"subject key(s) {clash} already belong to another current record of this type"
                )

    def _pending_by_subject(self, org: str, project: str) -> dict[tuple[str, str], ResolutionRecord]:
        """Subjects with a resolution in progress, including the target keys
        of a pending separate-subjects resolution (so a half-applied
        separation never looks resolved, §A4.2)."""
        pending: dict[tuple[str, str], ResolutionRecord] = {}
        for record in self._resolutions.for_project(org, project, state="pending"):
            pending[(record.artifact_type, record.subject_key)] = record
            if record.action == "separate_subjects":
                for assignment in record.parameters.get("assignments", []):
                    pending.setdefault((record.artifact_type, assignment["subject_key"]), record)
        return pending

    def _resolving_subjects(self, org: str, project: str) -> set[tuple[str, str]]:
        return set(self._pending_by_subject(org, project))

    def _subject_in_conflict(self, org: str, project: str, subject: tuple[str, str]) -> bool:
        heads = self._resolver.heads(self._store.all(org, project)).values()
        return sum(1 for h in heads if (h.artifact_type, h.subject_key) == subject) > 1

    def _open_conflicts(self, org: str, project: str) -> dict[str, Conflict]:
        pending = self._pending_by_subject(org, project)
        result = self._resolver.resolve(self._store.all(org, project), resolving=set(pending))
        conflicts = [self._to_conflict(org, project, d, pending) for d in result.conflicts]
        return {c.conflict_id: c for c in conflicts}

    def _to_conflict(self, org: str, project: str, detected: DetectedConflict, pending) -> Conflict:
        subject = (detected.artifact_type, detected.subject_key)
        record = pending.get(subject)
        generation = self._resolutions.completed_count(org, project, detected.artifact_type, detected.subject_key)
        candidates = sorted(
            (ConflictCandidate.of(a) for a in detected.artifacts), key=lambda c: c.artifact_id
        )
        hashes = {c.content_sha256 for c in candidates}
        return Conflict(
            conflict_id=conflict_id_for(org, project, detected.artifact_type, detected.subject_key, generation),
            state="resolving" if record is not None else "open",
            generation=generation,
            organization_id=org,
            project=project,
            artifact_type=detected.artifact_type,
            subject_key=detected.subject_key,
            identical_content=len(candidates) > 1 and len(hashes) == 1,
            reason=(
                f"{len(candidates)} lineage(s) claim artifact_type={detected.artifact_type!r} "
                f"subject_key={detected.subject_key!r}"
                + (" (resolution in progress)" if record is not None else "")
            ),
            candidates=candidates,
            hidden_candidate_count=0,
            resolution=record,
        )

    def _resolved_view(self, record: ResolutionRecord) -> Conflict:
        candidates = []
        for artifact_id in record.candidate_artifact_ids:
            found = self._store.get(record.organization_id, record.project, artifact_id)
            if found is not None:
                candidates.append(ConflictCandidate.of(found[0]))
        hashes = {c.content_sha256 for c in candidates}
        return Conflict(
            conflict_id=record.conflict_id,
            state="resolved",
            generation=record.generation,
            organization_id=record.organization_id,
            project=record.project,
            artifact_type=record.artifact_type,
            subject_key=record.subject_key,
            identical_content=len(candidates) > 1 and len(hashes) == 1,
            reason=f"resolved by {record.action} ({record.reason})",
            candidates=candidates,
            hidden_candidate_count=0,
            resolution=record,
        )

    def _reject_supersede(self, correlation_id, principal, old_id, context, reason) -> None:
        if self._audit is not None:
            self._audit.log_supersede_rejected(
                correlation_id=correlation_id,
                actor=principal.principal_id,
                old_id=old_id,
                reason=reason,
                context=context,
            )

    def _log_resolution(self, event, correlation_id, principal, context, **fields) -> None:
        if self._audit is not None:
            self._audit.log(
                event,
                correlation_id=correlation_id,
                actor=principal.principal_id if principal is not None else fields.get("actor_principal_id", "system"),
                context=context,
                **fields,
            )

    # -- internals ------------------------------------------------------

    def _next_available_id(self, organization_id: str, project: str, old_id: str) -> str:
        for attempt in range(1, _MAX_SUCCESSOR_ID_ATTEMPTS + 1):
            candidate = _bump_id(old_id, attempt)
            if self._store.get(organization_id, project, candidate) is None:
                return candidate
        raise RuntimeError(f"could not find an available successor id for {old_id!r}")


def _resolution_parameters(
    action: str,
    *,
    winner_artifact_id,
    into_artifact_id,
    content,
    title,
    rationale,
    assignments,
) -> dict:
    """Validate and normalize the action's parameters (MAK-0004 §A7.3–§A7.5)."""
    if action == "select_winner":
        if not winner_artifact_id:
            raise ValidationFailedError("select_winner needs winner_artifact_id")
        return {"winner_artifact_id": winner_artifact_id}
    if action == "merge":
        if not into_artifact_id or not (content or "").strip():
            raise ValidationFailedError("merge needs into_artifact_id and non-empty content")
        params = {"into_artifact_id": into_artifact_id, "content": content}
        if title is not None:
            if not title.strip():
                raise ValidationFailedError("title must not be blank")
            params["title"] = title
        if rationale is not None:
            params["rationale"] = rationale
        return params
    if action == "separate_subjects":
        if not assignments:
            raise ValidationFailedError("separate_subjects needs assignments")
        normalized = []
        for assignment in assignments:
            if not isinstance(assignment, dict) or not assignment.get("artifact_id"):
                raise ValidationFailedError("each assignment needs artifact_id and subject_key")
            try:
                key = normalize_subject_key(assignment.get("subject_key"))
            except ValueError as exc:
                raise ValidationFailedError(str(exc)) from exc
            if key is None:
                raise ValidationFailedError(
                    "each assignment needs a subject_key; separation assigns subjects, it can't clear them"
                )
            normalized.append({"artifact_id": assignment["artifact_id"], "subject_key": key})
        return {"assignments": normalized}
    raise ValidationFailedError("action must be select_winner, merge or separate_subjects")


def _provenance(
    principal: Principal,
    auth: AuthContext | None,
    auth_method: str,
    client: ClientMetadata | None,
    now,
) -> Provenance:
    """MAK-0006 §7: built only from the authenticated principal and the
    verified authentication context; `client` is the unverified
    self-report."""
    method, _ceiling = _resolve_auth(auth, auth_method)
    return Provenance(
        principal_id=principal.principal_id,
        principal_type=principal.type,
        agent_id=principal.agent_id,
        display_name=principal.display_name,
        auth_method="oauth" if method == "oauth" else ("credential" if method == CREDENTIAL else "operator"),
        credential_id=auth.credential_id if auth is not None else None,
        oauth_client_id=auth.oauth_client_id if auth is not None else None,
        client=client,
        recorded_at=now,
    )


def _resolve_auth(
    auth: AuthContext | None, auth_method: str
) -> tuple[str, frozenset[str] | None]:
    """`auth` (set by the transport from a verified token) wins over the
    legacy `auth_method` keyword, which the CLI and older callers pass."""
    if auth is None:
        return auth_method, None
    return auth.auth_method, auth.ceiling


def _bump_id(old_id: str, offset: int) -> str:
    """[MVP CHOICE]: neither doc specifies a successor-id algorithm, only
    consistent example results (decision-db-001 -> decision-db-002). This
    increments a trailing numeric suffix, zero-padded to the original
    width; ids with no such suffix fall back to an appended `-vN`."""
    match = re.match(r"^(.*-)(\d+)$", old_id)
    if match:
        prefix, digits = match.groups()
        width = len(digits)
        return f"{prefix}{int(digits) + offset:0{width}d}"
    return f"{old_id}-v{offset + 1}"


def _version_sort_key(version: str) -> tuple[int, str]:
    """Sort lineage history oldest-first. Numeric-aware so "10.0" sorts
    after "2.0"; falls back to the raw string for any non-numeric version
    rather than raising."""
    try:
        return (int(version.partition(".")[0]), version)
    except ValueError:
        return (-1, version)
