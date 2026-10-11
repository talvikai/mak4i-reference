from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

SCOPES = ("mak4i:read", "mak4i:write", "mak4i:resolve")
DEFAULT_SCOPES = ("mak4i:read", "mak4i:write")
"""MAK-0008 §7.2: granted when an authorization request names no scope."""

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

# Internal (application) paths. Advertised URLs are the issuer + these.
AUTHORIZE_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
REVOKE_PATH = "/oauth/revoke"
REGISTER_PATH = "/oauth/register"
SIGN_IN_PATH = "/oauth/sign-in"
CONSENT_PATH = "/oauth/consent"
PRM_PREFIX = "/.well-known/oauth-protected-resource"
ASM_PREFIX = "/.well-known/oauth-authorization-server"


class OAuthConfigError(ValueError):
    """The OAuth configuration is invalid; the server must not start."""


def _int_env(env, name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = env.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise OAuthConfigError(f"{name} must be an integer (seconds)") from exc
    if not minimum <= value <= maximum:
        raise OAuthConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


def _flag(env, name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw in (None, ""):
        return default
    if raw.lower() in ("1", "true", "yes", "on"):
        return True
    if raw.lower() in ("0", "false", "no", "off"):
        return False
    raise OAuthConfigError(f"{name} must be 1 or 0")


def _check_url(name: str, url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise OAuthConfigError(f"{name} must be an absolute https URL")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
        raise OAuthConfigError(f"{name} must use https (http is allowed only on loopback)")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise OAuthConfigError(f"{name} must not contain a query, fragment or credentials")
    if parts.path.endswith("/"):
        raise OAuthConfigError(f"{name} must not end with '/'")


@dataclass(frozen=True)
class OAuthSettings:
    """MAK-0008 §3 and §12. Built from deployment configuration only —
    never from request headers (§3.3)."""

    resource: str
    issuer: str
    enable_cimd: bool = True
    enable_dcr: bool = False
    cimd_allowed_hosts: tuple[str, ...] = ()
    code_ttl: int = 60
    access_token_ttl: int = 3600
    refresh_idle_ttl: int = 7 * 86400
    refresh_absolute_ttl: int = 30 * 86400
    sign_in_code_ttl: int = 600
    browser_session_ttl: int = 900
    resource_name: str = "MAK4I"
    rate_limit_per_minute: int = 20
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_url("MAK4I_PUBLIC_ENDPOINT", self.resource)
        _check_url("MAK4I_OAUTH_ISSUER", self.issuer)
        r, i = urlsplit(self.resource), urlsplit(self.issuer)
        if (r.scheme, r.netloc) != (i.scheme, i.netloc):
            raise OAuthConfigError(
                "MAK4I_OAUTH_ISSUER must have the same origin as MAK4I_PUBLIC_ENDPOINT "
                "(this server is both resource server and authorization server)"
            )

    # -- derived values -------------------------------------------------------

    @property
    def origin(self) -> str:
        parts = urlsplit(self.resource)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def resource_path(self) -> str:
        return urlsplit(self.resource).path

    @property
    def issuer_path(self) -> str:
        return urlsplit(self.issuer).path

    @property
    def secure_cookies(self) -> bool:
        return urlsplit(self.issuer).scheme == "https"

    def endpoint(self, path: str) -> str:
        """Advertised URL of an internal OAuth path (MAK-0008 §3.4)."""
        return self.issuer + path

    @property
    def protected_resource_metadata_url(self) -> str:
        return self.origin + PRM_PREFIX + self.resource_path

    @property
    def protected_resource_metadata_paths(self) -> tuple[str, ...]:
        # Path-inserted (required) and root (fallback) locations, §4.1.
        return (PRM_PREFIX + self.resource_path, PRM_PREFIX)

    @property
    def authorization_server_metadata_path(self) -> str:
        return ASM_PREFIX + self.issuer_path

    @classmethod
    def from_env(cls, env: dict | None = None) -> OAuthSettings | None:
        """`None` when OAuth is disabled (`MAK4I_OAUTH_ENABLED` unset/0)."""
        env = os.environ if env is None else env
        if not _flag(env, "MAK4I_OAUTH_ENABLED", False):
            return None
        resource = env.get("MAK4I_PUBLIC_ENDPOINT") or ""
        if not resource:
            raise OAuthConfigError(
                "MAK4I_OAUTH_ENABLED=1 requires MAK4I_PUBLIC_ENDPOINT "
                "(the public https URL of the MCP endpoint, e.g. https://mak4i.example.com/mcp)"
            )
        issuer = env.get("MAK4I_OAUTH_ISSUER") or default_issuer(resource)
        hosts = tuple(
            h.strip().lower()
            for h in (env.get("MAK4I_OAUTH_CIMD_ALLOWED_HOSTS") or "").split(",")
            if h.strip()
        )
        return cls(
            resource=resource,
            issuer=issuer,
            enable_cimd=_flag(env, "MAK4I_OAUTH_CIMD", True),
            enable_dcr=_flag(env, "MAK4I_OAUTH_DCR", False),
            cimd_allowed_hosts=hosts,
            code_ttl=_int_env(env, "MAK4I_OAUTH_CODE_TTL", 60, minimum=10, maximum=600),
            access_token_ttl=_int_env(
                env, "MAK4I_OAUTH_ACCESS_TOKEN_TTL", 3600, minimum=60, maximum=86400
            ),
            refresh_idle_ttl=_int_env(
                env, "MAK4I_OAUTH_REFRESH_IDLE_TTL", 7 * 86400, minimum=300, maximum=90 * 86400
            ),
            refresh_absolute_ttl=_int_env(
                env,
                "MAK4I_OAUTH_REFRESH_ABSOLUTE_TTL",
                30 * 86400,
                minimum=300,
                maximum=365 * 86400,
            ),
            sign_in_code_ttl=_int_env(
                env, "MAK4I_OAUTH_SIGN_IN_CODE_TTL", 600, minimum=60, maximum=600
            ),
            resource_name=env.get("MAK4I_INSTANCE_NAME") or "MAK4I",
            rate_limit_per_minute=_int_env(
                env, "MAK4I_OAUTH_RATE_LIMIT_PER_MINUTE", 20, minimum=1, maximum=10000
            ),
        )


def default_issuer(resource: str) -> str:
    """The resource URL without its final `/mcp` segment: an MCP endpoint at
    `https://h/team-a/mcp` gets issuer `https://h/team-a`, one at
    `https://h/mcp` gets `https://h`."""
    parts = urlsplit(resource)
    path = parts.path
    if path.endswith("/mcp"):
        path = path[: -len("/mcp")]
    return f"{parts.scheme}://{parts.netloc}{path}"
