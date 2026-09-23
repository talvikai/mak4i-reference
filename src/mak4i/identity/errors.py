from __future__ import annotations


class IdentityError(Exception):
    """Base class for every control-plane / authorization error."""


class CredentialInvalidError(IdentityError):
    """Authentication failed — the presented bearer token maps to no usable
    credential (unknown, expired, or revoked).

    The public message is deliberately uniform across all three causes so a
    caller cannot use it as an oracle; `reason` carries the specific cause
    for audit/logging only.
    """

    def __init__(self, reason: str = "unknown"):
        super().__init__("invalid or expired credential")
        self.reason = reason


class AccessDeniedError(IdentityError):
    """The authenticated principal is not authorized for the requested
    operation on the requested project.

    Raised identically whether the project does not exist or exists but the
    principal has no grant — never distinguish the two, so an unauthorized
    caller learns nothing about projects outside its grants
    (MVP spec §3: "avoid leaking data from unauthorized projects").
    """

    def __init__(self, *, principal_id: str, permission: str):
        super().__init__("access denied")
        self.principal_id = principal_id
        self.permission = permission


class LastOwnerError(IdentityError):
    """Refused: an active organization must always retain at least one
    active owner principal (MVP spec §1)."""

    def __init__(self, organization_id: str):
        super().__init__(
            f"organization {organization_id!r} must retain at least one active owner"
        )
        self.organization_id = organization_id


class ProjectAlreadyExistsError(IdentityError, ValueError):
    """Refused: an active project with this name already exists in the
    organization. Also inherits `ValueError` — this condition was
    previously raised as a bare `ValueError`, so existing callers that
    catch `ValueError` keep working unchanged."""


class CrossOrganizationGrantError(IdentityError, ValueError):
    """Refused: a grant was attempted between a principal and a project
    belonging to different organizations. Also inherits `ValueError` —
    this condition was previously raised as a bare `ValueError`, so
    existing callers that catch `ValueError` keep working unchanged."""


class EntityNotFoundError(IdentityError):
    """A control-plane entity referenced by an operator/CLI operation does
    not exist. Used only on trusted operator paths — never on a
    principal-facing authorization decision (that raises AccessDeniedError
    instead, to avoid an existence oracle)."""

    entity = "entity"

    def __init__(self, entity_id: str):
        super().__init__(f"{self.entity} not found: {entity_id}")
        self.entity_id = entity_id


class OrganizationNotFoundError(EntityNotFoundError):
    entity = "organization"


class PrincipalNotFoundError(EntityNotFoundError):
    entity = "principal"


class ProjectNotFoundError(EntityNotFoundError):
    entity = "project"


class CredentialNotFoundError(EntityNotFoundError):
    entity = "credential"
