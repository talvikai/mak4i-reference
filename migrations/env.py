"""Alembic environment for the MAK4I control plane.

The URL comes from `MAK4I_CONTROL_PLANE_DB` (same var as
`config.build_control_plane_from_env`), so `alembic upgrade head` and the
running service never disagree about which database they mean. Autogenerate
targets `mak4i.identity.schema.metadata`.
"""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from mak4i.identity.schema import metadata

config = context.config

_file = os.environ.get("MAK4I_CONTROL_PLANE_DB_FILE")
_url = (
    os.environ.get("MAK4I_CONTROL_PLANE_DB")
    or (open(_file).read().strip() if _file else None)
    or config.get_main_option("sqlalchemy.url")
)
if _url:
    config.set_main_option("sqlalchemy.url", _url)

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
