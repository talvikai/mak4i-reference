"""Request-scoped audit fields (MAK-0006 §8.6, MAK-0008 §9.4).

A transport opens one scope per request: a correlation id shared by every
audit event of the request and by the error it may return, plus the
non-secret identifiers of how the request was authenticated (credential or
OAuth client) and which tool was called. The engine and the audit logger
read it; nothing here is ever taken from request content."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_SCOPE: ContextVar[dict | None] = ContextVar("mak4i_audit_scope", default=None)

SCOPED_FIELDS = ("tool", "agent_id", "credential_id", "oauth_client_id")


def current() -> dict:
    return dict(_SCOPE.get() or {})


def correlation_id() -> str:
    """The request's correlation id, or a fresh one outside any scope."""
    scope = _SCOPE.get()
    if scope and scope.get("correlation_id"):
        return scope["correlation_id"]
    return str(uuid.uuid4())


@contextmanager
def request_scope(**fields) -> Iterator[dict]:
    merged = {**(_SCOPE.get() or {}), **{k: v for k, v in fields.items() if v is not None}}
    merged.setdefault("correlation_id", str(uuid.uuid4()))
    token = _SCOPE.set(merged)
    try:
        yield merged
    finally:
        _SCOPE.reset(token)
