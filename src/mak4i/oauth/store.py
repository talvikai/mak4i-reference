from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, select

from mak4i.identity import schema


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(timezone.utc)


def _row(row) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row._mapping)
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = _aware(value)
    return out


class SqlOAuthStore:
    """Durable OAuth state in the control-plane database (MAK-0008 §6.8).

    Single-use and rotation rules are enforced with conditional UPDATEs
    (`… WHERE used_at IS NULL` / `… WHERE status = 'active'`) inside one
    transaction, so of two concurrent attempts exactly one can win — on
    SQLite and PostgreSQL alike."""

    def __init__(self, engine: Engine):
        self._engine = engine

    # -- clients ------------------------------------------------------------

    def get_client(self, client_id: str) -> dict | None:
        t = schema.oauth_clients
        with self._engine.connect() as conn:
            return _row(conn.execute(select(t).where(t.c.client_id == client_id)).first())

    def put_client(self, client: dict) -> None:
        t = schema.oauth_clients
        values = {k: (_utc(v) if isinstance(v, datetime) else v) for k, v in client.items()}
        with self._engine.begin() as conn:
            exists = conn.execute(
                select(t.c.client_id).where(t.c.client_id == client["client_id"])
            ).first()
            if exists:
                conn.execute(t.update().where(t.c.client_id == client["client_id"]).values(**values))
            else:
                conn.execute(t.insert().values(**values))

    def list_clients(self, organization_id: str) -> list[dict]:
        t = schema.oauth_clients
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t).where(t.c.organization_id == organization_id).order_by(t.c.client_id)
            ).all()
        return [_row(r) for r in rows]

    # -- sign-in codes -----------------------------------------------------

    def insert_sign_in_code(self, values: dict) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                schema.oauth_sign_in_codes.insert().values(
                    **{k: (_utc(v) if isinstance(v, datetime) else v) for k, v in values.items()}
                )
            )

    def consume_sign_in_code(self, code_hash: str, now: datetime) -> dict | None:
        """Mark the code used and return it — or `None` if it doesn't exist
        or was already used. Expiry is checked by the caller."""
        t = schema.oauth_sign_in_codes
        with self._engine.begin() as conn:
            result = conn.execute(
                t.update()
                .where(t.c.code_hash == code_hash, t.c.used_at.is_(None))
                .values(used_at=_utc(now))
            )
            if result.rowcount != 1:
                return None
            return _row(conn.execute(select(t).where(t.c.code_hash == code_hash)).first())

    # -- browser sessions --------------------------------------------------

    def insert_session(self, values: dict) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                schema.oauth_browser_sessions.insert().values(
                    **{k: (_utc(v) if isinstance(v, datetime) else v) for k, v in values.items()}
                )
            )

    def get_session(self, session_hash: str) -> dict | None:
        t = schema.oauth_browser_sessions
        with self._engine.connect() as conn:
            return _row(conn.execute(select(t).where(t.c.session_hash == session_hash)).first())

    def bind_session_principal(self, session_hash: str, principal_id: str) -> None:
        t = schema.oauth_browser_sessions
        with self._engine.begin() as conn:
            conn.execute(
                t.update().where(t.c.session_hash == session_hash).values(principal_id=principal_id)
            )

    def delete_session(self, session_hash: str) -> bool:
        """Delete and report whether this call deleted it (single use)."""
        t = schema.oauth_browser_sessions
        with self._engine.begin() as conn:
            return conn.execute(t.delete().where(t.c.session_hash == session_hash)).rowcount == 1

    def purge_expired_sessions(self, now: datetime) -> None:
        t = schema.oauth_browser_sessions
        with self._engine.begin() as conn:
            conn.execute(t.delete().where(t.c.expires_at < _utc(now)))

    # -- authorizations -----------------------------------------------------

    def insert_authorization(self, values: dict) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                schema.oauth_authorizations.insert().values(
                    **{k: (_utc(v) if isinstance(v, datetime) else v) for k, v in values.items()}
                )
            )

    def get_authorization(self, authorization_id: str) -> dict | None:
        t = schema.oauth_authorizations
        with self._engine.connect() as conn:
            return _row(
                conn.execute(select(t).where(t.c.authorization_id == authorization_id)).first()
            )

    def touch_authorization(self, authorization_id: str, now: datetime) -> None:
        t = schema.oauth_authorizations
        with self._engine.begin() as conn:
            conn.execute(
                t.update()
                .where(t.c.authorization_id == authorization_id)
                .values(last_used_at=_utc(now))
            )

    def revoke_authorization(self, authorization_id: str, reason: str, now: datetime) -> bool:
        """Revoke the authorization and every token descending from it, in
        one transaction. Returns whether it was active before."""
        a, tok = schema.oauth_authorizations, schema.oauth_tokens
        with self._engine.begin() as conn:
            changed = conn.execute(
                a.update()
                .where(a.c.authorization_id == authorization_id, a.c.status == "active")
                .values(status="revoked", revoked_reason=reason, revoked_at=_utc(now))
            ).rowcount
            conn.execute(
                tok.update()
                .where(tok.c.authorization_id == authorization_id, tok.c.status != "revoked")
                .values(status="revoked")
            )
        return changed == 1

    def list_authorizations(
        self,
        *,
        organization_id: str,
        principal_id: str | None = None,
        client_id: str | None = None,
    ) -> list[dict]:
        a, p = schema.oauth_authorizations, schema.principals
        query = (
            select(a)
            .join(p, p.c.principal_id == a.c.principal_id)
            .where(p.c.organization_id == organization_id)
            .order_by(a.c.created_at)
        )
        if principal_id is not None:
            query = query.where(a.c.principal_id == principal_id)
        if client_id is not None:
            query = query.where(a.c.client_id == client_id)
        with self._engine.connect() as conn:
            return [_row(r) for r in conn.execute(query).all()]

    # -- authorization codes ------------------------------------------------

    def insert_code(self, values: dict) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                schema.oauth_codes.insert().values(
                    **{k: (_utc(v) if isinstance(v, datetime) else v) for k, v in values.items()}
                )
            )

    def consume_code(self, code_hash: str, now: datetime) -> tuple[str, dict | None]:
        """`("ok", row)` on first use, `("reused", row)` if it was already
        used, `("unknown", None)` if it never existed."""
        t = schema.oauth_codes
        with self._engine.begin() as conn:
            row = _row(conn.execute(select(t).where(t.c.code_hash == code_hash)).first())
            if row is None:
                return "unknown", None
            changed = conn.execute(
                t.update()
                .where(t.c.code_hash == code_hash, t.c.used_at.is_(None))
                .values(used_at=_utc(now))
            ).rowcount
        return ("ok" if changed == 1 else "reused"), row

    # -- tokens -------------------------------------------------------------

    def insert_token(self, values: dict) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                schema.oauth_tokens.insert().values(
                    **{k: (_utc(v) if isinstance(v, datetime) else v) for k, v in values.items()}
                )
            )

    def get_token(self, token_hash: str) -> dict | None:
        t = schema.oauth_tokens
        with self._engine.connect() as conn:
            return _row(conn.execute(select(t).where(t.c.token_hash == token_hash)).first())

    def rotate_refresh_token(self, token_hash: str) -> bool:
        """Retire an active refresh token. `False` means another request
        already rotated (or revoked) it — i.e. this presentation is a
        replay."""
        t = schema.oauth_tokens
        with self._engine.begin() as conn:
            return (
                conn.execute(
                    t.update()
                    .where(t.c.token_hash == token_hash, t.c.status == "active")
                    .values(status="rotated")
                ).rowcount
                == 1
            )

    def revoke_token(self, token_hash: str) -> None:
        t = schema.oauth_tokens
        with self._engine.begin() as conn:
            conn.execute(t.update().where(t.c.token_hash == token_hash).values(status="revoked"))
