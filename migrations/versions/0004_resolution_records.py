"""conflict resolution records (MAK-0004 §A7–§A8)

Revision ID: 0004_resolution_records
Revises: 0003_agent_id
Create Date: 2026-10-10

Additive and reversible. Downgrading drops resolution history (the
artifacts the resolutions changed are untouched).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_resolution_records"
down_revision: str | None = "0003_agent_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "resolution_records",
        sa.Column("resolution_id", sa.String(), primary_key=True),
        sa.Column("conflict_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("artifact_type", sa.String(), nullable=False),
        sa.Column("subject_key", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("slot", sa.String(), nullable=True, unique=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("record", sa.JSON(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("completed_at", _TS, nullable=True),
    )
    op.create_index(
        "ix_resolution_records_project", "resolution_records", ["organization_id", "project_id"]
    )
    op.create_index("ix_resolution_records_conflict_id", "resolution_records", ["conflict_id"])


def downgrade() -> None:
    op.drop_index("ix_resolution_records_conflict_id", table_name="resolution_records")
    op.drop_index("ix_resolution_records_project", table_name="resolution_records")
    op.drop_table("resolution_records")
