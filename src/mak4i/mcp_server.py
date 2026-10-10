from __future__ import annotations

import json
import logging
import os
import uuid
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar
from urllib.parse import parse_qs

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from mak4i import __version__
from mak4i.api import ArtifactNotActiveError, MAK4IEngine, SubjectKeyChangeError
from mak4i.audit import AuditLogger
from mak4i.config import build_control_plane_from_env, build_store_from_env
from mak4i.context import ContextPackage
from mak4i.identity import AccessDeniedError, ControlPlane, CredentialInvalidError, Principal
from mak4i.identity.auth_context import CREDENTIAL, DEFAULT_AUTH, AuthContext
from mak4i.identity.authz import Authorizer
from mak4i.identity.tokens import TOKEN_PREFIX
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

_NO_CROSS_CONNECTION_FALLBACK = (
    "This tool writes only to the MAK4I connection it belongs to. If the "
    "call is denied or fails, do NOT retry it, or call a write tool, on "
    "any other MAK4I connection, environment, organization or project "
    "instead: a project with the same name on another connection is a "
    "different project in a different trust boundary. Report the failure "
    "to the user. You may show alternatives only with their full identity "
    "(connection, environment, organization and project IDs, principal, "
    "permission — see mak4i_whoami and mak4i_list_projects) and write to "
    "one only after the user explicitly selects and confirms that exact "
    "destination."
)
# Issues #8/#9: a client with several MAK4I connections retried a denied
# write on a different connection. The server can't see or stop what a
# client does on *other* connections, so this is enforced the only way
# available to it — by telling the calling model, in the tool descriptions,
# the server instructions and every denial/failure message.

_SERVER_INSTRUCTIONS = (
    "MAK4I stores durable project knowledge. Each MAK4I connection is a "
    "separate trust boundary with its own organizations, projects, "
    "principals and permissions; identify it with mak4i_whoami. Identify "
    "a project by connection + organization ID + project ID, never by name "
    "alone. Never retry a denied or failed write on a different MAK4I "
    "connection without the user's explicit confirmation of that exact "
    "destination."
)


class ConnectionIdentity(BaseModel):
    """How this server identifies itself to a client — the part of a
    write target's identity that no project or principal record carries.
    Operator-configured, never derived from a request."""

    instance_name: str
    environment: str
    public_endpoint: str | None = None
    server_version: str


class OrganizationRef(BaseModel):
    organization_id: str
    name: str | None = None


class PrincipalRef(BaseModel):
    principal_id: str
    display_name: str
    principal_type: str
    type: str
    """Deprecated alias of `principal_type` (MAK-0006 §2.2)."""
    role: str


class WhoAmI(BaseModel):
    """`mak4i_whoami`'s result: which installation, which organization,
    which principal — everything a user needs to confirm a destination."""

    connection: ConnectionIdentity
    organization: OrganizationRef
    principal: PrincipalRef
    auth_method: str
    scopes: list[str] | None = None
    """OAuth scopes of the presented access token; `None` for a principal
    credential, which has no scope ceiling (MAK-0008 §7.4)."""


def resolve_connection_identity() -> ConnectionIdentity:
    """`MAK4I_INSTANCE_NAME` / `MAK4I_ENVIRONMENT` (operator labels, e.g.
    "Acme MAK4I" / "production"), `MAK4I_PUBLIC_ENDPOINT`, and the
    installed package version."""
    return ConnectionIdentity(
        instance_name=os.environ.get("MAK4I_INSTANCE_NAME") or "MAK4I",
        environment=os.environ.get("MAK4I_ENVIRONMENT") or "unspecified",
        public_endpoint=os.environ.get("MAK4I_PUBLIC_ENDPOINT") or None,
        server_version=__version__,
    )


def _denied(exc: AccessDeniedError, *, connection: ConnectionIdentity) -> ToolError:
    """An access denial that names the connection that denied it and says,
    in the error itself, that it must not be retried elsewhere. It never
    echoes the requested project, and is identical whether or not that
    project exists (no existence or enumeration leak)."""
    return ToolError(
        f"access denied: this principal has no {exc.permission} permission on "
        f"the requested project on MAK4I connection {connection.instance_name!r} "
        f"(environment: {connection.environment}). This denial is final for "
        "this connection. Do not retry this operation on another MAK4I "
        "connection, organization or project unless the user explicitly "
        "selects and confirms that exact destination."
    )


