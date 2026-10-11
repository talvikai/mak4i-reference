from __future__ import annotations

from dataclasses import dataclass

CREDENTIAL = "credential"
OAUTH = "oauth"
OPERATOR_IMPERSONATION = "operator_impersonation"

SCOPE_TO_PERMISSION = {
    "mak4i:read": "read",
    "mak4i:write": "write",
    "mak4i:resolve": "resolve",
}
"""MAK-0008 §7: each OAuth scope is the ceiling on exactly one project
permission."""


@dataclass(frozen=True)
class AuthContext:
    """How the current request was authenticated (MAK-0006 §4, §7).

    Built by the transport layer from a *verified* credential or access
    token — never from request content — and passed to the engine so that
    authorization (the ceiling) and provenance (credential / OAuth client)
    are decided in core, not in an MCP handler.

    `ceiling` is `None` for principal credentials (no ceiling) and the set
    of permissions the OAuth scopes allow for access tokens.
    """

    auth_method: str = CREDENTIAL
    ceiling: frozenset[str] | None = None
    credential_id: str | None = None
    oauth_client_id: str | None = None
    oauth_authorization_id: str | None = None
    scopes: tuple[str, ...] = ()

    @classmethod
    def for_scopes(
        cls,
        scopes: list[str] | tuple[str, ...],
        *,
        oauth_client_id: str,
        oauth_authorization_id: str,
    ) -> AuthContext:
        return cls(
            auth_method=OAUTH,
            ceiling=frozenset(
                SCOPE_TO_PERMISSION[s] for s in scopes if s in SCOPE_TO_PERMISSION
            ),
            oauth_client_id=oauth_client_id,
            oauth_authorization_id=oauth_authorization_id,
            scopes=tuple(scopes),
        )


DEFAULT_AUTH = AuthContext()
