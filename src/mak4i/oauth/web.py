"""HTTP endpoints of the OAuth authorization service (MAK-0008 §4–§8).

Minimal browser pages for sign-in and consent are part of OAuth, not a
management portal: they show only what the resource owner needs to decide.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import html
import json
import secrets
from datetime import timedelta
from urllib.parse import unquote, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from mak4i.identity.tokens import hash_token
from mak4i.oauth import settings as s
from mak4i.oauth.errors import NonRedirectableError, OAuthError
from mak4i.oauth.ratelimit import RateLimiter
from mak4i.oauth.service import AuthorizationRequest, OAuthService

COOKIE_NAME = "mak4i_oauth_session"

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
_JSON_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}

_SCOPE_TEXT = {
    "mak4i:read": "Read project knowledge on projects you can read",
    "mak4i:write": "Create and supersede project knowledge on projects you can write",
    "mak4i:resolve": "Resolve conflicts on projects where you may resolve them",
}


def _page(title: str, body: str, *, status_code: int = 200) -> HTMLResponse:
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:34rem;margin:3rem auto;padding:0 1rem;color:#1d1d1f;background:#fff;line-height:1.45}}
h1{{font-size:1.35rem}} .box{{border:1px solid #d2d2d7;border-radius:10px;padding:1rem 1.25rem;margin:1rem 0}}
.muted{{color:#6e6e73;font-size:.9rem}} input[type=text]{{width:100%;font:inherit;padding:.6rem;letter-spacing:.08em;box-sizing:border-box}}
button{{font:inherit;padding:.55rem 1.1rem;border-radius:8px;border:1px solid #0a66c2;background:#0a66c2;color:#fff;cursor:pointer;margin-right:.5rem}}
button.secondary{{background:#fff;color:#0a66c2}} .error{{color:#b3261e}} code{{word-break:break-all}}
@media (prefers-color-scheme: dark){{body{{background:#1c1c1e;color:#f5f5f7}}.box{{border-color:#3a3a3c}}.muted{{color:#a1a1a6}}button.secondary{{background:#1c1c1e}}}}
</style></head><body>{body}</body></html>"""
    return HTMLResponse(document, status_code=status_code, headers=_PAGE_HEADERS)


