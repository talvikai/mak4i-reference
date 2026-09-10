from __future__ import annotations

import logging
import os
import uuid
from contextvars import ContextVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from mak4i.api import ArtifactNotActiveError, MAK4IEngine
from mak4i.audit import AuditLogger
from mak4i.config import build_control_plane_from_env, build_store_from_env
from mak4i.context import ContextPackage
from mak4i.identity import AccessDeniedError, ControlPlane, CredentialInvalidError, Principal
from mak4i.identity.authz import Authorizer
from mak4i.models import Artifact
from mak4i.store.base import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ConcurrentModificationError,
)

_DURABLE_VS_CONVERSATION_GUIDANCE = (
    "Call this only when the user's message establishes or changes a "
    "durable project decision, not for hypotheticals, options, or open "
    "questions. If intent is ambiguous, ask the user to confirm before "
    "calling this tool."
)
# [PROTOCOL] per MVP_ARCHITECTURE.md §6: MAK4I cannot inspect intent, so
# this gate is enforced by prompting the calling model via the tool
# description, not by code. This is the exact wording that section
# specifies for mak4i_create/mak4i_supersede.

current_principal: ContextVar[Principal | None] = ContextVar(
    "current_principal", default=None
)
"""The principal authenticated for the in-flight call. Set once by
`_CredentialAuthMiddleware` per HTTP request (streamable-HTTP) or once at
process startup after authenticating `MAK4I_TOKEN` (stdio) — never derived
from a tool argument, so a caller cannot spoof identity by passing a
different value in the payload."""


def _require_principal() -> Principal:
    principal = current_principal.get()
    if principal is None:
        raise ToolError("not authenticated")
    return principal


def build_server(engine: MAK4IEngine, *, name: str = "mak4i") -> MCPServer:
    """Expose the MAK4I tool contract over MCP. This is a thin adapter —
    every tool function below is a couple of lines translating arguments
    to/from an MAK4IEngine call; no protocol logic lives here (requirements
    §9: "the MCP layer must call the same provider-neutral MAK4I engine
    used by CLI/tests; it must not contain project-technology-specific
    behavior").

    Every tool resolves the authenticated `Principal` from `current_principal`
    (never from a tool argument — `created_by`/`actor` no longer exist as
    parameters) and lets the engine's `Authorizer` decide read/write access;
    `AccessDeniedError` surfaces as a `ToolError`, identically whether the
    project doesn't exist or the principal simply has no grant on it.

    `engine` is injected rather than constructed here, so the same
    function serves both the local stdio dev path and the Cloud Run
    deployment — only the store/control-plane backing `engine` and the
    transport passed to `.run()` differ between them.
    """
    server = MCPServer(name)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> PlainTextResponse:
        # Unauthenticated by design (see build_http_app's auth middleware,
        # which exempts this one path) — a plain liveness check, not a
        # protocol endpoint, so it carries no project knowledge to gate.
        return PlainTextResponse("ok")

    @server.tool(
        description=(
            "List every project the authenticated principal may act on, "
            "with the permissions granted on each (read and/or write). "
            "Projects the principal has no grant on never appear — call "
            "this to discover valid `project` values for the other tools."
        )
    )
    def mak4i_list_projects() -> list[dict]:
        principal = _require_principal()
        authorized = engine.list_projects(principal=principal)
        return [
            {
                "project_id": ap.project.project_id,
                "name": ap.project.name,
                "organization_id": ap.project.organization_id,
                "permissions": list(ap.permissions),
            }
            for ap in authorized
        ]

    @server.tool(
        description=(
            "Find applicable artifacts by deterministic metadata filters "
            "(type, tags, status) — a raw candidate lookup, not a resolved "
            "result. Prefer mak4i_get_current for the compact, "
            "conflict/integrity-checked current answer; use mak4i_search "
            "when you want to see all matching candidates directly."
        )
    )
    def mak4i_search(
        project: str,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
        status: str | None = "active",
    ) -> list[Artifact]:
        principal = _require_principal()
        try:
            return engine.search(
                principal=principal,
                project=project,
                artifact_type=artifact_type,
                tags=tags,
                status=status,
            )
        except AccessDeniedError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        description=(
            "Retrieve the smallest useful set of CURRENT applicable "
            "project knowledge for this task — never the whole project. "
            "Call this when the task needs a project-level decision or "
            "fact you don't already have in this conversation, or when "
            "the user explicitly asks you to check MAK4I. Never returns "
            "superseded history — use mak4i_history for that. If the "
            "result's `conflicts` or `integrity_errors` are non-empty, "
            "follow the returned `instruction` rather than guessing."
        )
    )
    def mak4i_get_current(
        project: str,
        artifact_type: str | None = None,
        tags: list[str] | None = None,
    ) -> ContextPackage:
        principal = _require_principal()
        try:
            return engine.get_current(
                principal=principal, project=project, artifact_type=artifact_type, tags=tags
            )
        except AccessDeniedError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        description="Create new durable project knowledge. " + _DURABLE_VS_CONVERSATION_GUIDANCE
    )
    def mak4i_create(
        project: str,
        artifact_id: str,
        artifact_type: str,
        title: str,
        content: str,
        rationale: str | None = None,
        tags: list[str] | None = None,
    ) -> Artifact:
        principal = _require_principal()
        try:
            return engine.create_artifact(
                principal=principal,
                project=project,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                title=title,
                content=content,
                rationale=rationale,
                tags=tags,
            )
        except (ArtifactAlreadyExistsError, AccessDeniedError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        description=(
            "Replace current project knowledge with a new decision while "
            "preserving lineage and history — the prior version becomes "
            "superseded, never deleted. `reason` is required and becomes "
            "the new artifact's rationale: a supersede without a stated "
            "reason is not explainable. " + _DURABLE_VS_CONVERSATION_GUIDANCE
        )
    )
    def mak4i_supersede(
        project: str,
        old_id: str,
        content: str,
        reason: str,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> Artifact:
        principal = _require_principal()
        try:
            return engine.supersede_artifact(
                principal=principal,
                project=project,
                old_id=old_id,
                content=content,
                reason=reason,
                title=title,
                tags=tags,
            )
        except (
            ArtifactNotFoundError,
            ArtifactNotActiveError,
            ArtifactAlreadyExistsError,
            ConcurrentModificationError,
            AccessDeniedError,
        ) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        description=(
            "Retrieve every version in a lineage, any status, oldest "
            "first — the explicit tool for history, migration, or "
            "rationale questions (e.g. \"why did we move off "
            "PostgreSQL?\"). mak4i_get_current intentionally never "
            "returns superseded artifacts; use this tool instead whenever "
            "the task asks for history, migration, or a rationale "
            "comparison."
        )
    )
    def mak4i_history(project: str, lineage_id: str) -> list[Artifact]:
        principal = _require_principal()
        try:
            return engine.get_history(principal=principal, project=project, lineage_id=lineage_id)
        except AccessDeniedError as exc:
            raise ToolError(str(exc)) from exc

    return server


