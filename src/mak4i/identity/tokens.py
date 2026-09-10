from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_PREFIX = "mak4i_"
"""Human-recognizable prefix on the raw bearer token. Purely cosmetic — it
helps a developer spot a MAK4I credential in their config; it is not parsed
and carries no meaning."""

_TOKEN_ENTROPY_BYTES = 32


def generate_token() -> tuple[str, str]:
    """Mint a new credential secret.

    Returns `(raw_token, token_hash)`. The raw token is shown to the
    developer exactly once at issuance and never stored; only `token_hash`
    is persisted (MVP spec §1 CREDENTIAL: "Never store raw bearer tokens
    after issuance").
    """
    raw = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
    return raw, hash_token(raw)


def hash_token(raw_token: str) -> str:
    """SHA-256 hex digest of a raw bearer token — the value stored and
    looked up in the credentials table. A hash (not a slow KDF) is
    appropriate here: the input is a 256-bit random secret, not a
    low-entropy password, so it is not brute-forcible."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def tokens_match(raw_token: str, expected_hash: str) -> bool:
    """Constant-time comparison of a presented raw token against a stored
    hash."""
    return hmac.compare_digest(hash_token(raw_token), expected_hash)
