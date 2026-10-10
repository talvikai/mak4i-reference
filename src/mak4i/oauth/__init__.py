"""Self-contained OAuth 2.1 authorization service for MCP over HTTP
(MAK-0008). Free and independent of any external identity provider: the
resource owner is a MAK4I principal who signs in with a CLI-issued sign-in
code, and every token is an opaque random value stored only as a hash."""

from mak4i.oauth.errors import OAuthError
from mak4i.oauth.service import OAuthService, ValidatedAccessToken
from mak4i.oauth.settings import OAuthConfigError, OAuthSettings

__all__ = [
    "OAuthConfigError",
    "OAuthError",
    "OAuthService",
    "OAuthSettings",
    "ValidatedAccessToken",
]
