"""Client ID Metadata Document fetching (MAK-0008 §8.3).

When an OAuth `client_id` is an https URL, the authorization server fetches
the document at that URL to learn the client's redirect URIs. Fetching an
attacker-chosen URL from the server is an SSRF risk, so this module:

- accepts only `https` URLs without credentials, fragments or dot-segments;
- resolves the host once and refuses it unless *every* address is public
  (no loopback, private, link-local, multicast, reserved or unspecified);
- connects to the address it validated (so DNS cannot be re-pointed between
  the check and the connection) while still verifying the TLS certificate
  against the original host name;
- never follows redirects, and enforces a timeout and a size limit.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_DOCUMENT_BYTES = 64 * 1024
FETCH_TIMEOUT_SECONDS = 5.0
DEFAULT_CACHE_SECONDS = 24 * 3600


class ClientMetadataError(Exception):
    """The client metadata document could not be fetched or is invalid.
    Its message is safe to show on an error page."""


@dataclass(frozen=True)
class FetchedDocument:
    body: bytes
    max_age: int | None


Resolver = Callable[[str, int], list[str]]


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def check_url(url: str) -> tuple[str, int, str]:
    """Validate the client_id URL; return (host, port, path_and_query)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ClientMetadataError("client_id URL must be https")
    if parts.username or parts.password or parts.fragment:
        raise ClientMetadataError("client_id URL must not contain credentials or a fragment")
    segments = parts.path.split("/")
    if "." in segments or ".." in segments or not parts.path:
        raise ClientMetadataError("client_id URL must have a path without dot-segments")
    port = parts.port or 443
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    return parts.hostname, port, target


def public_addresses(host: str, port: int, resolver: Resolver) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            addresses = resolver(host, port)
        except OSError as exc:
            raise ClientMetadataError("client_id host could not be resolved") from exc
    if not addresses:
        raise ClientMetadataError("client_id host could not be resolved")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ClientMetadataError("client_id host resolves to a non-public address")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a pre-validated IP address, with SNI and certificate
    verification for the original host name."""

    def __init__(self, host: str, address: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:  # pragma: no cover - exercised against real hosts only
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def fetch_document(url: str, *, resolver: Resolver = _default_resolver) -> FetchedDocument:
    host, port, target = check_url(url)
    address = public_addresses(host, port, resolver)[0]
    conn = _PinnedHTTPSConnection(host, address, port, FETCH_TIMEOUT_SECONDS)
    try:
        conn.request("GET", target, headers={"Accept": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise ClientMetadataError(f"client metadata document returned HTTP {response.status}")
        body = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(body) > MAX_DOCUMENT_BYTES:
            raise ClientMetadataError("client metadata document is too large")
        max_age = None
        match = re.search(r"max-age=(\d+)", response.getheader("Cache-Control") or "")
        if match:
            max_age = int(match.group(1))
        return FetchedDocument(body=body, max_age=max_age)
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise ClientMetadataError("client metadata document could not be fetched") from exc
    finally:
        conn.close()


def parse_document(client_id: str, body: bytes) -> dict:
    """Validate a fetched document against MAK-0008 §8.3 and the protocol's
    client-id-metadata-document schema. Returns the normalized fields."""
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ClientMetadataError("client metadata document is not JSON") from exc
    if not isinstance(doc, dict):
        raise ClientMetadataError("client metadata document must be a JSON object")
    if doc.get("client_id") != client_id:
        raise ClientMetadataError("client metadata document client_id does not match its URL")
    redirect_uris = doc.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or not all(isinstance(u, str) and u for u in redirect_uris)
    ):
        raise ClientMetadataError("client metadata document needs redirect_uris")
    method = doc.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise ClientMetadataError(
            "client metadata documents are accepted only for public clients "
            "(token_endpoint_auth_method 'none')"
        )
    grant_types = doc.get("grant_types", ["authorization_code"])
    if not isinstance(grant_types, list) or "authorization_code" not in grant_types:
        raise ClientMetadataError("client metadata document must allow authorization_code")
    name = doc.get("client_name")
    return {
        "client_name": name if isinstance(name, str) and name.strip() else None,
        "redirect_uris": list(redirect_uris),
    }
