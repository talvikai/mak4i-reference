"""An rc.5 local environment (SQLite created with create_all, no Alembic)
keeps working after upgrading the code (requirements R5/R6: upgrade)."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from mak4i.identity.local_schema import reconcile


def _rc5_database(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE organizations (organization_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, status VARCHAR NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        conn.execute(text("CREATE TABLE principals (principal_id VARCHAR PRIMARY KEY, organization_id VARCHAR NOT NULL, type VARCHAR NOT NULL, role VARCHAR NOT NULL, display_name VARCHAR NOT NULL, status VARCHAR NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        conn.execute(text("CREATE TABLE projects (project_id VARCHAR PRIMARY KEY, organization_id VARCHAR NOT NULL, name VARCHAR NOT NULL, status VARCHAR NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
        conn.execute(text("CREATE TABLE grants (grant_id VARCHAR PRIMARY KEY, principal_id VARCHAR NOT NULL, project_id VARCHAR NOT NULL, permissions JSON NOT NULL, created_at DATETIME NOT NULL)"))
        conn.execute(text("CREATE TABLE credentials (credential_id VARCHAR PRIMARY KEY, principal_id VARCHAR NOT NULL, token_hash VARCHAR NOT NULL UNIQUE, status VARCHAR NOT NULL, display_name VARCHAR, created_at DATETIME NOT NULL, expires_at DATETIME, revoked_at DATETIME)"))
        t = "2026-10-01 00:00:00"
        conn.execute(text("INSERT INTO organizations VALUES ('org_1', 'Local', 'active', :t, :t)"), {"t": t})
        conn.execute(text("INSERT INTO principals VALUES ('prn_aaaabbbb-cccc', 'org_1', 'agent', 'member', 'Bot', 'active', :t, :t)"), {"t": t})
        conn.execute(text("INSERT INTO principals VALUES ('prn_owner', 'org_1', 'human', 'owner', 'Me', 'active', :t, :t)"), {"t": t})
    return engine


def test_rc5_local_database_is_reconciled_in_place(tmp_path, monkeypatch):
    db = tmp_path / "control-plane.db"
    engine = _rc5_database(db)
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{db}")
    monkeypatch.delenv("MAK4I_CONTROL_PLANE_CREATE_TABLES", raising=False)
    from mak4i.config import build_control_plane_from_env

    cp = build_control_plane_from_env()
    assert cp.get_principal("prn_aaaabbbb-cccc").agent_id == "agent-aaaabbbbcccc"
    assert cp.get_principal("prn_owner").agent_id is None
    tables = set(inspect(engine).get_table_names())
    assert {"oauth_authorizations", "resolution_records", "admin_events"} <= tables
    assert reconcile(engine) == []  # idempotent
    _org, owner = cp.onboard_organization(organization_name="After upgrade", owner_display_name="O")
    assert cp.list_admin_events(actor=owner, organization_id=owner.organization_id)


def test_alembic_managed_databases_are_left_alone(tmp_path):
    engine = _rc5_database(tmp_path / "managed.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR NOT NULL)"))
    assert reconcile(engine) == []
    assert "agent_id" not in {c["name"] for c in inspect(engine).get_columns("principals")}
