"""initial control-plane schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-08

Mirrors mak4i.identity.schema exactly. tests/test_control_plane.py asserts
that `alembic upgrade head` and `schema.metadata.create_all` produce the
same tables, so the two never drift.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("organization_id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "principals",
        sa.Column("principal_id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.organization_id"),
            nullable=False,
        ),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_principals_organization_id", "principals", ["organization_id"])

    op.create_table(
        "projects",
        sa.Column("project_id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.organization_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"])

    op.create_table(
        "grants",
        sa.Column("grant_id", sa.String(), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(),
            sa.ForeignKey("principals.principal_id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
        ),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "principal_id", "project_id", name="uq_grants_principal_project"
        ),
    )
    op.create_index("ix_grants_principal_id", "grants", ["principal_id"])

    op.create_table(
        "credentials",
        sa.Column("credential_id", sa.String(), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(),
            sa.ForeignKey("principals.principal_id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_credentials_principal_id", "credentials", ["principal_id"])


def downgrade() -> None:
    op.drop_table("credentials")
    op.drop_table("grants")
    op.drop_table("projects")
    op.drop_table("principals")
    op.drop_table("organizations")
