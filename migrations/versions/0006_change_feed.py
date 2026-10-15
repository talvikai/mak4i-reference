"""change feed (MAK-0008 §14)

Revision ID: 0006_change_feed
Revises: 0005_admin_events
Create Date: 2026-10-14

Additive and reversible; downgrading drops the change feed only (events are
change signals, not records or audit).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_change_feed"
down_revision: str | None = "0005_admin_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change_feed_heads",
        sa.Column("organization_id", sa.String(), primary_key=True),
        sa.Column("project", sa.String(), primary_key=True),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("pruned_through", sa.Integer(), nullable=False),
    )
    op.create_table(
        "change_events",
        sa.Column("organization_id", sa.String(), primary_key=True),
        sa.Column("project", sa.String(), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(), nullable=False, unique=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("event", sa.JSON(), nullable=False),
        sa.Column("verify", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_change_events_state", "change_events", ["state", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_change_events_state", table_name="change_events")
    op.drop_table("change_events")
    op.drop_table("change_feed_heads")
