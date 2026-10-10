"""Bring a *local* SQLite control plane up to the current schema.

Local environments (`mak4i init`) create their SQLite schema with
`metadata.create_all` rather than Alembic, and the Alembic migrations are
not part of the installed package. `create_all` adds missing tables but
never changes an existing one, so an environment created by an earlier
release would lack columns added since. `reconcile()` applies the same
changes as the migrations, idempotently, to such databases only:

- databases managed by Alembic (an `alembic_version` table exists) are
  left alone — they are upgraded with `alembic upgrade head`;
- non-SQLite databases are left alone.

Currently: new tables (OAuth, resolution records, admin events) and
`principals.agent_id` (migration 0003, including its backfill of
pre-existing agent principals).
"""

from __future__ import annotations

import re

from sqlalchemy import Engine, inspect, text

from mak4i.identity import schema


def _legacy_agent_id(principal_id: str) -> str:
    hexdigits = re.sub(r"[^0-9a-f]", "", principal_id.lower().split("_", 1)[-1])
    return f"agent-{(hexdigits or 'unknown')[:12]}"


def reconcile(engine: Engine) -> list[str]:
    """Returns the changes it made (empty when already current)."""
    if engine.dialect.name != "sqlite":
        return []
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables or "principals" not in tables:
        return []
    changes: list[str] = []
    missing = [name for name in schema.metadata.tables if name not in tables]
    if missing:
        schema.metadata.create_all(engine)
        changes.append(f"created tables {sorted(missing)}")
    columns = {c["name"] for c in inspector.get_columns("principals")}
    if "agent_id" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE principals ADD COLUMN agent_id VARCHAR"))
            for (principal_id,) in conn.execute(
                text("SELECT principal_id FROM principals WHERE type = 'agent'")
            ).all():
                conn.execute(
                    text("UPDATE principals SET agent_id = :a WHERE principal_id = :p"),
                    {"a": _legacy_agent_id(principal_id), "p": principal_id},
                )
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_principals_org_agent_id "
                    "ON principals (organization_id, agent_id)"
                )
            )
        changes.append("added principals.agent_id")
    return changes
