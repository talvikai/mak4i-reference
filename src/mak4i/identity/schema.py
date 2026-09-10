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
