"""Durable storage of conflict resolution records (MAK-0004 §A7.6, §A8).

Records live in the control-plane database next to identities and grants
(SQL) — or in memory for tests. Artifacts stay in the artifact store; the
two cannot share a transaction, so a resolution is written as a `pending`
record *before* any artifact changes and marked `completed` (or `aborted`)
afterwards (§A8.2). The unique `slot` allows at most one non-aborted
resolution per subject and generation (§A8.4).
"""

from __future__ import annotations

import hashlib
import threading
from datetime import timezone
from typing import Protocol

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError as SqlIntegrityError

from mak4i.identity import schema
from mak4i.resolution.types import ResolutionRecord


class SlotTakenError(Exception):
    """Another non-aborted resolution already exists for this subject and
    generation."""


def slot_for(record: ResolutionRecord) -> str:
    material = "\n".join(
        (record.organization_id, record.project, record.artifact_type, record.subject_key)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest() + f":{record.generation}"


class ResolutionStore(Protocol):
    def create_pending(self, record: ResolutionRecord) -> None: ...
    def update(self, record: ResolutionRecord) -> None: ...
    def get(self, resolution_id: str) -> ResolutionRecord | None: ...
    def by_conflict(self, conflict_id: str) -> list[ResolutionRecord]: ...
    def completed_count(
        self, organization_id: str, project: str, artifact_type: str, subject_key: str
    ) -> int: ...
    def for_project(
        self, organization_id: str, project: str, *, state: str | None = None
    ) -> list[ResolutionRecord]: ...
    def all_pending(self) -> list[ResolutionRecord]: ...


class InMemoryResolutionStore:
    def __init__(self) -> None:
        self._records: dict[str, ResolutionRecord] = {}
        self._slots: dict[str, str] = {}
        self._lock = threading.Lock()

    def create_pending(self, record: ResolutionRecord) -> None:
        with self._lock:
            slot = slot_for(record)
            if slot in self._slots:
                raise SlotTakenError(slot)
            self._slots[slot] = record.resolution_id
            self._records[record.resolution_id] = record

    def update(self, record: ResolutionRecord) -> None:
        with self._lock:
            self._records[record.resolution_id] = record
            if record.state == "aborted":
                self._slots.pop(slot_for(record), None)

    def get(self, resolution_id: str) -> ResolutionRecord | None:
        return self._records.get(resolution_id)

    def by_conflict(self, conflict_id: str) -> list[ResolutionRecord]:
        return sorted(
            (r for r in self._records.values() if r.conflict_id == conflict_id),
            key=lambda r: r.created_at,
        )

    def completed_count(self, organization_id, project, artifact_type, subject_key) -> int:
        return sum(
            1
            for r in self._records.values()
            if r.state == "completed"
            and (r.organization_id, r.project, r.artifact_type, r.subject_key)
            == (organization_id, project, artifact_type, subject_key)
        )

    def for_project(self, organization_id, project, *, state=None) -> list[ResolutionRecord]:
        return sorted(
            (
                r
                for r in self._records.values()
                if (r.organization_id, r.project) == (organization_id, project)
                and (state is None or r.state == state)
            ),
            key=lambda r: r.created_at,
        )

    def all_pending(self) -> list[ResolutionRecord]:
        return [r for r in self._records.values() if r.state == "pending"]


class SqlResolutionStore:
    def __init__(self, engine: Engine):
        self._engine = engine

    @staticmethod
    def _values(record: ResolutionRecord) -> dict:
        return {
            "resolution_id": record.resolution_id,
            "conflict_id": record.conflict_id,
            "organization_id": record.organization_id,
            "project_id": record.project,
            "artifact_type": record.artifact_type,
            "subject_key": record.subject_key,
            "generation": record.generation,
            "state": record.state,
            "slot": None if record.state == "aborted" else slot_for(record),
            "idempotency_key": record.idempotency_key,
            "record": record.model_dump(mode="json"),
            "created_at": record.created_at.astimezone(timezone.utc),
            "completed_at": record.completed_at.astimezone(timezone.utc) if record.completed_at else None,
        }

    def create_pending(self, record: ResolutionRecord) -> None:
        try:
            with self._engine.begin() as conn:
                conn.execute(schema.resolution_records.insert().values(**self._values(record)))
        except SqlIntegrityError as exc:
            raise SlotTakenError(slot_for(record)) from exc

    def update(self, record: ResolutionRecord) -> None:
        t = schema.resolution_records
        with self._engine.begin() as conn:
            conn.execute(
                t.update().where(t.c.resolution_id == record.resolution_id).values(**self._values(record))
            )

    def _query(self, *conditions) -> list[ResolutionRecord]:
        t = schema.resolution_records
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.c.record).where(*conditions).order_by(t.c.created_at)).all()
        return [ResolutionRecord.model_validate(row.record) for row in rows]

    def get(self, resolution_id: str) -> ResolutionRecord | None:
        found = self._query(schema.resolution_records.c.resolution_id == resolution_id)
        return found[0] if found else None

    def by_conflict(self, conflict_id: str) -> list[ResolutionRecord]:
        return self._query(schema.resolution_records.c.conflict_id == conflict_id)

    def completed_count(self, organization_id, project, artifact_type, subject_key) -> int:
        t = schema.resolution_records
        with self._engine.connect() as conn:
            return conn.execute(
                select(func.count())
                .select_from(t)
                .where(
                    t.c.organization_id == organization_id,
                    t.c.project_id == project,
                    t.c.artifact_type == artifact_type,
                    t.c.subject_key == subject_key,
                    t.c.state == "completed",
                )
            ).scalar_one()

    def for_project(self, organization_id, project, *, state=None) -> list[ResolutionRecord]:
        t = schema.resolution_records
        conditions = [t.c.organization_id == organization_id, t.c.project_id == project]
        if state is not None:
            conditions.append(t.c.state == state)
        return self._query(*conditions)

    def all_pending(self) -> list[ResolutionRecord]:
        return self._query(schema.resolution_records.c.state == "pending")
