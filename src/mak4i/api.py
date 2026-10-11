from __future__ import annotations

import re
import uuid

from mak4i.audit import AuditContext, AuditLogger
from mak4i.context import ContextBuilder, ContextPackage
from mak4i.discovery import Discovery
from mak4i.identity import AuthorizedProject, ControlPlane, Organization, Principal
from mak4i.identity.auth_context import AuthContext
from mak4i.identity.authz import Authorizer, apply_ceiling
from mak4i.models import Artifact, bump_version, normalize_subject_key, utc_now
from mak4i.resolution import IntegrityError, Resolver
from mak4i.store.base import ArtifactNotFoundError, ArtifactStore

_MAX_SUCCESSOR_ID_ATTEMPTS = 1000

CREDENTIAL = "credential"
OPERATOR_IMPERSONATION = "operator_impersonation"


class ArtifactNotActiveError(Exception):
    """Raised by supersede_artifact when the target isn't currently active.

    A supersede must never be applied to an already-superseded artifact —
    MVP_ARCHITECTURE.md §5 step 2: "Confirm old.status == active. If not,
    reject." Nothing is written when this is raised.
    """

    def __init__(self, artifact_id: str, status: str):
        super().__init__(
            f"cannot supersede {artifact_id!r}: status is {status!r}, not 'active'"
        )
        self.artifact_id = artifact_id
        self.status = status


class SubjectKeyChangeError(Exception):
    """Raised by supersede_artifact when a supersede would alter the
    lineage's `subject_key` other than by an explicit release: supplying a
    different key, acquiring a key for a lineage that has none (including
    one that previously released its key), or an ambiguous/no-op release.

    `subject_key` is stable within a lineage: a supersede may omit it
    (carried forward) or restate it, and may give it up only via
    `release_subject_key=True`. Nothing is written when this is raised.
    """

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
    ):
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

        new_id = self._next_available_id(organization_id, project, old_id)
        now = utc_now()

        old_marked_superseded = old.model_copy(
            update={"status": "superseded", "superseded_by": new_id, "updated_at": now}
        )
        # Step 4: the GCS-native race-closing check — if `old` changed since
        # we read it, reject the whole operation here and write nothing else.
        self._store.put_if_match(old_marked_superseded, expected_version_token=old_token)

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
        resolution = self._resolver.resolve(
            all_artifacts, artifact_type=artifact_type, tags=tags
        )
        if self._audit is not None:
            self._audit.log_resolve(
                correlation_id=correlation_id,
                actor=principal.principal_id,
                resolution=resolution,
                context=context,
            )
            for conflict in resolution.conflicts:
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

        package = self._context_builder.build(resolution, resolution_trace_id=correlation_id)
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

    # -- internals ------------------------------------------------------

    def _next_available_id(self, organization_id: str, project: str, old_id: str) -> str:
        for attempt in range(1, _MAX_SUCCESSOR_ID_ATTEMPTS + 1):
            candidate = _bump_id(old_id, attempt)
            if self._store.get(organization_id, project, candidate) is None:
                return candidate
        raise RuntimeError(f"could not find an available successor id for {old_id!r}")


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
