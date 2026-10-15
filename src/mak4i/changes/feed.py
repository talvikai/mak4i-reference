"""MAK-0008 §14: the per-project change feed.

Every committed change to a project's governed knowledge gets exactly one
event, ordered by a per-project `sequence`. An event is recorded *pending*
before the change it describes is written and is *committed* (or
*abandoned*) afterwards, so readers can be given only committed events at or
below the **watermark**: the highest sequence below which nothing is still
pending. A process that dies between writing the change and committing the
event leaves a pending event behind; `reconcile_pending` decides from the
stored state whether the change happened (§14.3).

Events carry identifiers and the authenticated actor, never record content.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError as SqlIntegrityError

from mak4i.errors import CursorExpiredError, ValidationFailedError
from mak4i.identity import schema

KINDS = ("record.created", "record.superseded", "record.withdrawn", "resolution.completed", "project.archived")
MAX_LIMIT = 500
DEFAULT_LIMIT = 100
MIN_RETENTION = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventActor(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal_id: str
    agent_id: str | None = None
    auth_method: str


class ChangeEvent(BaseModel):
    """`change-event.schema.json`."""

    model_config = ConfigDict(frozen=True)

    sequence: int
    event_id: str
    kind: str
    organization_id: str
    project: str
    occurred_at: datetime
    artifact_id: str | None = None
    lineage_id: str | None = None
    artifact_type: str | None = None
    subject_key: str | None = None
    previous_artifact_id: str | None = None
    resolution_id: str | None = None
    conflict_id: str | None = None
    actor: EventActor | None = None

    def to_protocol(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        data["actor"] = self.actor.model_dump(mode="json") if self.actor else None
        return data


@dataclass(frozen=True)
class Pending:
    organization_id: str
    project: str
    sequence: int
    event_id: str


@dataclass(frozen=True)
class ChangeBatch:
    events: list[ChangeEvent]
    next: int
    watermark: int
    more: bool

    def to_protocol(self) -> dict[str, Any]:
        return {
            "events": [e.to_protocol() for e in self.events],
            "next": self.next,
            "watermark": self.watermark,
            "more": self.more,
        }


@dataclass(frozen=True)
class PendingEvent:
    pending: Pending
    kind: str
    event: dict[str, Any]
    verify: dict[str, Any]
    created_at: datetime


class ChangeFeed(Protocol):
    durable: bool

    def begin(self, organization_id: str, project: str, kind: str, *, verify: dict[str, Any],
              **fields: Any) -> Pending: ...
    def commit(self, pending: Pending, **fields: Any) -> ChangeEvent: ...
    def abandon(self, pending: Pending) -> None: ...
    def watermark(self, organization_id: str, project: str) -> int: ...
    def read(self, organization_id: str, project: str, *, after: int | None, limit: int = DEFAULT_LIMIT) -> ChangeBatch: ...
    def pending_events(self, *, older_than: timedelta | None = None) -> list[PendingEvent]: ...
    def prune(self, *, retention: timedelta) -> int: ...


def _event(pending: Pending, kind: str, fields: dict[str, Any], occurred_at: datetime) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"unknown change event kind {kind!r}")
    return {
        **fields,
        "sequence": pending.sequence,
        "event_id": pending.event_id,
        "kind": kind,
        "organization_id": pending.organization_id,
        "project": pending.project,
        "occurred_at": occurred_at.isoformat(),
    }


def _batch(events: list[ChangeEvent], *, after: int, watermark: int, limit: int) -> ChangeBatch:
    more = len(events) > limit
    events = events[:limit]
    # Past the last returned event when more remain; otherwise the watermark
    # (any gap up to it is abandoned events: nothing below it is pending).
    next_cursor = events[-1].sequence if more else watermark
    return ChangeBatch(events=events, next=max(next_cursor, after), watermark=watermark, more=more)


def _check_read(after: int | None, limit: int, watermark: int, pruned_through: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValidationFailedError(f"limit must be between 1 and {MAX_LIMIT}")
    if after is None:
        return
    if after < 0:
        raise ValidationFailedError("after must not be negative")
    if after > watermark:
        raise ValidationFailedError("after is beyond the feed's watermark")
    if after < pruned_through:
        raise CursorExpiredError()


class InMemoryChangeFeed:
    """Tests and stores without a SQL control plane; not durable."""

    durable = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._heads: dict[tuple[str, str], list[int]] = {}  # [last_sequence, pruned_through]
        self._rows: dict[tuple[str, str, int], dict[str, Any]] = {}

    def begin(self, organization_id, project, kind, *, verify, **fields) -> Pending:
        with self._lock:
            head = self._heads.setdefault((organization_id, project), [0, 0])
            head[0] += 1
            pending = Pending(organization_id, project, head[0], f"evt_{uuid.uuid4().hex}")
            now = _now()
            self._rows[(organization_id, project, head[0])] = {
                "state": "pending", "kind": kind, "event": _event(pending, kind, fields, now),
                "verify": verify, "created_at": now,
            }
            return pending

    def commit(self, pending, **fields) -> ChangeEvent:
        with self._lock:
            row = self._rows[(pending.organization_id, pending.project, pending.sequence)]
            row["event"] = {**row["event"], **fields, "occurred_at": _now().isoformat()}
            row["state"] = "committed"
            return ChangeEvent.model_validate(row["event"])

    def abandon(self, pending) -> None:
        with self._lock:
            row = self._rows.get((pending.organization_id, pending.project, pending.sequence))
            if row is not None and row["state"] == "pending":
                row["state"] = "abandoned"

    def _watermark(self, org, project) -> int:
        head = self._heads.get((org, project))
        if head is None:
            return 0
        pending = [s for (o, p, s), r in self._rows.items() if o == org and p == project and r["state"] == "pending"]
        return min(pending) - 1 if pending else head[0]

    def watermark(self, organization_id, project) -> int:
        with self._lock:
            return self._watermark(organization_id, project)

    def read(self, organization_id, project, *, after, limit=DEFAULT_LIMIT) -> ChangeBatch:
        with self._lock:
            wm = self._watermark(organization_id, project)
            pruned = self._heads.get((organization_id, project), [0, 0])[1]
            _check_read(after, limit, wm, pruned)
            if after is None:
                return ChangeBatch(events=[], next=wm, watermark=wm, more=False)
            rows = sorted(
                (s, r) for (o, p, s), r in self._rows.items()
                if o == organization_id and p == project and r["state"] == "committed" and after < s <= wm
            )
            events = [ChangeEvent.model_validate(r["event"]) for _, r in rows[: limit + 1]]
            return _batch(events, after=after, watermark=wm, limit=limit)

    def pending_events(self, *, older_than=None) -> list[PendingEvent]:
        cutoff = _now() - older_than if older_than else None
        with self._lock:
            return [
                PendingEvent(Pending(o, p, s, r["event"]["event_id"]), r["kind"], dict(r["event"]), dict(r["verify"]), r["created_at"])
                for (o, p, s), r in sorted(self._rows.items())
                if r["state"] == "pending" and (cutoff is None or r["created_at"] <= cutoff)
            ]

    def prune(self, *, retention) -> int:
        cutoff = _now() - max(retention, MIN_RETENTION)
        removed = 0
        with self._lock:
            for (o, p), head in self._heads.items():
                wm = self._watermark(o, p)
                old = [s for (oo, pp, s), r in self._rows.items()
                       if oo == o and pp == p and s <= wm and r["state"] != "pending" and r["created_at"] < cutoff]
                for s in old:
                    del self._rows[(o, p, s)]
                if old:
                    head[1] = max(head[1], max(old))
                    removed += len(old)
        return removed


class SqlChangeFeed:
    """Durable feed in the control-plane database (migration 0006)."""

    durable = True

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _next_sequence(self, conn, org: str, project: str) -> int:
        h = schema.change_feed_heads
        where = (h.c.organization_id == org) & (h.c.project == project)
        for _ in range(3):
            row = conn.execute(
                update(h).where(where).values(last_sequence=h.c.last_sequence + 1).returning(h.c.last_sequence)
            ).first()
            if row is not None:
                return int(row[0])
            try:
                with conn.begin_nested():
                    conn.execute(insert(h).values(organization_id=org, project=project, last_sequence=1, pruned_through=0))
                return 1
            except SqlIntegrityError:
                continue  # another writer created the head first; increment it
        raise RuntimeError("could not allocate a change-feed sequence")

    def begin(self, organization_id, project, kind, *, verify, **fields) -> Pending:
        now = _now()
        event_id = f"evt_{uuid.uuid4().hex}"
        with self._engine.begin() as conn:
            seq = self._next_sequence(conn, organization_id, project)
            pending = Pending(organization_id, project, seq, event_id)
            conn.execute(insert(schema.change_events).values(
                organization_id=organization_id, project=project, sequence=seq, event_id=event_id, kind=kind,
                state="pending", event=_event(pending, kind, fields, now), verify=verify, created_at=now,
            ))
        return pending

    def _where(self, pending: Pending):
        e = schema.change_events
        return (e.c.organization_id == pending.organization_id) & (e.c.project == pending.project) & (
            e.c.sequence == pending.sequence)

    def commit(self, pending, **fields) -> ChangeEvent:
        e = schema.change_events
        now = _now()
        with self._engine.begin() as conn:
            current = conn.execute(select(e.c.event).where(self._where(pending))).scalar_one()
            event = {**current, **fields, "occurred_at": now.isoformat()}
            conn.execute(update(e).where(self._where(pending)).values(state="committed", event=event, committed_at=now))
        return ChangeEvent.model_validate(event)

    def abandon(self, pending) -> None:
        e = schema.change_events
        with self._engine.begin() as conn:
            conn.execute(update(e).where(self._where(pending) & (e.c.state == "pending")).values(state="abandoned"))

    def _watermark(self, conn, org: str, project: str) -> tuple[int, int]:
        h, e = schema.change_feed_heads, schema.change_events
        head = conn.execute(
            select(h.c.last_sequence, h.c.pruned_through).where((h.c.organization_id == org) & (h.c.project == project))
        ).first()
        if head is None:
            return 0, 0
        lowest_pending = conn.execute(
            select(func.min(e.c.sequence)).where(
                (e.c.organization_id == org) & (e.c.project == project) & (e.c.state == "pending"))
        ).scalar()
        wm = int(lowest_pending) - 1 if lowest_pending is not None else int(head.last_sequence)
        return wm, int(head.pruned_through)

    def watermark(self, organization_id, project) -> int:
        with self._engine.connect() as conn:
            return self._watermark(conn, organization_id, project)[0]

    def read(self, organization_id, project, *, after, limit=DEFAULT_LIMIT) -> ChangeBatch:
        e = schema.change_events
        with self._engine.connect() as conn:
            wm, pruned = self._watermark(conn, organization_id, project)
            _check_read(after, limit, wm, pruned)
            if after is None:
                return ChangeBatch(events=[], next=wm, watermark=wm, more=False)
            rows = conn.execute(
                select(e.c.event).where(
                    (e.c.organization_id == organization_id) & (e.c.project == project)
                    & (e.c.state == "committed") & (e.c.sequence > after) & (e.c.sequence <= wm)
                ).order_by(e.c.sequence).limit(limit + 1)
            ).scalars().all()
        return _batch([ChangeEvent.model_validate(r) for r in rows], after=after, watermark=wm, limit=limit)

    def pending_events(self, *, older_than=None) -> list[PendingEvent]:
        e = schema.change_events
        query = select(e).where(e.c.state == "pending").order_by(e.c.organization_id, e.c.project, e.c.sequence)
        if older_than is not None:
            query = query.where(e.c.created_at <= _now() - older_than)
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        return [
            PendingEvent(
                Pending(r.organization_id, r.project, r.sequence, r.event_id), r.kind, dict(r.event), dict(r.verify),
                r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc),
            )
            for r in rows
        ]

    def prune(self, *, retention) -> int:
        h, e = schema.change_feed_heads, schema.change_events
        cutoff = _now() - max(retention, MIN_RETENTION)
        removed = 0
        with self._engine.begin() as conn:
            for head in conn.execute(select(h.c.organization_id, h.c.project)).all():
                wm, _ = self._watermark(conn, head.organization_id, head.project)
                scope = (e.c.organization_id == head.organization_id) & (e.c.project == head.project)
                old = (scope & (e.c.sequence <= wm) & (e.c.state != "pending") & (e.c.created_at < cutoff))
                highest = conn.execute(select(func.max(e.c.sequence)).where(old)).scalar()
                if highest is None:
                    continue
                removed += conn.execute(delete(e).where(old)).rowcount or 0
                conn.execute(update(h).where(
                    (h.c.organization_id == head.organization_id) & (h.c.project == head.project)
                    & (h.c.pruned_through < highest)
                ).values(pruned_through=highest))
        return removed