def _write_failed(exc: Exception, *, connection: ConnectionIdentity) -> ToolError:
    return ToolError(
        f"{exc} (MAK4I connection {connection.instance_name!r}, environment: "
        f"{connection.environment}; nothing was written). Do not retry this "
        "write on another MAK4I connection without the user's explicit "
        "confirmation of that exact destination."
    )


current_principal: ContextVar[Principal | None] = ContextVar(
    "current_principal", default=None
)
"""The principal authenticated for the in-flight call. Set once by
`_AuthMiddleware` per HTTP request (streamable-HTTP) or once at
process startup after authenticating `MAK4I_TOKEN` (stdio) — never derived
from a tool argument, so a caller cannot spoof identity by passing a
different value in the payload."""


current_auth: ContextVar[AuthContext | None] = ContextVar("current_auth", default=None)
"""How `current_principal` was authenticated (credential or OAuth, its
scope ceiling, credential / client ids) — set alongside it by the transport,
never from request content (MAK-0006 §3.6)."""

_stdio_reauthenticate: ContextVar[Callable[[], Principal] | None] = ContextVar(
    "stdio_reauthenticate", default=None
)


def _require_principal() -> Principal:
    reauthenticate = _stdio_reauthenticate.get()
    if reauthenticate is not None:
        # MAK-0006 §6.4: a long-lived stdio session re-checks its credential
        # before every operation, so revocation/deactivation apply at once.
        try:
            current_principal.set(reauthenticate())
        except CredentialInvalidError as exc:
            raise ToolError("not authenticated: the MAK4I credential is no longer valid") from exc
    principal = current_principal.get()
    if principal is None:
        raise ToolError("not authenticated")
    return principal


def _require_auth() -> tuple[Principal, AuthContext]:
    principal = _require_principal()
    return principal, current_auth.get() or DEFAULT_AUTH


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
"""Hosts a local developer would bind `--transport http` to. Distinct from
an enterprise/container bind (typically `0.0.0.0`) — see `resolve_host`
and `build_http_app`'s DNS-rebinding-protection handling."""


def resolve_transport() -> str:
    """`MAK4I_TRANSPORT`, normalized to `'stdio'` or `'streamable-http'`.

    `'http'` is the CLI-friendly spelling (requirements §6/§2);
    `'streamable-http'` is the value already used by the deployed
    Cloud Run service and `docs/DEPLOYMENT.md` and remains accepted so
    that existing container configuration keeps working unchanged. Both
    resolve to the identical Streamable HTTP implementation — there is no
    separate code path per spelling.

    Read directly from the environment (rather than threaded through as
    an argument) so this one function is the single source of truth for
    both `cli.py serve` (which sets the env var from a flag, then asks
    this function to normalize/validate it for the banner) and this
    module's own `main()` — a container that sets `MAK4I_TRANSPORT=http`
    directly, with no CLI involved, resolves identically.
    """
    transport = os.environ.get("MAK4I_TRANSPORT", "stdio")
    if transport == "http":
        return "streamable-http"
    if transport not in ("stdio", "streamable-http"):
        raise ValueError(
            f"unknown MAK4I_TRANSPORT: {transport!r} (expected 'stdio' or 'http'/'streamable-http')"
        )
    return transport


def resolve_host() -> str:
    """`MAK4I_HOST`, defaulting to loopback.

    Requirements §9: a developer running `mak4i serve --transport http`
    for local protocol testing should bind safely by default, not on
    every interface. Explicit enterprise/container deployment sets
    `MAK4I_HOST=0.0.0.0` (or `--host 0.0.0.0`) — this default never
    prevents that, it only changes what happens when nothing is set.
    """
    return os.environ.get("MAK4I_HOST", "127.0.0.1")


def resolve_port() -> int:
    """Bind port, in the precedence requirements §8 specifies:
    `--port` (already folded into `MAK4I_PORT` by the CLI before this
    runs) > `MAK4I_PORT` (MAK4I-specific alias) > `PORT` (the existing
    Cloud Run/container convention `config.py`/the Dockerfile already
    depend on — left authoritative so nothing already deployed breaks)
    > a default."""
    for var in ("MAK4I_PORT", "PORT"):
        value = os.environ.get(var)
        if value:
            return int(value)
    return 8080


