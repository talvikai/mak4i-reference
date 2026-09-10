from __future__ import annotations

import os

from mak4i.identity.control_plane import ControlPlane
from mak4i.store.base import ArtifactStore

DEFAULT_CONTROL_PLANE_DB = "sqlite:///./mak4i-control-plane.db"
"""Local/dev default for the control-plane database. The deployed service
sets MAK4I_CONTROL_PLANE_DB to a `postgresql+psycopg://…` URL; the same
variable is read by Alembic's env.py so migrations and the service never
disagree about which database they mean."""


def build_store_from_env() -> ArtifactStore:
    """MAK4I_STORE selects the artifact backend, independent of transport or
    entry point — shared by mcp_server.py and cli.py so both talk to the
    same backend given the same environment. Only MAK4I_STORE=local (the
    default, for tests/offline dev) uses LocalJSONStore; MAK4I_STORE=gcs is
    what Cloud Run is configured with.
    """
    backend = os.environ.get("MAK4I_STORE", "local")
    if backend == "gcs":
        from mak4i.store.gcs import GCSArtifactStore

        bucket_name = os.environ["MAK4I_GCS_BUCKET"]
        project = os.environ.get("MAK4I_GCP_PROJECT")
        return GCSArtifactStore(bucket_name, project=project)
    if backend == "local":
        from mak4i.store.local_json import LocalJSONStore

        store_dir = os.environ.get("MAK4I_LOCAL_STORE_DIR", "artifacts/local")
        return LocalJSONStore(store_dir)
    raise ValueError(f"unknown MAK4I_STORE: {backend!r} (expected 'local' or 'gcs')")


def build_control_plane_from_env() -> ControlPlane:
    """Build the control plane over a SQL database selected by
    MAK4I_CONTROL_PLANE_DB (SQLite locally, PostgreSQL deployed).

    `MAK4I_CONTROL_PLANE_CREATE_TABLES=1` builds the schema directly from
    the metadata — convenient for local/dev/CI against a throwaway SQLite
    file. The deployed Postgres database is migrated with Alembic instead,
    so that variable is left unset there.
    """
    from mak4i.identity.sql_store import SqlControlPlaneStore

    url = os.environ.get("MAK4I_CONTROL_PLANE_DB", DEFAULT_CONTROL_PLANE_DB)
    create_tables = os.environ.get("MAK4I_CONTROL_PLANE_CREATE_TABLES") == "1"
    return ControlPlane(SqlControlPlaneStore(url, create_tables=create_tables))
