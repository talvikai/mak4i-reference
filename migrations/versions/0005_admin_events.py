"""administrative audit events (MAK-0006 §8.5)

Revision ID: 0005_admin_events
Revises: 0004_resolution_records
Create Date: 2026-10-10

Additive and reversible; downgrading drops the administrative history only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_admin_events"
down_revision: str | None = "0004_resolution_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_events",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("actor_principal_id", sa.String(), nullable=False),
        sa.Column("actor_auth_method", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=False),
    )
    op.create_index("ix_admin_events_org_time", "admin_events", ["organization_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_events_org_time", table_name="admin_events")
    op.drop_table("admin_events")