def build_server(
    engine: MAK4IEngine,
    *,
    name: str = "mak4i",
    connection: ConnectionIdentity | None = None,
) -> MCPServer:
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

    `connection` (default: `resolve_connection_identity()`) is how this
    server names itself in `mak4i_whoami`, `mak4i_list_projects` and every
    denial — see issues #8/#9 and `_NO_CROSS_CONNECTION_FALLBACK`.
    """
    connection = connection or resolve_connection_identity()
    server = MCPServer(name, instructions=_SERVER_INSTRUCTIONS, version=__version__)

    @server.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> PlainTextResponse:
        # Unauthenticated by design (see build_http_app's auth middleware,
        # which exempts this one path) — a plain liveness check, not a
        # protocol endpoint, so it carries no project knowledge to gate.
        return PlainTextResponse("ok")

    @server.tool(
        description=(
            "Identify this MAK4I connection and who you are on it: the "
            "operator-configured connection name and environment, the "
            "server version and endpoint, your organization (ID and name) "
            "and your principal. Read-only. Call it to show the user exactly "
            "which MAK4I installation a request goes to — especially when "
            "more than one MAK4I connection is available."
        )
    )
    def mak4i_whoami() -> WhoAmI:
        principal, auth = _require_auth()
        organization = engine.organization_of(principal=principal)
        return WhoAmI(
            connection=connection,
            organization=OrganizationRef(
                organization_id=principal.organization_id,
                name=organization.name if organization else None,
            ),
            principal=PrincipalRef(
                principal_id=principal.principal_id,
                display_name=principal.display_name,
                principal_type=principal.type,
                type=principal.type,
                role=principal.role,
            ),
            auth_method=auth.auth_method,
            scopes=list(auth.scopes) if auth.auth_method == "oauth" else None,
        )

    @server.tool(
        description=(
            "List every project the authenticated principal may act on, "
            "with the permissions granted on each (read and/or write) and "
            "the connection, environment and organization it belongs to. "
            "Projects the principal has no grant on never appear — call "
            "this to discover valid `project` values for the other tools. "
            "A project is identified by connection + organization ID + "
            "project ID, not by name: projects with the same name on "
            "different MAK4I connections are unrelated."
        )
    )
    def mak4i_list_projects() -> list[dict]:
        principal, auth = _require_auth()
        authorized = engine.list_projects(principal=principal, auth=auth)
        organization = engine.organization_of(principal=principal)
        return [
            {
                "project_id": ap.project.project_id,
                "name": ap.project.name,
                "organization_id": ap.project.organization_id,
                "organization_name": organization.name
                if organization and organization.organization_id == ap.project.organization_id
                else None,
                "permissions": list(ap.permissions),
                "connection": connection.instance_name,
                "environment": connection.environment,
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
        principal, auth = _require_auth()
        try:
            return engine.search(
                principal=principal,
                auth=auth,
                project=project,
                artifact_type=artifact_type,
                tags=tags,
                status=status,
            )
        except AccessDeniedError as exc:
            raise _denied(exc, connection=connection) from exc

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
        principal, auth = _require_auth()
        try:
            return engine.get_current(
                principal=principal,
                auth=auth,
                project=project,
                artifact_type=artifact_type,
                tags=tags,
            )
        except AccessDeniedError as exc:
            raise _denied(exc, connection=connection) from exc

    @server.tool(
        description=(
            "Create new durable project knowledge. " + _DURABLE_VS_CONVERSATION_GUIDANCE
            + " Optional `subject_key`: a stable, machine-readable key (not display "
            "text) naming the one logical subject this artifact decides, e.g. "
            "\"session-cache\". Current artifacts of the same type that share a "
            "subject_key are reported as a conflict; omit it for artifacts that "
            "are independent by nature (e.g. separate requirement documents). "
            "Tags are for classification and search only and never create conflicts. "
            + _NO_CROSS_CONNECTION_FALLBACK
        )
    )
    def mak4i_create(
        project: str,
        artifact_id: str,
        artifact_type: str,
        title: str,
        content: str,
        rationale: str | None = None,
        tags: list[str] | None = None,
        subject_key: str | None = None,
    ) -> Artifact:
        principal, auth = _require_auth()
        try:
            return engine.create_artifact(
                principal=principal,
                auth=auth,
                project=project,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                title=title,
                content=content,
                rationale=rationale,
                tags=tags,
                subject_key=subject_key,
            )
        except AccessDeniedError as exc:
            raise _denied(exc, connection=connection) from exc
        except (ArtifactAlreadyExistsError, ValueError) as exc:
            raise _write_failed(exc, connection=connection) from exc

    @server.tool(
        description=(
            "Replace current project knowledge with a new decision while "
            "preserving lineage and history — the prior version becomes "
            "superseded, never deleted. `reason` is required and becomes "
            "the new artifact's rationale: a supersede without a stated "
            "reason is not explainable. " + _DURABLE_VS_CONVERSATION_GUIDANCE
            + " The lineage's `subject_key` is kept as-is and cannot be changed "
            "here. To resolve a conflict between lineages that claim the same "
            "subject_key, supersede the losing lineage with "
            "`release_subject_key=true`: its new version stops claiming that "
            "subject (its history keeps the old key); the winning lineage is "
            "left unchanged. " + _NO_CROSS_CONNECTION_FALLBACK
        )
    )
    def mak4i_supersede(
        project: str,
        old_id: str,
        content: str,
        reason: str,
        title: str | None = None,
        tags: list[str] | None = None,
        subject_key: str | None = None,
        release_subject_key: bool = False,
    ) -> Artifact:
        principal, auth = _require_auth()
        try:
            return engine.supersede_artifact(
                principal=principal,
                auth=auth,
                project=project,
                old_id=old_id,
                content=content,
                reason=reason,
                title=title,
                tags=tags,
                subject_key=subject_key,
                release_subject_key=release_subject_key,
            )
        except AccessDeniedError as exc:
            raise _denied(exc, connection=connection) from exc
        except (
            ArtifactNotFoundError,
            ArtifactNotActiveError,
            SubjectKeyChangeError,
            ValueError,
            ArtifactAlreadyExistsError,
            ConcurrentModificationError,
        ) as exc:
            raise _write_failed(exc, connection=connection) from exc

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
        principal, auth = _require_auth()
        try:
            return engine.get_history(
                principal=principal, auth=auth, project=project, lineage_id=lineage_id
            )
        except AccessDeniedError as exc:
            raise _denied(exc, connection=connection) from exc

    return server


_TOOL_SCOPES: dict[str, tuple[str, ...]] = {
    "mak4i_whoami": (),
    "mak4i_list_projects": (),
    "mak4i_search": ("mak4i:read",),
    "mak4i_get_current": ("mak4i:read",),
    "mak4i_history": ("mak4i:read",),
    "mak4i_list_conflicts": ("mak4i:read",),
    "mak4i_get_conflict": ("mak4i:read",),
    "mak4i_create": ("mak4i:write",),
    "mak4i_supersede": ("mak4i:write",),
    "mak4i_resolve_conflict": ("mak4i:read", "mak4i:resolve"),
}
"""MAK-0008 §4.2/§7: OAuth scopes each tool requires. A token lacking one
gets HTTP 403 `insufficient_scope` before the call runs; a principal
lacking the underlying *grant* still gets the tool-level `access_denied`."""

_MAX_BUFFERED_BODY = 8 * 1024 * 1024
_MAX_TRACKED_SESSIONS = 20000


def _replaying_receive(body: bytes, original_receive):
    """An ASGI `receive` that first yields the already-read request body,
    then defers to the original channel (for disconnect messages)."""
    replayed = False

    async def receive():
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await original_receive()

    return receive


def _required_scopes(body: bytes) -> set[str]:
    """Scopes needed by the JSON-RPC message(s) in an MCP POST body. Unknown
    or malformed bodies need none here — the MCP layer rejects them."""
    try:
        payload = json.loads(body) if body else None
    except (ValueError, UnicodeDecodeError):
        return set()
    messages = payload if isinstance(payload, list) else [payload]
    needed: set[str] = set()
    for message in messages:
        if (
            isinstance(message, dict)
            and message.get("method") == "tools/call"
            and isinstance(message.get("params"), dict)
        ):
            needed.update(_TOOL_SCOPES.get(message["params"].get("name"), ()))
    return needed


class _AuthMiddleware:
    """The authentication gate for MCP over Streamable HTTP (MAK-0008 §1–§4).

    Two bearer token kinds are accepted, told apart by prefix and verified
    only as the kind they claim to be (§2.2): principal credentials
    (`mak4i_…`, `ControlPlane.authenticate`) and, when the OAuth service is
    enabled, OAuth access tokens (`mak4at_…`, `OAuthService`). A token that
    fails verification is rejected — never retried as the other kind, never
    downgraded to another identity. Duplicate `Authorization` headers and
    tokens in the query string are refused (§2.3).

    401 responses carry `WWW-Authenticate` with `resource_metadata` and
    `scope` when OAuth is enabled, so OAuth clients can discover the
    authorization server (§4.2). A tool call whose OAuth token lacks the
    tool's scope gets 403 `insufficient_scope` (§4.2).

    MCP sessions are bound to the principal that created them (§1.3).

    `current_principal` / `current_auth` are set only for the duration of
    the request, so one request's identity never leaks into another's.
    `/health`, `/ready` and the OAuth endpoints are exempt.
    """

    _UNAUTHENTICATED_PATHS = frozenset({"/health", "/ready"})
    _UNAUTHENTICATED_PREFIXES = ("/.well-known/",)
    """Discovery documents are public by definition (RFC 8414 / RFC 9728);
    an unknown one is a plain 404, never a 401 that would make a client
    think the discovery location itself needs a token."""

    def __init__(
        self,
        app,
        *,
        control_plane: ControlPlane,
        audit: AuditLogger | None = None,
        oauth=None,
        exempt_paths: frozenset[str] = frozenset(),
    ):
        self.app = app
        self._control_plane = control_plane
        self._audit = audit
        self._oauth = oauth
        self._exempt = self._UNAUTHENTICATED_PATHS | exempt_paths
        self._exempt_prefixes = self._UNAUTHENTICATED_PREFIXES + (("/oauth/",) if oauth else ())
        self._session_owners: OrderedDict[str, str] = OrderedDict()

    # -- responses -------------------------------------------------------------

    def _challenge(self, error: str | None) -> str:
        if self._oauth is None:
            parts = ['realm="mak4i"']
        else:
            settings = self._oauth.settings
            parts = [
                f'resource_metadata="{settings.protected_resource_metadata_url}"',
                'scope="mak4i:read mak4i:write"',
            ]
        if error:
            parts.append(f'error="{error}"')
        return "Bearer " + ", ".join(parts)

    async def _reject(self, scope, receive, send, status: int, error: str | None) -> None:
        body = {"error": "unauthorized"} if status == 401 else {"error": error or "invalid_request"}
        headers = {}
        if status in (401, 403):
            headers["WWW-Authenticate"] = self._challenge(error)
        await JSONResponse(body, status_code=status, headers=headers)(scope, receive, send)

    def _log_failure(self, correlation_id: str, reason: str, auth_method: str) -> None:
        if self._audit is not None:
            self._audit.log_authenticate(
                correlation_id=correlation_id, success=False, reason=reason, auth_method=auth_method
            )

    # -- ASGI --------------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["path"] in self._exempt
            or scope["path"].startswith(self._exempt_prefixes)
        ):
            await self.app(scope, receive, send)
            return

        correlation_id = str(uuid.uuid4())
        headers = [(k.lower(), v) for k, v in scope.get("headers", [])]
        authorizations = [v.decode("latin-1") for k, v in headers if k == b"authorization"]
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if len(authorizations) > 1 or "access_token" in query:
            self._log_failure(correlation_id, "invalid_request", CREDENTIAL)
            await self._reject(scope, receive, send, 400, "invalid_request")
            return

        scheme, _, token = (authorizations[0] if authorizations else "").strip().partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            self._log_failure(correlation_id, "missing_token", CREDENTIAL)
            await self._reject(scope, receive, send, 401, None)
            return

        if token.startswith(TOKEN_PREFIX):
            try:
                principal, credential_id = self._control_plane.authenticate_with_credential(token)
            except CredentialInvalidError as exc:
                self._log_failure(correlation_id, exc.reason, CREDENTIAL)
                await self._reject(scope, receive, send, 401, "invalid_token")
                return
            auth = AuthContext(auth_method=CREDENTIAL, credential_id=credential_id)
        elif self._oauth is not None:
            from mak4i.oauth.service import InvalidAccessToken

            try:
                validated = self._oauth.validate_access_token(token)
            except InvalidAccessToken as exc:
                self._log_failure(correlation_id, exc.reason, "oauth")
                await self._reject(scope, receive, send, 401, "invalid_token")
                return
            principal = validated.principal
            auth = AuthContext.for_scopes(
                validated.scopes,
                oauth_client_id=validated.client_id,
                oauth_authorization_id=validated.authorization_id,
            )
        else:
            self._log_failure(correlation_id, "unknown", CREDENTIAL)
            await self._reject(scope, receive, send, 401, "invalid_token")
            return

        # §1.3: a session belongs to the principal that created it.
        session_id = next((v.decode("latin-1") for k, v in headers if k == b"mcp-session-id"), None)
        if session_id is not None:
            owner = self._session_owners.get(session_id)
            if owner is not None and owner != principal.principal_id:
                self._log_failure(correlation_id, "session_owner_mismatch", auth.auth_method)
                await JSONResponse({"error": "session not found"}, status_code=404)(scope, receive, send)
                return

        # §4.2: OAuth scope check for tool calls, before anything runs.
        if auth.auth_method == "oauth" and scope["method"] == "POST":
            body = b""
            more = True
            while more:
                message = await receive()
                body += message.get("body", b"")
                more = message.get("more_body", False)
                if len(body) > _MAX_BUFFERED_BODY:
                    await JSONResponse({"error": "request too large"}, status_code=413)(scope, receive, send)
                    return
            missing = _required_scopes(body) - set(auth.scopes)
            if missing:
                self._log_failure(correlation_id, "insufficient_scope", "oauth")
                needed = sorted(_required_scopes(body) | set(auth.scopes))
                await JSONResponse(
                    {"error": "insufficient_scope"},
                    status_code=403,
                    headers={
                        "WWW-Authenticate": (
                            f'Bearer error="insufficient_scope", scope="{" ".join(needed)}", '
                            f'resource_metadata="{self._oauth.settings.protected_resource_metadata_url}"'
                        )
                    },
                )(scope, receive, send)
                return
            receive = _replaying_receive(body, receive)

        if self._audit is not None:
            self._audit.log_authenticate(
                correlation_id=correlation_id,
                success=True,
                principal_id=principal.principal_id,
                auth_method=auth.auth_method,
            )

        async def send_and_bind(message):
            if message["type"] == "http.response.start" and session_id is None:
                for k, v in message.get("headers", []):
                    if k.lower() == b"mcp-session-id":
                        self._session_owners[v.decode("latin-1")] = principal.principal_id
                        while len(self._session_owners) > _MAX_TRACKED_SESSIONS:
                            self._session_owners.popitem(last=False)
            await send(message)

        principal_token = current_principal.set(principal)
        auth_token = current_auth.set(auth)
        try:
            await self.app(scope, receive, send_and_bind)
        finally:
            current_principal.reset(principal_token)
            current_auth.reset(auth_token)


def build_http_app(
    server: MCPServer,
    *,
    control_plane: ControlPlane,
    audit: AuditLogger | None = None,
    host: str = "0.0.0.0",
    oauth=None,
) -> Starlette:
    """Wrap the Streamable HTTP app with credential-based auth and a
    `/ready` probe.

    `host` is the address this app is *going to be bound to* by the
    caller (`main()`'s `uvicorn.run(..., host=host, ...)`) — passing it
    through here lets DNS-rebinding protection be conditional on it
    (requirements §9/§15), rather than the previous unconditional
    disable:

    - A loopback host (`127.0.0.1` / `localhost` / `::1`, matched against
      `_LOOPBACK_HOSTS`) is a local developer running
      `mak4i serve --transport http` for protocol testing. DNS-rebinding
      protection exists exactly for that shape of server — a malicious
      webpage tricking a browser into reaching a localhost-bound process
      via a spoofed Host header — so it stays enabled there: passing
      `transport_security=None` lets the SDK apply its own loopback
      allowlist (`127.0.0.1:*`/`localhost:*`/`[::1]:*`) automatically.
    - Any other host (the default, and what an enterprise/container
      deployment explicitly sets — typically `0.0.0.0`) is deliberately
      public: real AI clients' cloud infrastructure must reach it under
      its real hostname, so that threat model doesn't apply and enabling
      the guard would 421 every legitimate request. Protection is
      disabled there, exactly as before this change — credential
      authentication is the actual boundary for that deployment shape.

    The default of `host="0.0.0.0"` (rather than the new loopback-safe
    default `resolve_host()` uses) preserves this function's prior
    behavior for any caller that doesn't pass `host` explicitly.
    """
    is_loopback = host in _LOOPBACK_HOSTS
    transport_security = (
        None if is_loopback else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    app = server.streamable_http_app(host=host, transport_security=transport_security)

    async def ready(request: Request) -> PlainTextResponse:
        # No secrets or config in the body (requirements §11/§16) — a
        # bare status word, mirroring /health.
        if control_plane.is_reachable():
            return PlainTextResponse("ready")
        return PlainTextResponse("not ready", status_code=503)

    # Inserted directly into the router (rather than via
    # `server.custom_route`, which `/health` uses) because this route
    # needs `control_plane`, which `build_server(engine)` deliberately
    # doesn't receive — see that function's docstring on why `engine` is
    # its only required dependency.
    app.router.routes.insert(0, Route("/ready", ready, methods=["GET"]))

    exempt: frozenset[str] = frozenset()
    if oauth is not None:
        # MAK-0008 §4–§8: metadata and the authorization service's own
        # endpoints are reachable without a bearer token.
        from mak4i.oauth.web import OAuthEndpoints

        endpoints = OAuthEndpoints(oauth)
        for route in reversed(endpoints.routes()):
            app.router.routes.insert(0, route)
        exempt = frozenset(endpoints.unauthenticated_paths())

    app.add_middleware(
        _AuthMiddleware, control_plane=control_plane, audit=audit, oauth=oauth, exempt_paths=exempt
    )
    return app


def configure_audit_logging() -> None:
    """Without this, AuditLogger's logger.info(...) calls are silent
    no-ops: Python's root logger defaults to WARNING with no handler
    attached, so nothing reaches stdout for Cloud Logging to capture
    (MVP_ARCHITECTURE.md §21's "watch the Cloud Run logs" requires this to
    actually work). format="%(message)s" keeps each line pure JSON, as
    AuditLogger emits it."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def main(*, on_http_started: Callable[[], None] | None = None) -> None:
    """One entry point for both transports — the container's CMD and a
    developer's local `python -m mak4i.mcp_server` are the same command;
    MAK4I_TRANSPORT picks which one runs.

    `on_http_started`, if given, is called once the HTTP server has
    actually bound its port and finished startup — never before, and never
    if the bind fails. `mak4i serve` uses it to print its "ready" line only
    when that is true. Without it (the container entry point), HTTP serving
    is exactly `uvicorn.run(...)` as before."""
    configure_audit_logging()

    store = build_store_from_env()
    control_plane = build_control_plane_from_env()
    audit = AuditLogger()
    authorizer = Authorizer(control_plane, audit)
    engine = MAK4IEngine(store, audit=audit, authorizer=authorizer, control_plane=control_plane)
    server = build_server(engine)

    transport = resolve_transport()
    if transport == "stdio":
        # A local stdio session is single-user for its whole lifetime —
        # one credential is authenticated once at startup, not per call.
        token = os.environ["MAK4I_TOKEN"]
        principal, credential_id = control_plane.authenticate_with_credential(token)
        current_principal.set(principal)
        current_auth.set(AuthContext(auth_method=CREDENTIAL, credential_id=credential_id))
        _stdio_reauthenticate.set(lambda: control_plane.authenticate(token))
        server.run(transport="stdio")
    else:
        import uvicorn

        from mak4i.config import build_oauth_from_env

        host = resolve_host()
        port = resolve_port()
        oauth = build_oauth_from_env(control_plane, audit=audit)
        app = build_http_app(server, control_plane=control_plane, audit=audit, host=host, oauth=oauth)
        if on_http_started is None:
            uvicorn.run(app, host=host, port=port)
            return

        class _NotifyingServer(uvicorn.Server):
            # uvicorn sets `started` only after a successful bind; a failed
            # bind exits inside `startup` before reaching this check.
            async def startup(self, sockets=None) -> None:
                await super().startup(sockets=sockets)
                if self.started:
                    on_http_started()

        _NotifyingServer(uvicorn.Config(app, host=host, port=port)).run()


if __name__ == "__main__":
    main()
