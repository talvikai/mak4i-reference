"""client-scoped project access (MAK-0006 §5.7, CLIENT-AUTH-001)

Revision ID: 0007_client_policies
Revises: 0006_change_feed
Create Date: 2026-10-15

Additive and reversible. Every existing project gets restriction mode
`off` and there are no policies, so effective permissions are unchanged
until an owner creates a policy or turns restrictions on. Downgrading
drops the policies and the mode.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_client_policies"
down_revision: str | None = "0006_change_feed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("client_restrictions", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "client_policies",
        sa.Column("policy_id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), sa.ForeignKey("organizations.organization_id"), nullable=False),
        sa.Column("principal_id", sa.String(), sa.ForeignKey("principals.principal_id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.project_id"), nullable=False),
        sa.Column("client_kind", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("principal_id", "project_id", "client_kind", "client_id",
                            name="uq_client_policies_principal_project_client"),
    )
    op.create_index("ix_client_policies_project", "client_policies", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_client_policies_project", table_name="client_policies")
    op.drop_table("client_policies")
    op.drop_column("projects", "client_restrictions")
