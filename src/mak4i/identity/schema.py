"""SQLAlchemy Core table definitions for the MAK4I control plane.

One `MetaData` shared by `sql_store.py` (runtime) and Alembic (migrations).
Core tables, not ORM classes — `sql_store.py` maps rows to/from the frozen
Pydantic models in `models.py`, keeping validation in exactly one place.

No dialect-specific types: `sqlalchemy.JSON` renders as native `jsonb` on
PostgreSQL and TEXT on SQLite, and `DateTime(timezone=True)` is stored in
UTC by `sql_store.py` and re-tagged as UTC on read. This satisfies the
requirement that the same code path serve SQLite (local) and PostgreSQL
(deployed) with no business-logic change.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

organizations = Table(
    "organizations",
    metadata,
    Column("organization_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

principals = Table(
    "principals",
    metadata,
    Column("principal_id", String, primary_key=True),
    Column(
        "organization_id",
        String,
        ForeignKey("organizations.organization_id"),
        nullable=False,
    ),
    Column("type", String, nullable=False),
    Column("role", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_principals_organization_id", "organization_id"),
)

projects = Table(
    "projects",
    metadata,
    Column("project_id", String, primary_key=True),
    Column(
        "organization_id",
        String,
        ForeignKey("organizations.organization_id"),
        nullable=False,
    ),
    Column("name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_projects_organization_id", "organization_id"),
)

grants = Table(
    "grants",
    metadata,
    Column("grant_id", String, primary_key=True),
    Column(
        "principal_id", String, ForeignKey("principals.principal_id"), nullable=False
    ),
    Column("project_id", String, ForeignKey("projects.project_id"), nullable=False),
    Column("permissions", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("principal_id", "project_id", name="uq_grants_principal_project"),
    Index("ix_grants_principal_id", "principal_id"),
)

credentials = Table(
    "credentials",
    metadata,
    Column("credential_id", String, primary_key=True),
    Column(
        "principal_id", String, ForeignKey("principals.principal_id"), nullable=False
    ),
    Column("token_hash", String, nullable=False, unique=True),
    Column("status", String, nullable=False),
    Column("display_name", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Index("ix_credentials_principal_id", "principal_id"),
)

# -- OAuth authorization service (MAK-0008 §5–§8) ------------------------------
#
# Every secret below (sign-in codes, browser sessions, authorization codes,
# access and refresh tokens, client secrets) is stored only as a SHA-256 hash
# of a high-entropy random value; nothing here can be turned back into a
# usable secret. All state is durable so it survives a restart (§6.8).

oauth_clients = Table(
    "oauth_clients",
    metadata,
    Column("client_id", String, primary_key=True),
    # pre_registered | cimd | dcr
    Column("kind", String, nullable=False),
    # Pre-registered clients belong to one organization; CIMD/DCR clients don't.
    Column("organization_id", String, ForeignKey("organizations.organization_id"), nullable=True),
    Column("client_name", String, nullable=True),
    Column("redirect_uris", JSON, nullable=False),
    Column("token_endpoint_auth_method", String, nullable=False),
    Column("client_secret_hash", String, nullable=True),
    # active | disabled
    Column("status", String, nullable=False),
    Column("created_by", String, nullable=True),
    Column("fetched_at", DateTime(timezone=True), nullable=True),
    Column("cache_expires_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_oauth_clients_organization_id", "organization_id"),
)

oauth_sign_in_codes = Table(
    "oauth_sign_in_codes",
    metadata,
    Column("code_hash", String, primary_key=True),
    Column("principal_id", String, ForeignKey("principals.principal_id"), nullable=False),
    Column("issued_by", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
    Index("ix_oauth_sign_in_codes_principal_id", "principal_id"),
)

oauth_browser_sessions = Table(
    "oauth_browser_sessions",
    metadata,
    Column("session_hash", String, primary_key=True),
    Column("csrf_hash", String, nullable=False),
    Column("principal_id", String, ForeignKey("principals.principal_id"), nullable=True),
    # The validated authorization request awaiting sign-in/consent.
    Column("request", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

oauth_authorizations = Table(
    "oauth_authorizations",
    metadata,
    Column("authorization_id", String, primary_key=True),
    Column("principal_id", String, ForeignKey("principals.principal_id"), nullable=False),
    Column("client_id", String, nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("resource", String, nullable=False),
    # active | revoked
    Column("status", String, nullable=False),
    Column("revoked_reason", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_used_at", DateTime(timezone=True), nullable=False),
    Column("absolute_expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Index("ix_oauth_authorizations_principal_id", "principal_id"),
    Index("ix_oauth_authorizations_client_id", "client_id"),
)

oauth_codes = Table(
    "oauth_codes",
    metadata,
    Column("code_hash", String, primary_key=True),
    Column(
        "authorization_id",
        String,
        ForeignKey("oauth_authorizations.authorization_id"),
        nullable=False,
    ),
    Column("client_id", String, nullable=False),
    Column("redirect_uri", String, nullable=False),
    Column("code_challenge", String, nullable=False),
    Column("resource", String, nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)

oauth_tokens = Table(
    "oauth_tokens",
    metadata,
    Column("token_hash", String, primary_key=True),
    # access | refresh
    Column("kind", String, nullable=False),
    Column(
        "authorization_id",
        String,
        ForeignKey("oauth_authorizations.authorization_id"),
        nullable=False,
    ),
    Column("scopes", JSON, nullable=False),
    # active | rotated | revoked
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Index("ix_oauth_tokens_authorization_id", "authorization_id"),
)
