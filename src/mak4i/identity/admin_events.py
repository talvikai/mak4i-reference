"""Durable, attributable history of administrative actions (MAK-0006 §8.5).

Every administrative mutation — and every denied attempt — is recorded with
its actor, how the actor was authenticated, the action, the target
identifiers, the outcome and a correlation id. Never a secret: no tokens,
hashes, sign-in codes or client secrets are ever passed in here.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, select

from mak4i.identity import schema

_FORBIDDEN_TARGET_KEYS = {"token", "token_hash", "secret", "client_secret", "code", "password", "sign_in_code"}
_logger = logging.getLogger("mak4i.audit")


class AdminEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    occurred_at: datetime
    organization_id: str | None
    actor_principal_id: str
    actor_auth_method: str
    action: str
    targets: dict[str, str]
    outcome: str
    detail: str | None = None
    correlation_id: str


class AdminEventSink(Protocol):
    def record(self, event: AdminEvent) -> None: ...
    def list(self, organization_id: str, *, limit: int = 200) -> list[AdminEvent]: ...


def new_event(
    *,
    organization_id: str | None,
    actor_principal_id: str,
    actor_auth_method: str,
    action: str,
    targets: dict[str, str],
    outcome: str,
    detail: str | None = None,
    now: datetime | None = None,
) -> AdminEvent:
    leaked = _FORBIDDEN_TARGET_KEYS & set(targets)
    if leaked:  # defensive: callers never pass secrets
        raise ValueError(f"admin events must not carry secrets: {sorted(leaked)}")
    return AdminEvent(
        event_id="evt_" + uuid.uuid4().hex,
        occurred_at=now or datetime.now(timezone.utc),
        organization_id=organization_id,
        actor_principal_id=actor_principal_id,
        actor_auth_method=actor_auth_method,
        action=action,
        targets={k: str(v) for k, v in targets.items() if v is not None},
        outcome=outcome,
        detail=detail,
        correlation_id=str(uuid.uuid4()),
    )


def _emit_log(event: AdminEvent) -> None:
    _logger.info(json.dumps({"event": "ADMIN", **event.model_dump(mode="json")}))


class InMemoryAdminEventSink:
    def __init__(self) -> None:
        self.events: list[AdminEvent] = []

    def record(self, event: AdminEvent) -> None:
        self.events.append(event)
        _emit_log(event)

    def list(self, organization_id: str, *, limit: int = 200) -> list[AdminEvent]:
        found = [e for e in self.events if e.organization_id == organization_id]
        return sorted(found, key=lambda e: e.occurred_at, reverse=True)[:limit]


class SqlAdminEventSink:
    def __init__(self, engine: Engine):
        self._engine = engine

    def record(self, event: AdminEvent) -> None:
        values = event.model_dump()
        values["occurred_at"] = event.occurred_at.astimezone(timezone.utc)
        with self._engine.begin() as conn:
            conn.execute(schema.admin_events.insert().values(**values))
        _emit_log(event)

    def list(self, organization_id: str, *, limit: int = 200) -> list[AdminEvent]:
        t = schema.admin_events
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t)
                .where(t.c.organization_id == organization_id)
                .order_by(t.c.occurred_at.desc())
                .limit(limit)
            ).all()
        out = []
        for row in rows:
            data = dict(row._mapping)
            if data["occurred_at"].tzinfo is None:
                data["occurred_at"] = data["occurred_at"].replace(tzinfo=timezone.utc)
            out.append(AdminEvent.model_validate(data))
        return out
