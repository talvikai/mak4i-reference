from __future__ import annotations

from dataclasses import dataclass

from mak4i.audit import AuditContext, AuditLogger
from mak4i.identity.control_plane import ControlPlane
from mak4i.identity.errors import AccessDeniedError
from mak4i.identity.models import Permission, Principal


def apply_ceiling(
    permissions: list[Permission], ceiling: frozenset[str] | None
) -> list[Permission]:
    """MAK-0006 §5.3: effective permissions are the live grant intersected
    with the authentication method's ceiling (for OAuth, the token's
    scopes). `None` means no ceiling (a principal credential). A ceiling
    can only remove permissions, never add one."""
    if ceiling is None:
        return list(permissions)
    return [p for p in permissions if p in ceiling]


@dataclass(frozen=True)
class AuthorizationOutcome:
    """What a successful `require` call resolved. The engine uses
    `organization_id` to stamp the canonical org onto a new artifact and to
    build the `AuditContext` for the rest of the operation."""

    principal_id: str
    organization_id: str
    project_id: str
    permission: Permission
    agent_id: str | None = None


class Authorizer:
    """The single fail-closed authorization gate every engine operation
    passes through (MVP spec §3: "Authorization must be checked before
    access to context/artifacts").

    Deliberately thin: it asks `ControlPlane` for the principal's effective
    permissions on the project and either returns an `AuthorizationOutcome`
    (logging `ACCESS_GRANTED`) or raises `AccessDeniedError` (logging
    `ACCESS_DENIED`). It never distinguishes "no such project" from "no
    grant" — both yield an empty permission set and the same denial, so an
    unauthorized caller learns nothing about projects outside its grants.
    """

    def __init__(self, control_plane: ControlPlane, audit: AuditLogger | None = None):
        self._control_plane = control_plane
        self._audit = audit

    def require(
        self,
        principal: Principal,
        project: str,
        permission: Permission,
        *,
        correlation_id: str,
        auth_method: str = "credential",
        ceiling: frozenset[str] | None = None,
        client: tuple[str, str] | None = None,
    ) -> AuthorizationOutcome:
        held = apply_ceiling(
            self._control_plane.effective_permissions(principal, project), ceiling
        )
        # MAK-0006 §5.7.4: the client ceiling is the last factor of §5.3.
        permissions = apply_ceiling(
            held, self._control_plane.client_ceiling(principal, project, client)
        )
        organization_id = self._control_plane.resolve_organization_id(principal, project)
        granted = permission in permissions
        # §5.7.6: only a principal that holds the permission learns that the
        # client policy refused it; every other denial stays uniform (§5.5).
        reason = "client_not_permitted" if (not granted and permission in held) else None

        context = AuditContext(
            principal_id=principal.principal_id,
            organization_id=organization_id,
            project_id=project,
            auth_method=auth_method,
            agent_id=principal.agent_id,
        )

        if not granted:
            if self._audit is not None:
                self._audit.log_access_denied(
                    correlation_id=correlation_id, context=context, permission=permission,
                    reason=reason,
                )
            raise AccessDeniedError(
                principal_id=principal.principal_id, permission=permission, reason=reason
            )

        if self._audit is not None:
            self._audit.log_access_granted(
                correlation_id=correlation_id, context=context, permission=permission
            )
        # organization_id is non-None whenever `granted` is true: a non-empty
        # permission set means the project resolved within the principal's org.
        assert organization_id is not None
        return AuthorizationOutcome(
            principal_id=principal.principal_id,
            organization_id=organization_id,
            project_id=project,
            permission=permission,
            agent_id=principal.agent_id,
        )

    def audit_context(
        self, outcome: AuthorizationOutcome, *, auth_method: str = "credential"
    ) -> AuditContext:
        return AuditContext(
            principal_id=outcome.principal_id,
            organization_id=outcome.organization_id,
            project_id=outcome.project_id,
            auth_method=auth_method,
            agent_id=outcome.agent_id,
        )
