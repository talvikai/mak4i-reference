"""MAK-0008 §14.5–§14.7: MCP resource URIs, subscriptions and change
notifications.

Notifications are driven by the **durable** change feed, not by in-process
hooks alone: a poller compares each subscribed project's watermark with the
last one it notified, so changes made by the CLI, by another process or by
start-up recovery are noticed too. In-process writes `poke` the poller for
low latency. Before every delivery the subscriber's access is re-checked
(principal active, credential or OAuth authorization still valid, live
`read` grant within the token's ceiling); a subscriber that lost access gets
nothing further. Notifications carry only URIs and are best effort: no
persistence, no replay; clients catch up with `mak4i_changes`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from mak4i.changes.feed import ChangeFeed

logger = logging.getLogger(__name__)

URI_PREFIX = "mak4i://organizations/"
_URI_RE = re.compile(
    r"^mak4i://organizations/(?P<org>[^/]+)/projects/(?P<project>[^/]+)"
    r"/(?:(?P<current>current)|records/(?P<record>[^/]+)|conflicts/(?P<conflict>cfl_[0-9a-f]{32}))$"
)
TEMPLATES = (
    ("mak4i://organizations/{organization_id}/projects/{project_id}/current", "Project current knowledge"),
    ("mak4i://organizations/{organization_id}/projects/{project_id}/records/{artifact_id}", "Record version"),
    ("mak4i://organizations/{organization_id}/projects/{project_id}/conflicts/{conflict_id}", "Conflict"),
)


@dataclass(frozen=True)
class ResourceRef:
    organization_id: str
    project: str
    kind: str  # current | record | conflict
    ident: str | None = None


def parse_uri(uri: str) -> ResourceRef | None:
    match = _URI_RE.match(str(uri))
    if match is None:
        return None
    if match.group("current"):
        return ResourceRef(match.group("org"), match.group("project"), "current")
    if match.group("record"):
        return ResourceRef(match.group("org"), match.group("project"), "record", match.group("record"))
    return ResourceRef(match.group("org"), match.group("project"), "conflict", match.group("conflict"))


def current_uri(organization_id: str, project: str) -> str:
    return f"{URI_PREFIX}{organization_id}/projects/{project}/current"


def record_uri(organization_id: str, project: str, artifact_id: str) -> str:
    return f"{URI_PREFIX}{organization_id}/projects/{project}/records/{artifact_id}"


def conflict_uri(organization_id: str, project: str, conflict_id: str) -> str:
    return f"{URI_PREFIX}{organization_id}/projects/{project}/conflicts/{conflict_id}"


class AccessChecker:
    """Quiet, live access re-check at delivery (MAK-0008 §14.6): no audit
    noise per poll; denials are not events, the notification just isn't
    sent."""

    def __init__(self, control_plane, oauth_store=None) -> None:
        self._cp = control_plane
        self._oauth = oauth_store

    def can_read(self, principal, auth, organization_id: str, project: str) -> bool:
        try:
            if principal.organization_id != organization_id:
                return False
            if "read" not in self._cp.effective_permissions(principal, project):
                return False
            ceiling = getattr(auth, "ceiling", None)
            if ceiling is not None and "read" not in ceiling:
                return False
            # MAK-0006 §5.7: the client policy applies to resources and notifications too.
            client_ceiling = self._cp.client_ceiling(principal, project, getattr(auth, "client", None))
            if client_ceiling is not None and "read" not in client_ceiling:
                return False
            store = self._cp._store  # noqa: SLF001 - same process, read-only
            credential_id = getattr(auth, "credential_id", None)
            if credential_id:
                credential = store.get_credential(credential_id)
                if credential is None or credential.status != "active":
                    return False
                expires = getattr(credential, "expires_at", None)
                if expires is not None:
                    expires = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
                    if expires <= datetime.now(timezone.utc):
                        return False
            authorization_id = getattr(auth, "oauth_authorization_id", None)
            if authorization_id and self._oauth is not None:
                authorization = self._oauth.get_authorization(authorization_id)
                if authorization is None or authorization.get("status") != "active":
                    return False
            return True
        except Exception:  # any doubt: deny
            logger.debug("access re-check failed", exc_info=True)
            return False

    def readable_projects(self, principal, auth) -> frozenset[str]:
        try:
            projects = self._cp._store.list_grants_for_principal(principal.principal_id)  # noqa: SLF001
        except AttributeError:
            projects = []
        return frozenset(
            g.project_id for g in projects
            if self.can_read(principal, auth, principal.organization_id, g.project_id)
        )


Sender = Callable[[str, str | None], Awaitable[None]]  # (kind: "updated"|"list_changed", uri)


@dataclass
class Subscriber:
    sid: str
    principal: object
    auth: object
    send: Sender
    uris: set[str] = field(default_factory=set)
    wants_list_changed: bool = False
    readable: frozenset[str] = frozenset()


class ChangeNotifier:
    def __init__(self, feed: ChangeFeed, checker: AccessChecker, *, interval: float = 2.0,
                 list_interval: float = 10.0) -> None:
        self._feed = feed
        self._checker = checker
        self._interval = interval
        self._list_interval = list_interval
        self._subs: dict[str, Subscriber] = {}
        self._seen: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    # -- registry -------------------------------------------------------------

    def subscribe(self, principal, auth, send: Sender, uris: set[str], *, list_changed: bool = False,
                  sid: str | None = None) -> str:
        sid = sid or uuid.uuid4().hex
        with self._lock:
            sub = self._subs.get(sid)
            if sub is None:
                sub = self._subs[sid] = Subscriber(sid, principal, auth, send)
            sub.uris |= set(uris)
            sub.wants_list_changed = sub.wants_list_changed or list_changed
            for uri in uris:
                ref = parse_uri(uri)
                if ref is not None:
                    key = (ref.organization_id, ref.project)
                    if key not in self._seen:
                        self._seen[key] = self._feed.watermark(*key)
        if list_changed:
            sub.readable = self._checker.readable_projects(principal, auth)
        self._ensure_running()
        return sid

    def unsubscribe(self, sid: str, uris: set[str] | None = None) -> None:
        with self._lock:
            sub = self._subs.get(sid)
            if sub is None:
                return
            if uris is None:
                del self._subs[sid]
            else:
                sub.uris -= set(uris)
                if not sub.uris and not sub.wants_list_changed:
                    del self._subs[sid]

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    # -- wake-ups -----------------------------------------------------------------

    def poke(self, _event=None) -> None:
        """Thread-safe: an in-process write committed; check now."""
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)

    def _ensure_running(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop (tests drive `run_once` directly)
        self._loop = loop
        self._wake = asyncio.Event()
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        last_list = 0.0
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self.subscriber_count() == 0:
                continue
            now = asyncio.get_running_loop().time()
            try:
                await self.run_once(list_changes=now - last_list >= self._list_interval)
            except Exception:
                logger.exception("change notifier pass failed")
            if now - last_list >= self._list_interval:
                last_list = now

    # -- one pass -----------------------------------------------------------------

    async def run_once(self, *, list_changes: bool = True) -> int:
        """Deliver everything new since the last pass. Returns notifications sent."""
        sent = 0
        with self._lock:
            subs = list(self._subs.values())
            projects = {key for key in self._seen}
        updates: dict[tuple[str, str], set[str]] = {}
        for key in projects:
            watermark = await asyncio.to_thread(self._feed.watermark, *key)
            seen = self._seen.get(key, watermark)
            if watermark <= seen:
                continue
            uris = {current_uri(*key)}
            try:
                after = seen
                while True:
                    batch = await asyncio.to_thread(self._feed.read, key[0], key[1], after=after, limit=500)
                    for event in batch.events:
                        if event.artifact_id:
                            uris.add(record_uri(key[0], key[1], event.artifact_id))
                        if event.previous_artifact_id:
                            uris.add(record_uri(key[0], key[1], event.previous_artifact_id))
                        if event.conflict_id:
                            uris.add(conflict_uri(key[0], key[1], event.conflict_id))
                    after = batch.next
                    if not batch.more:
                        break
            except Exception:  # e.g. cursor expired: the current resource still changed
                logger.debug("change feed read for notifications failed", exc_info=True)
            updates[key] = uris
            with self._lock:
                self._seen[key] = watermark
        for sub in subs:
            for key, uris in updates.items():
                wanted = sorted(sub.uris & uris)
                if not wanted:
                    continue
                allowed = await asyncio.to_thread(self._checker.can_read, sub.principal, sub.auth, *key)
                if not allowed:
                    continue
                for uri in wanted:
                    if await self._deliver(sub, "updated", uri):
                        sent += 1
            if list_changes and sub.wants_list_changed:
                readable = await asyncio.to_thread(self._checker.readable_projects, sub.principal, sub.auth)
                if readable != sub.readable:
                    sub.readable = readable
                    if await self._deliver(sub, "list_changed", None):
                        sent += 1
        return sent

    async def _deliver(self, sub: Subscriber, kind: str, uri: str | None) -> bool:
        try:
            await sub.send(kind, uri)
            return True
        except Exception:  # the session or stream is gone
            logger.debug("dropping subscriber %s", sub.sid, exc_info=True)
            self.unsubscribe(sub.sid)
            return False
