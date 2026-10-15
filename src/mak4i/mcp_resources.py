"""MAK-0008 §14.5–§14.6 over MCP: resources, subscriptions (2025-11-25
`resources/subscribe` and 2026-07-28 `subscriptions/listen`) and change
notifications. A thin adapter: authorization and data come from the engine;
delivery and the live access re-check come from `ChangeNotifier`.

Unreadable and nonexistent resources are refused with the same error
(MAK-0006 §5.5); notifications carry only URIs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anyio
from mcp.server.subscriptions import ResourcesListChanged, ResourceUpdated
from mcp.shared.exceptions import MCPError
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY, event_to_notification
from mcp_types import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    EmptyResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ReadResourceResult,
    SubscribeRequestParams,
    SubscriptionsAcknowledgedNotification,
    SubscriptionsAcknowledgedNotificationParams,
    SubscriptionsListenRequestParams,
    SubscriptionsListenResult,
    TextResourceContents,
    UnsubscribeRequestParams,
)
from mcp_types import Resource as MCPResource
from mcp_types import ResourceTemplate as MCPResourceTemplate

from mak4i.changes.notify import (
    TEMPLATES,
    AccessChecker,
    ChangeNotifier,
    current_uri,
    parse_uri,
)

logger = logging.getLogger(__name__)
_NOT_FOUND = "resource not found, or not readable by this principal"


def _refuse(uri: str) -> MCPError:
    return MCPError(code=INVALID_PARAMS, message=_NOT_FOUND, data={"uri": str(uri)})


def build_notifier(engine) -> ChangeNotifier:
    control_plane = engine._control_plane  # noqa: SLF001 - same process
    oauth_store = None
    try:
        from mak4i.identity.sql_store import SqlControlPlaneStore
        from mak4i.oauth.store import SqlOAuthStore

        if isinstance(control_plane._store, SqlControlPlaneStore):  # noqa: SLF001
            oauth_store = SqlOAuthStore(control_plane._store.engine)  # noqa: SLF001
    except Exception:  # pragma: no cover - OAuth tables absent
        oauth_store = None
    notifier = ChangeNotifier(engine.change_feed, AccessChecker(control_plane, oauth_store))
    engine.add_change_listener(notifier.poke)
    return notifier


class ResourceHandlers:
    """Bound to one server; `principal()` / `auth()` read the request's
    authenticated identity (never request content)."""

    def __init__(self, engine, notifier: ChangeNotifier, principal, auth) -> None:
        self._engine = engine
        self.notifier = notifier
        self._principal = principal
        self._auth = auth

    def _identity(self):
        principal = self._principal()
        if principal is None:
            raise MCPError(code=INVALID_REQUEST, message="not authenticated")
        return principal, self._auth()

    def _readable(self, principal, auth, uri: str) -> bool:
        ref = parse_uri(uri)
        if ref is None:
            return False
        return self.notifier._checker.can_read(principal, auth, ref.organization_id, ref.project)  # noqa: SLF001

    # -- list / templates / read --------------------------------------------------

    async def list_resources(self, ctx, params) -> ListResourcesResult:
        principal, auth = self._identity()
        projects = await anyio.to_thread.run_sync(
            lambda: self._engine.list_projects(principal=principal, auth=auth)
        )
        resources = []
        for item in projects:
            project = item.project
            if "read" not in item.permissions:
                continue
            resources.append(MCPResource(
                uri=current_uri(project.organization_id, project.project_id),
                name=f"{project.name} current knowledge",
                description="Current authoritative records and conflicts of this MAK4I project (MAK-0008 §14.5).",
                mime_type="application/json",
            ))
        return ListResourcesResult(resources=resources)

    async def list_templates(self, ctx, params) -> ListResourceTemplatesResult:
        self._identity()
        return ListResourceTemplatesResult(resource_templates=[
            MCPResourceTemplate(uri_template=template, name=name, mime_type="application/json")
            for template, name in TEMPLATES
        ])

    async def read_resource(self, ctx, params) -> ReadResourceResult:
        principal, auth = self._identity()
        uri = str(params.uri)
        ref = parse_uri(uri)
        if ref is None or ref.organization_id != principal.organization_id:
            raise _refuse(uri)

        def read() -> Any:
            if ref.kind == "current":
                return self._engine.get_current(principal=principal, auth=auth, project=ref.project).model_dump(mode="json", by_alias=True)
            if ref.kind == "record":
                return self._engine.get_record(
                    principal=principal, auth=auth, project=ref.project, artifact_id=ref.ident
                ).model_dump(mode="json", by_alias=True, exclude_none=True)
            return self._engine.get_conflict(
                principal=principal, auth=auth, project=ref.project, conflict_id=ref.ident
            ).model_dump(mode="json", by_alias=True)

        try:
            data = await anyio.to_thread.run_sync(read)
        except Exception as exc:  # access denied and not found look the same
            logger.info("resource read refused: %s", type(exc).__name__)
            raise _refuse(uri) from exc
        return ReadResourceResult(contents=[
            TextResourceContents(uri=uri, text=json.dumps(data), mime_type="application/json")
        ])

    # -- 2025-11-25: resources/subscribe ------------------------------------------

    async def subscribe(self, ctx, params: SubscribeRequestParams) -> EmptyResult:
        principal, auth = self._identity()
        uri = str(params.uri)
        if not await anyio.to_thread.run_sync(self._readable, principal, auth, uri):
            raise _refuse(uri)
        session = ctx.session

        async def send(kind: str, target: str | None) -> None:
            if kind == "updated":
                await session.send_resource_updated(target)
            else:
                await session.send_resource_list_changed()

        self.notifier.subscribe(principal, auth, send, {uri}, list_changed=True, sid=f"session:{id(session)}")
        return EmptyResult()

    async def unsubscribe(self, ctx, params: UnsubscribeRequestParams) -> EmptyResult:
        self._identity()
        self.notifier.unsubscribe(f"session:{id(ctx.session)}", {str(params.uri)})
        return EmptyResult()

    # -- 2026-07-28: subscriptions/listen -----------------------------------------

    async def listen(self, ctx, params: SubscriptionsListenRequestParams) -> SubscriptionsListenResult:
        """Like the SDK's `ListenHandler`, plus MAK-0008 §14.6: every
        requested URI must be readable, and delivery re-checks access."""
        principal, auth = self._identity()
        subscription_id = ctx.request_id
        if subscription_id is None:
            raise MCPError(INVALID_REQUEST, "subscriptions/listen requires a request id")
        requested = params.notifications
        uris = {str(u) for u in (requested.resource_subscriptions or [])}
        for uri in uris:
            if not await anyio.to_thread.run_sync(self._readable, principal, auth, uri):
                raise _refuse(uri)
        honored = type(requested)(
            resources_list_changed=True if requested.resources_list_changed else None,
            resource_subscriptions=sorted(uris) or None,
        )
        meta: dict[str, Any] = {SUBSCRIPTION_ID_META_KEY: subscription_id}
        send_stream, recv_stream = anyio.create_memory_object_stream(256)

        async def send(kind: str, target: str | None) -> None:
            event = ResourceUpdated(uri=target) if kind == "updated" else ResourcesListChanged()
            send_stream.send_nowait(event)

        sid = f"listen:{id(ctx.session)}:{subscription_id}"
        self.notifier.subscribe(principal, auth, send, uris, list_changed=bool(requested.resources_list_changed), sid=sid)
        try:
            await ctx.session.send_notification(
                SubscriptionsAcknowledgedNotification(
                    params=SubscriptionsAcknowledgedNotificationParams(notifications=honored, _meta=meta)
                ),
                related_request_id=subscription_id,
            )
            async for event in recv_stream:
                await ctx.session.send_notification(event_to_notification(event, meta), related_request_id=subscription_id)
        finally:
            self.notifier.unsubscribe(sid)
            send_stream.close()
            recv_stream.close()
        return SubscriptionsListenResult(_meta=meta)


def install(server, engine, *, principal, auth) -> ResourceHandlers:
    """Serve MAK-0008 §14.5–§14.6 on `server` (an `MCPServer`)."""
    notifier = build_notifier(engine)
    handlers = ResourceHandlers(engine, notifier, principal, auth)
    low = server._lowlevel_server  # noqa: SLF001 - documented extension seam
    from mcp_types import PaginatedRequestParams, ReadResourceRequestParams

    low.add_request_handler("resources/list", PaginatedRequestParams, handlers.list_resources)
    low.add_request_handler("resources/templates/list", PaginatedRequestParams, handlers.list_templates)
    low.add_request_handler("resources/read", ReadResourceRequestParams, handlers.read_resource)
    low.add_request_handler("resources/subscribe", SubscribeRequestParams, handlers.subscribe)
    low.add_request_handler("resources/unsubscribe", UnsubscribeRequestParams, handlers.unsubscribe)
    low.add_request_handler("subscriptions/listen", SubscriptionsListenRequestParams, handlers.listen)
    return handlers
