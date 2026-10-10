"""The OAuth authorization service (MAK-0008 §5–§8), independent of HTTP.

`web.py` turns requests into calls on `OAuthService`; tests and the CLI call
it directly. Every secret is a random value from `secrets` (≥ 256 bits for
tokens) and is persisted only as its SHA-256 hash. PKCE uses the standard
library's SHA-256 and base64url; no cryptographic primitive is implemented
here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit

from mak4i.audit import AuditLogger
from mak4i.identity.control_plane import ControlPlane
from mak4i.identity.errors import (
    AccessDeniedError,
    CredentialInvalidError,
    PrincipalNotFoundError,
)
from mak4i.identity.models import Principal, utc_now
from mak4i.identity.tokens import hash_token
from mak4i.oauth import cimd
from mak4i.oauth.errors import NonRedirectableError, OAuthError
from mak4i.oauth.settings import DEFAULT_SCOPES, SCOPES, OAuthSettings
from mak4i.oauth.store import SqlOAuthStore

ACCESS_TOKEN_PREFIX = "mak4at_"
REFRESH_TOKEN_PREFIX = "mak4rt_"
CODE_PREFIX = "mak4ac_"
CLIENT_SECRET_PREFIX = "mak4cs_"
"""Distinct from the principal-credential prefix `mak4i_`, so a presented
bearer token is verified only as the kind it claims to be (MAK-0008 §2.2)."""

_SIGN_IN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
_SIGN_IN_LENGTH = 28  # 28 × 5 bits = 140 bits of entropy (≥ 128, §5.3.1)
_PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9\-_]{43}$")
_LOOPBACK_REDIRECT_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _random_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _h(value: str) -> str:
    return hash_token(value)


def format_sign_in_code(raw: str) -> str:
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def normalize_sign_in_code(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "").upper()


def pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_uri_matches(requested: str, registered: list[str]) -> bool:
    """MAK-0008 §5.2: exact string match, except that a registered http
    loopback URI matches any port with identical scheme, host and path
    (RFC 8252 §7.3). Never a prefix or wildcard match."""
    if requested in registered:
        return True
    req = urlsplit(requested)
    if req.scheme != "http" or req.fragment:
        return False
    req_host = req.netloc.rsplit(":", 1)[0] if not req.netloc.startswith("[") else req.netloc.split("]")[0] + "]"
    if req_host not in _LOOPBACK_REDIRECT_HOSTS:
        return False
    for uri in registered:
        reg = urlsplit(uri)
        if reg.scheme != "http":
            continue
        reg_host = reg.netloc.rsplit(":", 1)[0] if not reg.netloc.startswith("[") else reg.netloc.split("]")[0] + "]"
        if reg_host == req_host and reg.path == req.path and reg.query == req.query:
            return True
    return False


def valid_redirect_uri(uri: str) -> bool:
    """§5.2.2: https, or http on a loopback host; no fragment or wildcard."""
    parts = urlsplit(uri)
    if parts.fragment or "*" in uri or not parts.netloc:
        return False
    if parts.scheme == "https":
        return True
    host = parts.netloc.rsplit(":", 1)[0] if not parts.netloc.startswith("[") else parts.netloc.split("]")[0] + "]"
    return parts.scheme == "http" and host in _LOOPBACK_REDIRECT_HOSTS


@dataclass(frozen=True)
class ValidatedAccessToken:
    principal: Principal
    scopes: tuple[str, ...]
    client_id: str
    authorization_id: str


@dataclass(frozen=True)
class AuthorizationRequest:
    client_id: str
    client_name: str | None
    client_kind: str
    redirect_uri: str
    state: str | None
    code_challenge: str
    scopes: tuple[str, ...]
    resource: str

    def as_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "client_name": self.client_name,
            "client_kind": self.client_kind,
            "redirect_uri": self.redirect_uri,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "scopes": list(self.scopes),
            "resource": self.resource,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AuthorizationRequest:
        return cls(**{**data, "scopes": tuple(data["scopes"])})


class InvalidAccessToken(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class OAuthService:
    def __init__(
        self,
        *,
        settings: OAuthSettings,
        store: SqlOAuthStore,
        control_plane: ControlPlane,
        audit: AuditLogger | None = None,
        clock: Callable[[], datetime] = utc_now,
        fetch_client_document: Callable[[str], cimd.FetchedDocument] = cimd.fetch_document,
    ):
        self.settings = settings
        self._store = store
        self._cp = control_plane
        self._audit = audit
        self._now = clock
        self._fetch = fetch_client_document

    # -- audit ----------------------------------------------------------------

    def _log(self, event: str, *, actor: str = "unauthenticated", **fields) -> None:
        if self._audit is not None:
            self._audit.log(event, correlation_id=str(uuid.uuid4()), actor=actor, **fields)

    # -- metadata (MAK-0008 §4) ----------------------------------------------

    def protected_resource_metadata(self) -> dict:
        return {
            "resource": self.settings.resource,
            "authorization_servers": [self.settings.issuer],
            "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": self.settings.resource_name,
        }

    def authorization_server_metadata(self) -> dict:
        from mak4i.oauth import settings as s

        metadata = {
            "issuer": self.settings.issuer,
            "authorization_endpoint": self.settings.endpoint(s.AUTHORIZE_PATH),
            "token_endpoint": self.settings.endpoint(s.TOKEN_PATH),
            "revocation_endpoint": self.settings.endpoint(s.REVOKE_PATH),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "none",
                "client_secret_post",
                "client_secret_basic",
            ],
            "revocation_endpoint_auth_methods_supported": [
                "none",
                "client_secret_post",
                "client_secret_basic",
            ],
            "scopes_supported": list(SCOPES),
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": self.settings.enable_cimd,
        }
        if self.settings.enable_dcr:
            metadata["registration_endpoint"] = self.settings.endpoint(s.REGISTER_PATH)
        return metadata

    # -- clients (MAK-0008 §8) ------------------------------------------------

    def resolve_client(self, client_id: str) -> dict | None:
        """A usable client record, or `None` if the client is unknown,
        disabled, or its kind is not enabled."""
        if not client_id:
            return None
        if client_id.startswith("https://"):
            if not self.settings.enable_cimd:
                return None
            return self._resolve_cimd_client(client_id)
        client = self._store.get_client(client_id)
        if client is None or client["status"] != "active":
            return None
        if client["kind"] == "dcr" and not self.settings.enable_dcr:
            return None
        if client["kind"] == "cimd":
            return None
        return client

    def _resolve_cimd_client(self, client_id: str) -> dict:
        allowed = self.settings.cimd_allowed_hosts
        host = (urlsplit(client_id).hostname or "").lower()
        if allowed and host not in allowed:
            raise NonRedirectableError(
                "invalid_client", "this server does not accept clients from that host"
            )
        now = self._now()
        cached = self._store.get_client(client_id)
        if cached is not None and cached["status"] != "active":
            return None  # disabled by an owner
        if cached is not None and cached["cache_expires_at"] and cached["cache_expires_at"] > now:
            return cached
        try:
            fetched = self._fetch(client_id)
            parsed = cimd.parse_document(client_id, fetched.body)
        except cimd.ClientMetadataError as exc:
            raise NonRedirectableError("invalid_client", str(exc)) from exc
        for uri in parsed["redirect_uris"]:
            if not valid_redirect_uri(uri):
                raise NonRedirectableError(
                    "invalid_client", "client metadata document lists an invalid redirect URI"
                )
        max_age = min(fetched.max_age or cimd.DEFAULT_CACHE_SECONDS, cimd.DEFAULT_CACHE_SECONDS)
        record = {
            "client_id": client_id,
            "kind": "cimd",
            "organization_id": None,
            "client_name": parsed["client_name"],
            "redirect_uris": parsed["redirect_uris"],
            "token_endpoint_auth_method": "none",
            "client_secret_hash": None,
            "status": "active",
            "created_by": None,
            "fetched_at": now,
            "cache_expires_at": now + timedelta(seconds=max_age),
            "created_at": cached["created_at"] if cached else now,
            "updated_at": now,
        }
        self._store.put_client(record)
        return record

    def register_client(
        self,
        *,
        actor: Principal,
        organization_id: str,
        client_name: str,
        redirect_uris: list[str],
        confidential: bool = False,
    ) -> tuple[dict, str | None]:
        """Pre-register a client for one organization (§8.2). Returns
        `(client, secret_or_None)`; the secret is shown once only."""
        self._cp.require_owner(actor, organization_id)
        if not redirect_uris or not all(valid_redirect_uri(u) for u in redirect_uris):
            raise ValueError("redirect URIs must be https, or http on a loopback host")
        secret = _random_token(CLIENT_SECRET_PREFIX) if confidential else None
        now = self._now()
        client = {
            "client_id": "mak4c_" + uuid.uuid4().hex,
            "kind": "pre_registered",
            "organization_id": organization_id,
            "client_name": client_name,
            "redirect_uris": list(redirect_uris),
            "token_endpoint_auth_method": "client_secret_basic" if confidential else "none",
            "client_secret_hash": _h(secret) if secret else None,
            "status": "active",
            "created_by": actor.principal_id,
            "fetched_at": None,
            "cache_expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._store.put_client(client)
        self._cp.record_admin_event(
            action="oauth_client.register", actor=actor, organization_id=organization_id,
            targets={"client_id": client["client_id"], "confidential": str(confidential).lower()},
        )
        self._log(
            "OAUTH_CLIENT_REGISTERED",
            actor=actor.principal_id,
            client_id=client["client_id"],
            organization_id=organization_id,
            confidential=confidential,
        )
        return client, secret

    def register_dynamic_client(self, metadata: dict) -> dict:
        """RFC 7591 registration of a public client (§8.4)."""
        if not self.settings.enable_dcr:
            raise OAuthError("invalid_request", "dynamic client registration is disabled", status_code=404)
        if not isinstance(metadata, dict):
            raise OAuthError("invalid_client_metadata", "body must be a JSON object")
        redirect_uris = metadata.get("redirect_uris")
        if (
            not isinstance(redirect_uris, list)
            or not redirect_uris
            or not all(isinstance(u, str) and valid_redirect_uri(u) for u in redirect_uris)
        ):
            raise OAuthError(
                "invalid_redirect_uri",
                "redirect_uris must be https, or http on a loopback host",
            )
        method = metadata.get("token_endpoint_auth_method", "none")
        if method != "none":
            raise OAuthError(
                "invalid_client_metadata", "only public clients (token_endpoint_auth_method 'none') may register"
            )
        grant_types = metadata.get("grant_types", ["authorization_code", "refresh_token"])
        if not isinstance(grant_types, list) or "authorization_code" not in grant_types:
            raise OAuthError("invalid_client_metadata", "grant_types must include authorization_code")
        name = metadata.get("client_name")
        now = self._now()
        client = {
            "client_id": "mak4dcr_" + uuid.uuid4().hex,
            "kind": "dcr",
            "organization_id": None,
            "client_name": name if isinstance(name, str) and name.strip() else None,
            "redirect_uris": list(redirect_uris),
            "token_endpoint_auth_method": "none",
            "client_secret_hash": None,
            "status": "active",
            "created_by": None,
            "fetched_at": None,
            "cache_expires_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._store.put_client(client)
        self._log("OAUTH_CLIENT_REGISTERED", client_id=client["client_id"], kind="dcr")
        return {
            "client_id": client["client_id"],
            "client_id_issued_at": int(now.timestamp()),
            "client_name": client["client_name"],
            "redirect_uris": client["redirect_uris"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    def list_clients(self, *, actor: Principal, organization_id: str) -> list[dict]:
        self._cp.require_owner(actor, organization_id)
        return [_public_client(c) for c in self._store.list_clients(organization_id)]

    def disable_client(self, *, actor: Principal, client_id: str) -> dict:
        client = self._store.get_client(client_id)
        if client is None or client["organization_id"] is None:
            raise AccessDeniedError(principal_id=actor.principal_id, permission="administer")
        self._cp.require_owner(actor, client["organization_id"])
        now = self._now()
        self._store.put_client({**client, "status": "disabled", "updated_at": now})
        for authorization in self._store.list_authorizations(
            organization_id=client["organization_id"], client_id=client_id
        ):
            self._store.revoke_authorization(authorization["authorization_id"], "client_disabled", now)
        self._cp.record_admin_event(
            action="oauth_client.disable", actor=actor, organization_id=client["organization_id"],
            targets={"client_id": client_id},
        )
        self._log("OAUTH_CLIENT_DISABLED", actor=actor.principal_id, client_id=client_id)
        return _public_client({**client, "status": "disabled"})

    # -- sign-in codes (§5.3) ---------------------------------------------------

    def issue_sign_in_code(self, *, actor: Principal, principal_id: str) -> tuple[str, datetime]:
        """For the principal itself, or for an owner of its organization.
        Returns `(code, expires_at)`; the code is shown once."""
        target = self._cp.get_principal(principal_id)
        if target is None:
            raise PrincipalNotFoundError(principal_id)
        if actor.principal_id != target.principal_id:
            self._cp.require_owner(actor, target.organization_id)
        self._cp.active_principal(principal_id)  # deactivated principals get none
        raw = "".join(secrets.choice(_SIGN_IN_ALPHABET) for _ in range(_SIGN_IN_LENGTH))
        now = self._now()
        expires_at = now + timedelta(seconds=self.settings.sign_in_code_ttl)
        self._store.insert_sign_in_code(
            {
                "code_hash": _h(raw),
                "principal_id": principal_id,
                "issued_by": actor.principal_id,
                "created_at": now,
                "expires_at": expires_at,
                "used_at": None,
            }
        )
        self._cp.record_admin_event(
            action="oauth_sign_in_code.issue", actor=actor, organization_id=target.organization_id,
            targets={"principal_id": principal_id},
        )
        self._log(
            "OAUTH_SIGN_IN_CODE_ISSUED",
            actor=actor.principal_id,
            principal_id=principal_id,
            expires_at=expires_at.isoformat(),
        )
        return format_sign_in_code(raw), expires_at

    def sign_in(self, code: str) -> Principal:
        """Consume a sign-in code (single use) and return its principal.
        Raises `OAuthError('access_denied')` without saying why."""
        raw = normalize_sign_in_code(code)
        now = self._now()
        row = self._store.consume_sign_in_code(_h(raw), now) if raw else None
        if row is None or row["expires_at"] <= now:
            self._log("OAUTH_SIGN_IN", outcome="failed")
            raise OAuthError("access_denied", "the sign-in code is invalid, expired or already used")
        try:
            principal = self._cp.active_principal(row["principal_id"])
        except CredentialInvalidError as exc:
            self._log("OAUTH_SIGN_IN", outcome="failed", reason=exc.reason)
            raise OAuthError("access_denied", "the sign-in code is invalid, expired or already used") from exc
        self._log("OAUTH_SIGN_IN", actor=principal.principal_id, outcome="succeeded")
        return principal

    # -- authorization requests (§5.1) -----------------------------------------

    def validate_authorization_request(self, params: dict) -> AuthorizationRequest:
        client_id = params.get("client_id") or ""
        client = self.resolve_client(client_id)
        if client is None:
            raise NonRedirectableError("invalid_client", "unknown or disabled client")
        redirect_uri = params.get("redirect_uri") or ""
        if not redirect_uri or not redirect_uri_matches(redirect_uri, client["redirect_uris"]):
            raise NonRedirectableError(
                "invalid_request", "redirect_uri is missing or not registered for this client"
            )
        state = params.get("state")
        if params.get("response_type") != "code":
            raise OAuthError("unsupported_response_type", "response_type must be 'code'")
        challenge = params.get("code_challenge") or ""
        if params.get("code_challenge_method") != "S256" or not _PKCE_CHALLENGE_RE.match(challenge):
            raise OAuthError("invalid_request", "PKCE with code_challenge_method=S256 is required")
        if (params.get("resource") or "").rstrip("/") != self.settings.resource:
            raise OAuthError("invalid_target", "resource must be this MCP server's resource identifier")
        requested = (params.get("scope") or "").split()
        if any(scope not in SCOPES for scope in requested):
            raise OAuthError("invalid_scope", "unsupported scope requested")
        scopes = tuple(dict.fromkeys(requested)) or DEFAULT_SCOPES
        return AuthorizationRequest(
            client_id=client["client_id"],
            client_name=client.get("client_name"),
            client_kind=client["kind"],
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
            scopes=scopes,
            resource=self.settings.resource,
        )

    def error_redirect(self, request_params: dict, error: OAuthError) -> str:
        """Redirect URL for an error after client + redirect were validated."""
        query = {"error": error.error, "error_description": error.description, "iss": self.settings.issuer}
        if request_params.get("state"):
            query["state"] = request_params["state"]
        return _append_query(request_params["redirect_uri"], query)

    def approve(self, principal: Principal, request: AuthorizationRequest) -> str:
        """Create an authorization and a code; return the redirect URL with
        `code`, `state` and `iss` (§5.6)."""
        principal = self._cp.active_principal(principal.principal_id)
        client = self.resolve_client(request.client_id)
        if client is None:
            raise NonRedirectableError("invalid_client", "unknown or disabled client")
        if client["organization_id"] is not None and client["organization_id"] != principal.organization_id:
            raise OAuthError("access_denied", "this client is not registered for your organization")
        now = self._now()
        authorization_id = "oaz_" + uuid.uuid4().hex
        self._store.insert_authorization(
            {
                "authorization_id": authorization_id,
                "principal_id": principal.principal_id,
                "client_id": request.client_id,
                "scopes": list(request.scopes),
                "resource": request.resource,
                "status": "active",
                "revoked_reason": None,
                "created_at": now,
                "last_used_at": now,
                "absolute_expires_at": now + timedelta(seconds=self.settings.refresh_absolute_ttl),
                "revoked_at": None,
            }
        )
        code = _random_token(CODE_PREFIX)
        self._store.insert_code(
            {
                "code_hash": _h(code),
                "authorization_id": authorization_id,
                "client_id": request.client_id,
                "redirect_uri": request.redirect_uri,
                "code_challenge": request.code_challenge,
                "resource": request.resource,
                "scopes": list(request.scopes),
                "created_at": now,
                "expires_at": now + timedelta(seconds=self.settings.code_ttl),
                "used_at": None,
            }
        )
        self._log(
            "OAUTH_AUTHORIZATION_GRANTED",
            actor=principal.principal_id,
            authorization_id=authorization_id,
            client_id=request.client_id,
            scopes=list(request.scopes),
        )
        query = {"code": code, "iss": self.settings.issuer}
        if request.state:
            query["state"] = request.state
        return _append_query(request.redirect_uri, query)

    def deny_redirect(self, principal: Principal | None, request: AuthorizationRequest) -> str:
        self._log(
            "OAUTH_AUTHORIZATION_DENIED",
            actor=principal.principal_id if principal else "unauthenticated",
            client_id=request.client_id,
        )
        query = {"error": "access_denied", "iss": self.settings.issuer}
        if request.state:
            query["state"] = request.state
        return _append_query(request.redirect_uri, query)

    # -- token endpoint (§6) ------------------------------------------------------

    def authenticate_client(
        self, form: dict, basic: tuple[str, str] | None
    ) -> dict:
        client_id = form.get("client_id") or (basic[0] if basic else "")
        if basic and form.get("client_id") and form["client_id"] != basic[0]:
            raise OAuthError("invalid_client", "conflicting client identification", status_code=401)
        client = self.resolve_client(client_id)
        if client is None:
            raise OAuthError("invalid_client", "unknown or disabled client", status_code=401)
        if client["token_endpoint_auth_method"] == "none":
            if form.get("client_secret") or (basic and basic[1]):
                raise OAuthError("invalid_client", "public clients must not send a secret", status_code=401)
            return client
        secret = form.get("client_secret") or (basic[1] if basic else "")
        if not secret or not client["client_secret_hash"] or not hmac.compare_digest(
            _h(secret), client["client_secret_hash"]
        ):
            raise OAuthError("invalid_client", "client authentication failed", status_code=401)
        return client

    def exchange_code(self, form: dict, client: dict) -> dict:
        code = form.get("code") or ""
        now = self._now()
        outcome, row = self._store.consume_code(_h(code), now) if code else ("unknown", None)
        if outcome == "unknown":
            raise self._token_rejected("invalid_grant", "authorization code is invalid")
        if outcome == "reused":
            # RFC 6749 §4.1.2 / MAK-0008 §6.2: revoke everything the code produced.
            self._store.revoke_authorization(row["authorization_id"], "code_reuse", now)
            self._log("OAUTH_CODE_REUSE", authorization_id=row["authorization_id"], client_id=row["client_id"])
            raise self._token_rejected("invalid_grant", "authorization code was already used")
        if row["expires_at"] <= now:
            raise self._token_rejected("invalid_grant", "authorization code has expired")
        if row["client_id"] != client["client_id"]:
            raise self._token_rejected("invalid_grant", "authorization code was issued to another client")
        if form.get("redirect_uri") != row["redirect_uri"]:
            raise self._token_rejected("invalid_grant", "redirect_uri does not match the authorization request")
        verifier = form.get("code_verifier") or ""
        if not _PKCE_VERIFIER_RE.match(verifier) or not hmac.compare_digest(
            pkce_s256(verifier), row["code_challenge"]
        ):
            raise self._token_rejected("invalid_grant", "PKCE verification failed")
        resource = form.get("resource")
        if resource is not None and resource.rstrip("/") != row["resource"]:
            raise self._token_rejected("invalid_target", "resource does not match the authorization")
        authorization = self._store.get_authorization(row["authorization_id"])
        if authorization is None or authorization["status"] != "active":
            raise self._token_rejected("invalid_grant", "authorization is no longer valid")
        principal = self._active_or_reject(authorization)
        return self._issue_tokens(authorization, tuple(row["scopes"]), principal, now)

    def refresh(self, form: dict, client: dict) -> dict:
        raw = form.get("refresh_token") or ""
        now = self._now()
        token = self._store.get_token(_h(raw)) if raw.startswith(REFRESH_TOKEN_PREFIX) else None
        if token is None or token["kind"] != "refresh":
            raise self._token_rejected("invalid_grant", "refresh token is invalid")
        authorization = self._store.get_authorization(token["authorization_id"])
        if authorization is None or authorization["client_id"] != client["client_id"]:
            raise self._token_rejected("invalid_grant", "refresh token is invalid")
        if token["status"] == "rotated":
            self._replay(authorization, now)
        if token["status"] != "active" or authorization["status"] != "active":
            raise self._token_rejected("invalid_grant", "refresh token is no longer valid")
        idle_deadline = authorization["last_used_at"] + timedelta(seconds=self.settings.refresh_idle_ttl)
        if token["expires_at"] <= now or authorization["absolute_expires_at"] <= now or idle_deadline <= now:
            self._store.revoke_authorization(authorization["authorization_id"], "expired", now)
            raise self._token_rejected("invalid_grant", "refresh token has expired; sign in again")
        resource = form.get("resource")
        if resource is not None and resource.rstrip("/") != authorization["resource"]:
            raise self._token_rejected("invalid_target", "resource does not match the authorization")
        granted = tuple(token["scopes"])
        requested = tuple((form.get("scope") or "").split()) or granted
        if any(scope not in granted for scope in requested):
            raise self._token_rejected("invalid_scope", "a refresh may narrow but not widen scope")
        principal = self._active_or_reject(authorization)
        if not self._store.rotate_refresh_token(token["token_hash"]):
            self._replay(authorization, now)  # lost a concurrent rotation race
        self._store.touch_authorization(authorization["authorization_id"], now)
        return self._issue_tokens(authorization, requested, principal, now)

    def revoke(self, form: dict, client: dict) -> None:
        """RFC 7009 (§6.7). Always succeeds from the caller's view."""
        raw = form.get("token") or ""
        token = self._store.get_token(_h(raw)) if raw else None
        if token is None:
            return
        authorization = self._store.get_authorization(token["authorization_id"])
        if authorization is None or authorization["client_id"] != client["client_id"]:
            return
        now = self._now()
        if token["kind"] == "refresh":
            self._store.revoke_authorization(authorization["authorization_id"], "revoked_by_client", now)
        else:
            self._store.revoke_token(token["token_hash"])
        self._log(
            "OAUTH_REVOKED",
            authorization_id=authorization["authorization_id"],
            kind=token["kind"],
            by="client",
        )

    # -- resource server (§6.6) -------------------------------------------------

    def validate_access_token(self, raw: str) -> ValidatedAccessToken:
        if not raw.startswith(ACCESS_TOKEN_PREFIX):
            raise InvalidAccessToken("unknown")
        token = self._store.get_token(_h(raw))
        now = self._now()
        if token is None or token["kind"] != "access":
            raise InvalidAccessToken("unknown")
        if token["status"] != "active":
            raise InvalidAccessToken("revoked")
        if token["expires_at"] <= now:
            raise InvalidAccessToken("expired")
        authorization = self._store.get_authorization(token["authorization_id"])
        if authorization is None or authorization["status"] != "active":
            raise InvalidAccessToken("revoked")
        if authorization["absolute_expires_at"] <= now:
            raise InvalidAccessToken("expired")
        if authorization["resource"] != self.settings.resource:
            raise InvalidAccessToken("wrong_audience")
        client = self._store.get_client(authorization["client_id"])
        if client is not None and client["status"] != "active":
            raise InvalidAccessToken("client_disabled")
        try:
            principal = self._cp.active_principal(authorization["principal_id"])
        except CredentialInvalidError as exc:
            raise InvalidAccessToken(exc.reason) from exc
        return ValidatedAccessToken(
            principal=principal,
            scopes=tuple(token["scopes"]),
            client_id=authorization["client_id"],
            authorization_id=authorization["authorization_id"],
        )

    # -- administration (§8.5) ---------------------------------------------------

    def list_authorizations(
        self, *, actor: Principal, principal_id: str | None = None, client_id: str | None = None
    ) -> list[dict]:
        organization_id = actor.organization_id
        if principal_id is not None:
            target = self._cp.get_principal(principal_id)
            if target is None:
                raise PrincipalNotFoundError(principal_id)
            organization_id = target.organization_id
        self._cp.require_owner(actor, organization_id)
        rows = self._store.list_authorizations(
            organization_id=organization_id, principal_id=principal_id, client_id=client_id
        )
        return [_public_authorization(r) for r in rows]

    def revoke_authorization(self, *, actor: Principal, authorization_id: str) -> dict:
        authorization = self._store.get_authorization(authorization_id)
        if authorization is None:
            raise AccessDeniedError(principal_id=actor.principal_id, permission="administer")
        target = self._cp.get_principal(authorization["principal_id"])
        if target is None:
            raise AccessDeniedError(principal_id=actor.principal_id, permission="administer")
        if actor.principal_id != target.principal_id:
            self._cp.require_owner(actor, target.organization_id)
        self._store.revoke_authorization(authorization_id, "revoked_by_admin", self._now())
        self._cp.record_admin_event(
            action="oauth_authorization.revoke", actor=actor, organization_id=target.organization_id,
            targets={"authorization_id": authorization_id},
        )
        self._log("OAUTH_REVOKED", actor=actor.principal_id, authorization_id=authorization_id, by="admin")
        return _public_authorization(self._store.get_authorization(authorization_id))

    def revoke_all(
        self,
        *,
        actor: Principal,
        principal_id: str | None = None,
        client_id: str | None = None,
        reason: str = "revoked_by_admin",
    ) -> int:
        """Revoke every active authorization of a principal and/or client
        within the actor's organization. Returns how many were revoked."""
        revoked = 0
        now = self._now()
        for row in self.list_authorizations(actor=actor, principal_id=principal_id, client_id=client_id):
            if row["status"] == "active" and self._store.revoke_authorization(
                row["authorization_id"], reason, now
            ):
                revoked += 1
        self._cp.record_admin_event(
            action="oauth_authorization.revoke_all", actor=actor, organization_id=actor.organization_id,
            targets={"principal_id": principal_id, "client_id": client_id, "count": str(revoked)},
        )
        self._log(
            "OAUTH_REVOKED",
            actor=actor.principal_id,
            principal_id=principal_id,
            client_id=client_id,
            count=revoked,
            by="admin",
        )
        return revoked

    def revoke_principal_on_deactivation(self, principal_id: str) -> int:
        """MAK-0006 §6.3: deactivating a principal revokes its OAuth
        authorizations (called by the control-plane deactivation flow)."""
        target = self._cp.get_principal(principal_id)
        if target is None:
            return 0
        now = self._now()
        count = 0
        for row in self._store.list_authorizations(
            organization_id=target.organization_id, principal_id=principal_id
        ):
            if row["status"] == "active" and self._store.revoke_authorization(
                row["authorization_id"], "principal_deactivated", now
            ):
                count += 1
        return count

    # -- internals -------------------------------------------------------------

    def _token_rejected(self, error: str, description: str) -> OAuthError:
        self._log("OAUTH_TOKEN_REJECTED", error=error)
        return OAuthError(error, description)

    def _replay(self, authorization: dict, now: datetime) -> None:
        self._store.revoke_authorization(authorization["authorization_id"], "refresh_replay", now)
        self._log(
            "OAUTH_REFRESH_REPLAY",
            authorization_id=authorization["authorization_id"],
            client_id=authorization["client_id"],
        )
        raise self._token_rejected("invalid_grant", "refresh token was already used; sign in again")

    def _active_or_reject(self, authorization: dict) -> Principal:
        try:
            return self._cp.active_principal(authorization["principal_id"])
        except CredentialInvalidError as exc:
            self._store.revoke_authorization(authorization["authorization_id"], exc.reason, self._now())
            raise self._token_rejected("invalid_grant", "the signed-in principal can no longer act") from exc

    def _issue_tokens(
        self, authorization: dict, scopes: tuple[str, ...], principal: Principal, now: datetime
    ) -> dict:
        access = _random_token(ACCESS_TOKEN_PREFIX)
        refresh = _random_token(REFRESH_TOKEN_PREFIX)
        access_expires = now + timedelta(seconds=self.settings.access_token_ttl)
        refresh_expires = min(
            now + timedelta(seconds=self.settings.refresh_idle_ttl),
            authorization["absolute_expires_at"],
        )
        for raw, kind, expires in (
            (access, "access", access_expires),
            (refresh, "refresh", refresh_expires),
        ):
            self._store.insert_token(
                {
                    "token_hash": _h(raw),
                    "kind": kind,
                    "authorization_id": authorization["authorization_id"],
                    "scopes": list(scopes),
                    "status": "active",
                    "created_at": now,
                    "expires_at": expires,
                }
            )
        self._log(
            "OAUTH_TOKEN_ISSUED",
            actor=principal.principal_id,
            authorization_id=authorization["authorization_id"],
            client_id=authorization["client_id"],
            scopes=list(scopes),
        )
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.settings.access_token_ttl,
            "refresh_token": refresh,
            "scope": " ".join(scopes),
        }


def _append_query(uri: str, params: dict) -> str:
    separator = "&" if urlsplit(uri).query else "?"
    return uri + separator + urlencode(params)


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _public_client(client: dict) -> dict:
    return {
        k: _iso(client.get(k))
        for k in (
            "client_id",
            "kind",
            "organization_id",
            "client_name",
            "redirect_uris",
            "token_endpoint_auth_method",
            "status",
            "created_by",
            "created_at",
        )
    }


def _public_authorization(row: dict) -> dict:
    return {
        k: _iso(row.get(k))
        for k in (
            "authorization_id",
            "principal_id",
            "client_id",
            "scopes",
            "resource",
            "status",
            "revoked_reason",
            "created_at",
            "last_used_at",
            "absolute_expires_at",
            "revoked_at",
        )
    }
