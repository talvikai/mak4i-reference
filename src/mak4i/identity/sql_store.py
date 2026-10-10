from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import Connection

from mak4i.identity import schema
from mak4i.identity.models import (
    Credential,
    Grant,
    Organization,
    Principal,
    Project,
)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on round-trip; re-tag as UTC on read. Writes
    always store UTC (see `_utc` below), so this is lossless."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc)


class SqlControlPlaneStore:
    """`ControlPlaneStore` backed by a SQL database via SQLAlchemy Core.

    One code path for SQLite (`sqlite:///…`, local/dev) and PostgreSQL
    (`postgresql+psycopg://…`, deployed) — the only difference is the URL.
    Rows are mapped to/from the frozen Pydantic models so validation stays
    in `models.py` alone.

    Pass `create_tables=True` for tests/local dev to build the schema
    directly from `schema.metadata`; the deployed database is migrated with
    Alembic instead.
    """

    def __init__(
        self,
        url_or_engine: str | Engine,
        *,
        create_tables: bool = False,
    ) -> None:
        if isinstance(url_or_engine, Engine):
            self._engine = url_or_engine
        else:
            self._engine = create_engine(url_or_engine, future=True)
        if create_tables:
            schema.metadata.create_all(self._engine)

    @property
    def engine(self) -> Engine:
        return self._engine

    # -- upsert helper ----------------------------------------------------

    @staticmethod
    def _upsert(conn: Connection, table, pk: dict, values: dict) -> None:
        exists = conn.execute(select(table).filter_by(**pk)).first() is not None
        if exists:
            conn.execute(table.update().filter_by(**pk).values(**values))
        else:
            conn.execute(table.insert().values(**values))

    # -- organizations --------------------------------------------------------

    def get_organization(self, organization_id: str) -> Organization | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.organizations).where(
                    schema.organizations.c.organization_id == organization_id
                )
            ).first()
        return _to_organization(row) if row else None

    def put_organization(self, organization: Organization) -> None:
        values = {
            "organization_id": organization.organization_id,
            "name": organization.name,
            "status": organization.status,
            "created_at": _utc(organization.created_at),
            "updated_at": _utc(organization.updated_at),
        }
        with self._engine.begin() as conn:
            self._upsert(
                conn,
                schema.organizations,
                {"organization_id": organization.organization_id},
                values,
            )

    def list_organizations(self) -> list[Organization]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(schema.organizations)).all()
        return [_to_organization(r) for r in rows]

    # -- principals ---------------------------------------------------------

    def get_principal(self, principal_id: str) -> Principal | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.principals).where(
                    schema.principals.c.principal_id == principal_id
                )
            ).first()
        return _to_principal(row) if row else None

    def put_principal(self, principal: Principal) -> None:
        values = {
            "principal_id": principal.principal_id,
            "organization_id": principal.organization_id,
            "type": principal.type,
            "role": principal.role,
            "display_name": principal.display_name,
            "status": principal.status,
            "agent_id": principal.agent_id,
            "created_at": _utc(principal.created_at),
            "updated_at": _utc(principal.updated_at),
        }
        with self._engine.begin() as conn:
            self._upsert(
                conn,
                schema.principals,
                {"principal_id": principal.principal_id},
                values,
            )

    def get_principal_by_agent_id(self, organization_id: str, agent_id: str) -> Principal | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.principals).where(
                    schema.principals.c.organization_id == organization_id,
                    schema.principals.c.agent_id == agent_id,
                )
            ).first()
        return _to_principal(row) if row else None

    def list_principals(self, organization_id: str) -> list[Principal]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(schema.principals).where(
                    schema.principals.c.organization_id == organization_id
                )
            ).all()
        return [_to_principal(r) for r in rows]

    # -- projects ---------------------------------------------------------

    def get_project(self, project_id: str) -> Project | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.projects).where(
                    schema.projects.c.project_id == project_id
                )
            ).first()
        return _to_project(row) if row else None

    def put_project(self, project: Project) -> None:
        values = {
            "project_id": project.project_id,
            "organization_id": project.organization_id,
            "name": project.name,
            "status": project.status,
            "created_at": _utc(project.created_at),
            "updated_at": _utc(project.updated_at),
        }
        with self._engine.begin() as conn:
            self._upsert(
                conn, schema.projects, {"project_id": project.project_id}, values
            )

    def list_projects(self, organization_id: str) -> list[Project]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(schema.projects).where(
                    schema.projects.c.organization_id == organization_id
                )
            ).all()
        return [_to_project(r) for r in rows]

    # -- grants ---------------------------------------------------------

    def get_grant(self, principal_id: str, project_id: str) -> Grant | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.grants).where(
                    schema.grants.c.principal_id == principal_id,
                    schema.grants.c.project_id == project_id,
                )
            ).first()
        return _to_grant(row) if row else None

    def put_grant(self, grant: Grant) -> None:
        values = {
            "grant_id": grant.grant_id,
            "principal_id": grant.principal_id,
            "project_id": grant.project_id,
            "permissions": list(grant.permissions),
            "created_at": _utc(grant.created_at),
        }
        with self._engine.begin() as conn:
            self._upsert(
                conn,
                schema.grants,
                {"principal_id": grant.principal_id, "project_id": grant.project_id},
                values,
            )

    def delete_grant(self, principal_id: str, project_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                schema.grants.delete().where(
                    schema.grants.c.principal_id == principal_id,
                    schema.grants.c.project_id == project_id,
                )
            )

    def list_grants_for_principal(self, principal_id: str) -> list[Grant]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(schema.grants).where(
                    schema.grants.c.principal_id == principal_id
                )
            ).all()
        return [_to_grant(r) for r in rows]

    # -- credentials --------------------------------------------------------

    def get_credential(self, credential_id: str) -> Credential | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.credentials).where(
                    schema.credentials.c.credential_id == credential_id
                )
            ).first()
        return _to_credential(row) if row else None

    def put_credential(self, credential: Credential) -> None:
        values = {
            "credential_id": credential.credential_id,
            "principal_id": credential.principal_id,
            "token_hash": credential.token_hash,
            "status": credential.status,
            "display_name": credential.display_name,
            "created_at": _utc(credential.created_at),
            "expires_at": _utc(credential.expires_at),
            "revoked_at": _utc(credential.revoked_at),
        }
        with self._engine.begin() as conn:
            self._upsert(
                conn,
                schema.credentials,
                {"credential_id": credential.credential_id},
                values,
            )

    def list_credentials(self, principal_id: str) -> list[Credential]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(schema.credentials).where(
                    schema.credentials.c.principal_id == principal_id
                )
            ).all()
        return [_to_credential(r) for r in rows]

    def lookup_credential_by_hash(self, token_hash: str) -> Credential | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(schema.credentials).where(
                    schema.credentials.c.token_hash == token_hash
                )
            ).first()
        return _to_credential(row) if row else None


# -- row -> model -----------------------------------------------------------


def _to_organization(row) -> Organization:
    return Organization(
        organization_id=row.organization_id,
        name=row.name,
        status=row.status,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _to_principal(row) -> Principal:
    return Principal(
        principal_id=row.principal_id,
        organization_id=row.organization_id,
        type=row.type,
        role=row.role,
        display_name=row.display_name,
        status=row.status,
        agent_id=row.agent_id,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _to_project(row) -> Project:
    return Project(
        project_id=row.project_id,
        organization_id=row.organization_id,
        name=row.name,
        status=row.status,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _to_grant(row) -> Grant:
    return Grant(
        grant_id=row.grant_id,
        principal_id=row.principal_id,
        project_id=row.project_id,
        permissions=list(row.permissions),
        created_at=_aware(row.created_at),
    )


def _to_credential(row) -> Credential:
    return Credential(
        credential_id=row.credential_id,
        principal_id=row.principal_id,
        token_hash=row.token_hash,
        status=row.status,
        display_name=row.display_name,
        created_at=_aware(row.created_at),
        expires_at=_aware(row.expires_at),
        revoked_at=_aware(row.revoked_at),
    )
