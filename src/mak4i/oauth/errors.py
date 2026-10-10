from __future__ import annotations


class OAuthError(Exception):
    """An OAuth protocol error with an RFC 6749 / RFC 7009 / RFC 8707 error
    code. `description` is safe to show the client: it never contains a
    secret."""

    def __init__(self, error: str, description: str = "", *, status_code: int = 400):
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description
        self.status_code = status_code


class NonRedirectableError(OAuthError):
    """An authorization-request error that must be shown as a page and never
    sent to the redirect URI, because the client or the redirect URI could
    not be validated (MAK-0008 §5.1)."""
