"""The MAK4I protocol release this implementation targets, and the
conformance profiles it claims (MAK-0008 §10.5, CONFORMANCE.md
"Conformance profiles"). A profile is claimed only when every criterion
of it has evidence (docs/CONFORMANCE_EVIDENCE.md); the HTTP + OAuth
profile needs real-client interoperability (M16) first."""

from __future__ import annotations

PROTOCOL_VERSION = "0.2"
"""SPEC.md version; the exact revision is pinned in PROTOCOL_REVISION."""

SERVER_PROFILE = "server-project-records"


def claimed_profiles(transport: str) -> list[str]:
    """Profiles claimed for a server running on `transport` ("stdio" or "http")."""
    transport_profile = "transport-stdio" if transport == "stdio" else "transport-http-credential"
    return [SERVER_PROFILE, transport_profile]
