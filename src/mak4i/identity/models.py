from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mak4i.models import utc_now

__all__ = [
    "Organization",
    "Principal",
    "Project",
    "Grant",
    "Credential",
    "OrganizationStatus",
    "PrincipalType",
    "PrincipalRole",
    "PrincipalStatus",
    "ProjectStatus",
    "Permission",
    "CredentialStatus",
    "utc_now",
    "new_organization_id",
    "new_principal_id",
    "new_project_id",
    "new_grant_id",
    "new_credential_id",
]

OrganizationStatus = Literal["active", "suspended"]
PrincipalType = Literal["human", "service", "agent"]
PrincipalRole = Literal["owner", "member"]
PrincipalStatus = Literal["active", "deactivated"]
ProjectStatus = Literal["active", "archived"]
ClientKind = Literal["credential", "oauth_client"]
"""MAK-0006 §5.7.1: how a request's verified client is identified."""
Permission = Literal["read", "write", "resolve"]
CredentialStatus = Literal["active", "revoked"]

AGENT_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,62}[a-z0-9])$")
"""MAK-0006 §3.2: 3–64 lowercase ASCII letters, digits, '.', '_', '-',
starting and ending with a letter or digit."""

_PERMISSION_ORDER = {"read": 0, "write": 1, "resolve": 2}
"""MAK-0006 §5.1: `read`, `write` and `resolve` are independent — none
implies another."""


def _new_id(prefix: str) -> str:
    """Opaque, collision-free id with an entity prefix, e.g.
    `org_1e2d3c4b-...`. The prefix is a readability aid only; nothing
    parses it."""
    return f"{prefix}_{uuid.uuid4()}"


def new_organization_id() -> str:
    return _new_id("org")


def new_principal_id() -> str:
    return _new_id("prn")


def new_client_policy_id() -> str:
    return _new_id("cpol")


def new_project_id() -> str:
    return _new_id("prj")


def new_grant_id() -> str:
    return _new_id("grt")


def new_credential_id() -> str:
    return _new_id("cred")


class _Entity(BaseModel):
    """Shared config for every control-plane entity: immutable, no unknown
    fields, non-blank strings, timezone-aware timestamps — the same
    discipline `models/artifact.py` applies to artifacts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*")
    @classmethod
    def _validate_common(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be blank")
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class Organization(_Entity):
    organization_id: str = Field(default_factory=new_organization_id)
    name: str
    status: OrganizationStatus = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Principal(_Entity):
    """MAK-0006 §2. `type` is exposed on interfaces as `principal_type`
    (canonical) with `type` kept as an alias. `agent_id` is required for
    agents and absent for humans and services (§3); it is organization-
    scoped, immutable and never reused."""

    principal_id: str = Field(default_factory=new_principal_id)
    organization_id: str
    type: PrincipalType
    role: PrincipalRole = "member"
    display_name: str
    status: PrincipalStatus = "active"
    agent_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def _agent_id_rules(self) -> Principal:
        if self.type == "agent":
            if self.agent_id is None or not AGENT_ID_PATTERN.match(self.agent_id):
                raise ValueError(
                    "an agent principal needs an agent_id of 3-64 lowercase letters, digits, "
                    "'.', '_' or '-', starting and ending with a letter or digit"
                )
        elif self.agent_id is not None:
            raise ValueError("only agent principals have an agent_id")
        return self

    @property
    def principal_type(self) -> PrincipalType:
        return self.type


class Project(_Entity):
    project_id: str = Field(default_factory=new_project_id)
    organization_id: str
    name: str
    status: ProjectStatus = "active"
    client_restrictions: bool = False
    """MAK-0006 §5.7.3 restriction mode: False = `off` (a client without a
    policy is unrestricted), True = `on` (deny clients without a policy)."""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ClientPolicy(_Entity):
    """MAK-0006 §5.7.2: a ceiling on one principal's permissions on one
    project when it connects through one verified client. Never a grant;
    an empty `permissions` list means no access through that client."""

    policy_id: str = Field(default_factory=new_client_policy_id)
    organization_id: str
    principal_id: str
    project_id: str
    client_kind: ClientKind
    client_id: str
    permissions: list[Permission]
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("permissions")
    @classmethod
    def _normalize_permissions(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - _PERMISSION_ORDER.keys())
        if unknown:
            raise ValueError(f"unknown permission(s): {unknown}")
        return sorted(set(value), key=_PERMISSION_ORDER.__getitem__)

    @property
    def client(self) -> tuple[str, str]:
        return (self.client_kind, self.client_id)

    def to_protocol(self) -> dict:
        """`client-policy.schema.json` shape."""
        return {
            "policy_id": self.policy_id,
            "organization_id": self.organization_id,
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "client": {"kind": self.client_kind, "id": self.client_id},
            "permissions": list(self.permissions),
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": self.updated_at.isoformat().replace("+00:00", "Z"),
        }


class Grant(_Entity):
    grant_id: str = Field(default_factory=new_grant_id)
    principal_id: str
    project_id: str
    permissions: list[Permission]
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("permissions")
    @classmethod
    def _normalize_permissions(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a grant must carry at least one permission")
        unknown = sorted(set(value) - _PERMISSION_ORDER.keys())
        if unknown:
            raise ValueError(f"unknown permission(s): {unknown}")
        return sorted(set(value), key=_PERMISSION_ORDER.__getitem__)

    def allows(self, permission: Permission) -> bool:
        return permission in self.permissions


class Credential(_Entity):
    credential_id: str = Field(default_factory=new_credential_id)
    principal_id: str
    token_hash: str
    status: CredentialStatus = "active"
    display_name: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def is_usable(self, at: datetime) -> bool:
        """A credential authenticates only while active, not revoked, and
        not past its expiry. Purely a function of the credential's own
        fields — no control-plane state involved."""
        if self.status != "active" or self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= at:
            return False
        return True