class _CredentialAuthMiddleware(BaseHTTPMiddleware):
    """Application-level auth per MVP_ARCHITECTURE.md §10/§21, upgraded for
    the Developer Preview: Cloud Run ingress allows unauthenticated HTTP
    (external SaaS connectors can't present a Google-signed identity
    token), so this is the actual gate — independent of, not in addition
    to, Cloud Run's own IAM-invoker layer.

    A bearer token is resolved to a `Principal` via
    `ControlPlane.authenticate` (hash lookup, expiry/revocation checked) —
    never against a single shared secret. `/health` is exempt since it
    carries no project knowledge. `current_principal` is set for the
    duration of the request only, so one request's identity can never leak
    into another's.
    """

    def __init__(self, app, *, control_plane: ControlPlane, audit: AuditLogger | None = None):
        super().__init__(app)
        self._control_plane = control_plane
        self._audit = audit

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        correlation_id = str(uuid.uuid4())
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme != "Bearer" or not token:
            if self._audit is not None:
                self._audit.log_authenticate(
                    correlation_id=correlation_id, success=False, reason="missing_token"
                )
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            principal = self._control_plane.authenticate(token)
        except CredentialInvalidError as exc:
            if self._audit is not None:
                self._audit.log_authenticate(
                    correlation_id=correlation_id, success=False, reason=exc.reason
                )
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        if self._audit is not None:
            self._audit.log_authenticate(
                correlation_id=correlation_id,
                success=True,
                principal_id=principal.principal_id,
            )

        reset_token = current_principal.set(principal)
        try:
            return await call_next(request)
        finally:
            current_principal.reset(reset_token)


def build_http_app(
    server: MCPServer, *, control_plane: ControlPlane, audit: AuditLogger | None = None
) -> Starlette:
    """Wrap the Streamable HTTP app with credential-based auth.

    DNS-rebinding protection is disabled here specifically: that guard
    exists to stop a malicious webpage from tricking a browser into
    reaching a *localhost-bound* dev server via a spoofed Host header. This
    server is deliberately public — real AI clients' cloud infrastructure
    must reach it — so the threat model it protects against doesn't apply,
    and enabling it would reject every request to the real Cloud Run
    hostname with 421. Credential authentication above is the actual
    boundary.
    """
    app = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    app.add_middleware(_CredentialAuthMiddleware, control_plane=control_plane, audit=audit)
    return app


def configure_audit_logging() -> None:
    """Without this, AuditLogger's logger.info(...) calls are silent
    no-ops: Python's root logger defaults to WARNING with no handler
    attached, so nothing reaches stdout for Cloud Logging to capture
    (MVP_ARCHITECTURE.md §21's "watch the Cloud Run logs" requires this to
    actually work). format="%(message)s" keeps each line pure JSON, as
    AuditLogger emits it."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    """One entry point for both transports — the container's CMD and a
    developer's local `python -m mak4i.mcp_server` are the same command;
    MAK4I_TRANSPORT picks which one runs."""
    configure_audit_logging()

    store = build_store_from_env()
    control_plane = build_control_plane_from_env()
    audit = AuditLogger()
    authorizer = Authorizer(control_plane, audit)
    engine = MAK4IEngine(store, audit=audit, authorizer=authorizer, control_plane=control_plane)
    server = build_server(engine)

    transport = os.environ.get("MAK4I_TRANSPORT", "stdio")
    if transport == "stdio":
        # A local stdio session is single-user for its whole lifetime —
        # one credential is authenticated once at startup, not per call.
        principal = control_plane.authenticate(os.environ["MAK4I_TOKEN"])
        current_principal.set(principal)
        server.run(transport="stdio")
    elif transport == "streamable-http":
        import uvicorn

        app = build_http_app(server, control_plane=control_plane, audit=audit)
        port = int(os.environ.get("PORT", "8080"))
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        raise ValueError(f"unknown MAK4I_TRANSPORT: {transport!r} (expected 'stdio' or 'streamable-http')")


if __name__ == "__main__":
    main()