def _error_page(error: OAuthError) -> HTMLResponse:
    return _page(
        "Authorization error",
        f"<h1>This sign-in request can't continue</h1>"
        f"<p class='error'>{html.escape(error.description or error.error)}</p>"
        f"<p class='muted'>Close this window and start the connection again from your AI client.</p>",
        status_code=400,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _basic_auth(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic" or not value:
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise OAuthError("invalid_client", "malformed basic authorization", status_code=401) from exc
    client_id, sep, secret = decoded.partition(":")
    if not sep:
        raise OAuthError("invalid_client", "malformed basic authorization", status_code=401)
    return unquote(client_id), unquote(secret)


def _oauth_json_error(error: OAuthError) -> JSONResponse:
    body = {"error": error.error}
    if error.description:
        body["error_description"] = error.description
    headers = dict(_JSON_HEADERS)
    if error.status_code == 401:
        headers["WWW-Authenticate"] = 'Basic realm="mak4i-oauth"'
    return JSONResponse(body, status_code=error.status_code, headers=headers)


async def _form(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("application/x-www-form-urlencoded"):
        raise OAuthError("invalid_request", "use application/x-www-form-urlencoded")
    form = await request.form()
    values = {}
    for key in form.keys():
        items = form.getlist(key)
        if len(items) != 1:
            raise OAuthError("invalid_request", f"parameter {key!r} must appear once")
        values[key] = items[0]
    return values


class OAuthEndpoints:
    def __init__(self, service: OAuthService):
        self.service = service
        self.settings = service.settings
        self._limiter = RateLimiter(self.settings.rate_limit_per_minute)
        issuer_path = self.settings.issuer_path
        self._cookie_path = (issuer_path or "") + "/oauth"

    # -- routing ---------------------------------------------------------------

    def routes(self) -> list[Route]:
        routes = [
            Route(path, self.protected_resource_metadata, methods=["GET"])
            for path in dict.fromkeys(self.settings.protected_resource_metadata_paths)
        ]
        routes += [
            Route(self.settings.authorization_server_metadata_path, self.authorization_server_metadata, methods=["GET"]),
            Route(s.AUTHORIZE_PATH, self.authorize, methods=["GET"]),
            Route(s.SIGN_IN_PATH, self.sign_in, methods=["POST"]),
            Route(s.CONSENT_PATH, self.consent, methods=["POST"]),
            Route(s.TOKEN_PATH, self.token, methods=["POST"]),
            Route(s.REVOKE_PATH, self.revoke, methods=["POST"]),
        ]
        if self.settings.enable_dcr:
            routes.append(Route(s.REGISTER_PATH, self.register, methods=["POST"]))
        return routes

    def unauthenticated_paths(self) -> set[str]:
        return {r.path for r in self.routes()}

    # -- metadata ----------------------------------------------------------------

    async def protected_resource_metadata(self, request: Request) -> JSONResponse:
        return JSONResponse(self.service.protected_resource_metadata(), headers={"Cache-Control": "max-age=300"})

    async def authorization_server_metadata(self, request: Request) -> JSONResponse:
        return JSONResponse(self.service.authorization_server_metadata(), headers={"Cache-Control": "max-age=300"})

    # -- browser flow ----------------------------------------------------------

    async def authorize(self, request: Request) -> Response:
        params = dict(request.query_params)
        try:
            auth_request = self.service.validate_authorization_request(params)
        except NonRedirectableError as exc:
            return _error_page(exc)
        except OAuthError as exc:
            return RedirectResponse(self.service.error_redirect(params, exc), status_code=302, headers=_PAGE_HEADERS)
        now = self.service._now()
        self.service._store.purge_expired_sessions(now)
        session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.service._store.insert_session(
            {
                "session_hash": hash_token(session_id),
                "csrf_hash": hash_token(csrf),
                "principal_id": None,
                "request": auth_request.as_dict(),
                "created_at": now,
                "expires_at": now + timedelta(seconds=self.settings.browser_session_ttl),
            }
        )
        response = self._sign_in_page(auth_request, csrf)
        response.set_cookie(
            COOKIE_NAME,
            session_id,
            max_age=self.settings.browser_session_ttl,
            path=self._cookie_path,
            secure=self.settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return response

    async def sign_in(self, request: Request) -> Response:
        if not self._limiter.allow(f"sign-in:{_client_ip(request)}"):
            return _page("Too many attempts", "<h1>Too many sign-in attempts</h1><p>Wait a minute and try again.</p>", status_code=429)
        session, error = await self._session(request)
        if error is not None:
            return error
        form = await request.form()
        auth_request = AuthorizationRequest.from_dict(session["request"])
        csrf = form.get("csrf", "")
        try:
            principal = self.service.sign_in(form.get("code", ""))
        except OAuthError as exc:
            return self._sign_in_page(auth_request, csrf, error=exc.description, status_code=400)
        self.service._store.bind_session_principal(session["session_hash"], principal.principal_id)
        return self._consent_page(auth_request, principal, csrf)

    async def consent(self, request: Request) -> Response:
        session, error = await self._session(request)
        if error is not None:
            return error
        if not session["principal_id"]:
            return _error_page(OAuthError("access_denied", "sign in first"))
        # One decision per session (§5.4): delete before acting.
        if not self.service._store.delete_session(session["session_hash"]):
            return _error_page(OAuthError("access_denied", "this request was already completed"))
        form = await request.form()
        auth_request = AuthorizationRequest.from_dict(session["request"])
        principal = self.service._cp.get_principal(session["principal_id"])
        response_kwargs = {"status_code": 302, "headers": _PAGE_HEADERS}
        if form.get("decision") != "approve":
            response = RedirectResponse(self.service.deny_redirect(principal, auth_request), **response_kwargs)
        else:
            try:
                location = self.service.approve(principal, auth_request)
            except NonRedirectableError as exc:
                return _error_page(exc)
            except OAuthError as exc:
                location = self.service.error_redirect(auth_request.as_dict(), exc)
            except Exception:
                location = self.service.error_redirect(
                    auth_request.as_dict(), OAuthError("access_denied", "authorization could not be granted")
                )
            response = RedirectResponse(location, **response_kwargs)
        response.delete_cookie(COOKIE_NAME, path=self._cookie_path)
        return response

    async def _session(self, request: Request):
        session_id = request.cookies.get(COOKIE_NAME, "")
        session = self.service._store.get_session(hash_token(session_id)) if session_id else None
        if session is None or session["expires_at"] <= self.service._now():
            return None, _error_page(OAuthError("access_denied", "your sign-in session expired; start again from your AI client"))
        form = await request.form()
        if not hmac.compare_digest(hash_token(form.get("csrf", "")), session["csrf_hash"]):
            return None, _error_page(OAuthError("access_denied", "the form could not be verified; start again"))
        return session, None

    def _sign_in_page(
        self, auth_request: AuthorizationRequest, csrf: str, *, error: str | None = None, status_code: int = 200
    ) -> HTMLResponse:
        action = html.escape(self.settings.endpoint(s.SIGN_IN_PATH))
        client = html.escape(auth_request.client_name or auth_request.client_id)
        error_html = f"<p class='error'>{html.escape(error)}</p>" if error else ""
        return _page(
            "Sign in to MAK4I",
            f"""<h1>Sign in to {html.escape(self.settings.resource_name)}</h1>
<p><strong>{client}</strong> wants to connect to this MAK4I server on your behalf.</p>
<div class="box"><form method="post" action="{action}">
<label for="code">Sign-in code</label>
<input type="text" id="code" name="code" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" required autofocus>
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
<p class="muted">Get a one-time code by running <code>mak4i oauth sign-in-code</code>, or ask an
owner of your organization for one. Codes expire after a few minutes and work once.</p>
{error_html}<button type="submit">Continue</button></form></div>""",
            status_code=status_code,
        )

    def _consent_page(self, auth_request: AuthorizationRequest, principal, csrf: str) -> HTMLResponse:
        action = html.escape(self.settings.endpoint(s.CONSENT_PATH))
        verified = auth_request.client_kind == "pre_registered"
        name = html.escape(auth_request.client_name or "Unnamed client")
        label = "" if verified else " <span class='muted'>(name reported by the client, unverified)</span>"
        host = html.escape(urlsplit(auth_request.redirect_uri).netloc)
        scopes = "".join(f"<li>{html.escape(_SCOPE_TEXT.get(sc, sc))}</li>" for sc in auth_request.scopes)
        return _page(
            "Allow access?",
            f"""<h1>Allow {name} to use MAK4I as you?</h1>
<div class="box">
<p>Signed in as <strong>{html.escape(principal.display_name)}</strong>
<span class="muted">({html.escape(principal.principal_id)})</span></p>
<p>Client: <strong>{name}</strong>{label}<br>
<span class="muted">Client ID: <code>{html.escape(auth_request.client_id)}</code></span></p>
<p>After you decide, your browser returns to <strong>{host}</strong>.</p>
<p>It will be able to:</p><ul>{scopes}</ul>
<p class="muted">It never gets more access than your project grants, and you can revoke it
at any time (<code>mak4i oauth authorization revoke</code>).</p>
<p class="muted">Server: <code>{html.escape(auth_request.resource)}</code></p>
</div>
<form method="post" action="{action}">
<input type="hidden" name="csrf" value="{html.escape(csrf)}">
<button type="submit" name="decision" value="approve">Allow</button>
<button type="submit" name="decision" value="deny" class="secondary">Deny</button>
</form>""",
        )

    # -- token / revocation / registration -------------------------------------

    async def token(self, request: Request) -> Response:
        try:
            form = await _form(request)
            basic = _basic_auth(request)
            key = f"token:{_client_ip(request)}:{form.get('client_id') or (basic[0] if basic else '')}"
            if not self._limiter.allow(key):
                return JSONResponse({"error": "slow_down"}, status_code=429, headers=_JSON_HEADERS)
            client = self.service.authenticate_client(form, basic)
            grant_type = form.get("grant_type")
            if grant_type == "authorization_code":
                body = self.service.exchange_code(form, client)
            elif grant_type == "refresh_token":
                body = self.service.refresh(form, client)
            else:
                raise OAuthError("unsupported_grant_type", "use authorization_code or refresh_token")
        except OAuthError as exc:
            return _oauth_json_error(exc)
        return JSONResponse(body, headers=_JSON_HEADERS)

    async def revoke(self, request: Request) -> Response:
        try:
            form = await _form(request)
            if not self._limiter.allow(f"revoke:{_client_ip(request)}"):
                return JSONResponse({"error": "slow_down"}, status_code=429, headers=_JSON_HEADERS)
            client = self.service.authenticate_client(form, _basic_auth(request))
            self.service.revoke(form, client)
        except OAuthError as exc:
            return _oauth_json_error(exc)
        return Response(status_code=200, headers=_JSON_HEADERS)

    async def register(self, request: Request) -> Response:
        if not self._limiter.allow(f"register:{_client_ip(request)}"):
            return JSONResponse({"error": "slow_down"}, status_code=429, headers=_JSON_HEADERS)
        try:
            try:
                metadata = json.loads(await request.body())
            except ValueError as exc:
                raise OAuthError("invalid_client_metadata", "body must be JSON") from exc
            body = self.service.register_dynamic_client(metadata)
        except OAuthError as exc:
            return _oauth_json_error(exc)
        return JSONResponse(body, status_code=201, headers=_JSON_HEADERS)
