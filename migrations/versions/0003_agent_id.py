"""agent_id for agent principals (MAK-0006 §3)

Revision ID: 0003_agent_id
Revises: 0002_oauth
Create Date: 2026-10-10

Adds `principals.agent_id` (nullable; organization-scoped unique). Every
existing principal is preserved unchanged, with one exception required by
MAK-0006 §2.1: an existing *agent* principal (which earlier releases created
without an agent_id) is given a deterministic one derived from its
principal_id — `agent-<first 12 hex digits of the principal UUID>` — so it
stays valid and attributable. Humans and services get none. Credentials are
untouched. Downgrade drops the column (generated agent_ids are lost; nothing
else changes).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_agent_id"
down_revision: str | None = "0002_oauth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def legacy_agent_id(principal_id: str) -> str:
    hexdigits = re.sub(r"[^0-9a-f]", "", principal_id.lower().split("_", 1)[-1])
    return f"agent-{(hexdigits or 'unknown')[:12]}"


def upgrade() -> None:
    with op.batch_alter_table("principals") as batch:
        batch.add_column(sa.Column("agent_id", sa.String(), nullable=True))
    principals = sa.table(
        "principals",
        sa.column("principal_id", sa.String()),
        sa.column("type", sa.String()),
        sa.column("agent_id", sa.String()),
    )
    conn = op.get_bind()
    for (principal_id,) in conn.execute(
        sa.select(principals.c.principal_id).where(principals.c.type == "agent")
    ).all():
        conn.execute(
            principals.update()
            .where(principals.c.principal_id == principal_id)
            .values(agent_id=legacy_agent_id(principal_id))
        )
    with op.batch_alter_table("principals") as batch:
        batch.create_unique_constraint("uq_principals_org_agent_id", ["organization_id", "agent_id"])


def downgrade() -> None:
    with op.batch_alter_table("principals") as batch:
        batch.drop_constraint("uq_principals_org_agent_id", type_="unique")
        batch.drop_column("agent_id")
