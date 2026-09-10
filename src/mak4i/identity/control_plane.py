from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from mak4i.identity.errors import (
    AccessDeniedError,
    CredentialInvalidError,
    CredentialNotFoundError,
    LastOwnerError,
    OrganizationNotFoundError,
    PrincipalNotFoundError,
    ProjectNotFoundError,
)
from mak4i.identity.models import (
    Credential,
    Grant,
    Organization,
    Permission,
    Principal,
    PrincipalType,
    Project,
    utc_now,
)
from mak4i.identity.store import ControlPlaneStore
from mak4i.identity.tokens import generate_token, hash_token


class AuthorizedProject(BaseModel):
    """A project the principal may act on, paired with the permissions the
    grant confers. The unit `list_authorized_projects` and the
    `mak4i_list_projects` MCP tool both return these."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    project: Project
    permissions: list[Permission]


class ControlPlane:
    """Business rules over a `ControlPlaneStore`.

    Everything the store deliberately does *not* enforce lives here:
    credential authentication, the owner-role gate on administration, the
    last-owner guard, same-organization grant scoping, and grant→permission
    resolution. Nothing here is scenario-specific — org/principal/project
    are generic (MVP spec "IMPORTANT DESIGN PRINCIPLE": smallest secure
    model, not an IAM product).

    Administrative methods take `actor: Principal` and are reachable only
    from the trusted operator CLI — never wired into the MCP server.
    `authenticate()` is the one method the hosted path calls.
    """

    def __init__(
        self,
        store: ControlPlaneStore,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._now = clock

    # -- authentication (hosted path) --------------------------------------

    def authenticate(self, raw_token: str) -> Principal:
        """Resolve a bearer token to its principal, or raise
        `CredentialInvalidError` with a uniform public message. `reason`
        records the specific cause for audit only — it is never surfaced to
        the caller, so the error is not an existence/state oracle."""
        credential = self._store.lookup_credential_by_hash(hash_token(raw_token))
        if credential is None:
            raise CredentialInvalidError("unknown")
        now = self._now()
        if credential.status == "revoked" or credential.revoked_at is not None:
            raise CredentialInvalidError("revoked")
        if not credential.is_usable(now):
            raise CredentialInvalidError("expired")

        principal = self._store.get_principal(credential.principal_id)
        if principal is None:
            raise CredentialInvalidError("orphaned_credential")
        if principal.status != "active":
            raise CredentialInvalidError("principal_inactive")

        organization = self._store.get_organization(principal.organization_id)
        if organization is None or organization.status != "active":
            raise CredentialInvalidError("organization_inactive")
        return principal

    # -- onboarding (bootstrap, no actor) ---------------------------------

    def onboard_organization(
        self,
        *,
        organization_name: str,
        owner_display_name: str,
        owner_type: PrincipalType = "human",
    ) -> tuple[Organization, Principal]:
        organization = Organization(name=organization_name)
        owner = Principal(
            organization_id=organization.organization_id,
            type=owner_type,
            role="owner",
            display_name=owner_display_name,
        )
        self._store.put_organization(organization)
        self._store.put_principal(owner)
        return organization, owner

    # -- administration (operator CLI only) -----------------------------------

    def create_project(self, *, actor: Principal, organization_id: str, name: str) -> Project:
        self._require_owner(actor, organization_id)
        if self._store.get_organization(organization_id) is None:
            raise OrganizationNotFoundError(organization_id)
        clash = any(
            p.name == name and p.status == "active"
            for p in self._store.list_projects(organization_id)
        )
        if clash:
            raise ValueError(
                f"an active project named {name!r} already exists in this organization"
            )
        project = Project(organization_id=organization_id, name=name)
        self._store.put_project(project)
        return project

    def create_principal(
        self,
        *,
        actor: Principal,
        organization_id: str,
        type: PrincipalType,
        display_name: str,
        role: str = "member",
    ) -> Principal:
        self._require_owner(actor, organization_id)
        principal = Principal(
            organization_id=organization_id,
            type=type,
            role=role,
            display_name=display_name,
        )
        self._store.put_principal(principal)
        return principal

    def grant(
        self,
        *,
        actor: Principal,
        principal_id: str,
        project_id: str,
        permissions: list[Permission],
    ) -> Grant:
        principal = self._store.get_principal(principal_id)
        if principal is None:
            raise PrincipalNotFoundError(principal_id)
        project = self._store.get_project(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)
        self._require_owner(actor, principal.organization_id)
        if principal.organization_id != project.organization_id:
            raise ValueError("cannot grant a principal access to another organization's project")
        grant = Grant(
            principal_id=principal_id, project_id=project_id, permissions=permissions
        )
        self._store.put_grant(grant)
        return grant

    def revoke_grant(self, *, actor: Principal, principal_id: str, project_id: str) -> None:
        principal = self._store.get_principal(principal_id)
        if principal is None:
            raise PrincipalNotFoundError(principal_id)
        self._require_owner(actor, principal.organization_id)
        self._store.delete_grant(principal_id, project_id)

    def issue_credential(
        self,
        *,
        actor: Principal,
        principal_id: str,
        display_name: str | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[Credential, str]:
        """Returns `(credential, raw_token)`. The raw token is the only time
        the secret exists in plaintext — the caller must show it to the
        developer once and discard it."""
        principal = self._store.get_principal(principal_id)
        if principal is None:
            raise PrincipalNotFoundError(principal_id)
        self._require_owner(actor, principal.organization_id)
        raw_token, token_hash = generate_token()
        credential = Credential(
            principal_id=principal_id,
            token_hash=token_hash,
            display_name=display_name,
            expires_at=expires_at,
        )
        self._store.put_credential(credential)
        return credential, raw_token

    def revoke_credential(self, *, actor: Principal, credential_id: str) -> Credential:
        credential = self._store.get_credential(credential_id)
        if credential is None:
            raise CredentialNotFoundError(credential_id)
        principal = self._store.get_principal(credential.principal_id)
        if principal is None:
            raise PrincipalNotFoundError(credential.principal_id)
        self._require_owner(actor, principal.organization_id)
        revoked = credential.model_copy(
            update={"status": "revoked", "revoked_at": self._now()}
        )
        self._store.put_credential(revoked)
        return revoked

    def deactivate_principal(self, *, actor: Principal, principal_id: str) -> Principal:
        target = self._store.get_principal(principal_id)
        if target is None:
            raise PrincipalNotFoundError(principal_id)
        self._require_owner(actor, target.organization_id)
        if target.role == "owner" and target.status == "active":
            active_owners = [
                p
                for p in self._store.list_principals(target.organization_id)
                if p.role == "owner" and p.status == "active"
            ]
            if len(active_owners) <= 1:
                raise LastOwnerError(target.organization_id)
        deactivated = target.model_copy(
            update={"status": "deactivated", "updated_at": self._now()}
        )
        self._store.put_principal(deactivated)
        return deactivated

    # -- operator lookups (trusted CLI only) --------------------------------

    def get_principal(self, principal_id: str) -> Principal | None:
        """Look up a principal by id — backs the operator CLI's trusted
        `--principal` impersonation flag, which resolves an identity to act
        as without a credential. Returns `None` rather than raising, so the
        caller decides how to react; unlike `authenticate()`, no active-
        status/org-active enforcement happens here — those checks still run
        inside `Authorizer.require` via `effective_permissions`."""
        return self._store.get_principal(principal_id)

    # -- authorization queries (used by Authorizer + MCP list_projects) ------

    def list_authorized_projects(
        self, principal: Principal, permission: Permission | None = None
    ) -> list[AuthorizedProject]:
        """Every active project the principal holds a grant on (optionally
        filtered to grants conferring `permission`), in a stable order.
        Projects the principal cannot see are simply absent — never an
        error, so nothing leaks."""
        out: list[AuthorizedProject] = []
        for grant in self._store.list_grants_for_principal(principal.principal_id):
            if permission is not None and permission not in grant.permissions:
                continue
            project = self._resolve_active_project(grant.project_id, principal)
            if project is None:
                continue
            out.append(AuthorizedProject(project=project, permissions=grant.permissions))
        return sorted(out, key=lambda ap: ap.project.project_id)

    def effective_permissions(
        self, principal: Principal, project_id: str
    ) -> list[Permission]:
        """The permissions this principal effectively has on this project —
        empty list for no grant, an unknown project, an inactive project, or
        an inactive org. Callers treat empty as 'deny' and must not
        distinguish the causes."""
        grant = self._store.get_grant(principal.principal_id, project_id)
        if grant is None:
            return []
        if self._resolve_active_project(project_id, principal) is None:
            return []
        return list(grant.permissions)

    def resolve_organization_id(self, principal: Principal, project_id: str) -> str | None:
        project = self._resolve_active_project(project_id, principal)
        return project.organization_id if project is not None else None

    # -- internals ---------------------------------------------------------

    def _resolve_active_project(
        self, project_id: str, principal: Principal
    ) -> Project | None:
        project = self._store.get_project(project_id)
        if project is None or project.status != "active":
            return None
        if project.organization_id != principal.organization_id:
            return None
        organization = self._store.get_organization(project.organization_id)
        if organization is None or organization.status != "active":
            return None
        return project

    def _require_owner(self, actor: Principal, organization_id: str) -> None:
        current = self._store.get_principal(actor.principal_id)
        if (
            current is None
            or current.status != "active"
            or current.role != "owner"
            or current.organization_id != organization_id
        ):
            raise AccessDeniedError(
                principal_id=actor.principal_id, permission="administer"
            )
