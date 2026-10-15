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
    client_registration: str | None = None
    """How the OAuth client was registered: `pre_registered`,
    `metadata_document` or `dynamic` (MAK-0008 §10.5); `None` for
    credentials."""

    @property
    def client(self) -> tuple[str, str] | None:
        """MAK-0006 §5.7.1 verified client identity `(kind, id)`, derived only
        from the verified credential or token — never from `clientInfo`.
        `None` when the request has no such identity (operator tooling)."""
        if self.auth_method == CREDENTIAL and self.credential_id:
            return ("credential", self.credential_id)
        if self.auth_method == OAUTH and self.oauth_client_id:
            return ("oauth_client", self.oauth_client_id)
        return None

    @classmethod
    def for_scopes(
        cls,
        scopes: list[str] | tuple[str, ...],
        *,
        oauth_client_id: str,
        oauth_authorization_id: str,
        client_registration: str | None = None,
    ) -> AuthContext:
        return cls(
            auth_method=OAUTH,
            ceiling=frozenset(
                SCOPE_TO_PERMISSION[s] for s in scopes if s in SCOPE_TO_PERMISSION
            ),
            oauth_client_id=oauth_client_id,
            oauth_authorization_id=oauth_authorization_id,
            scopes=tuple(scopes),
            client_registration=client_registration,
        )


DEFAULT_AUTH = AuthContext()
