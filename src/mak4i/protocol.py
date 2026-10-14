"""The MAK4I protocol release this implementation targets, and the
conformance profiles it claims (MAK-0008 §10.5, CONFORMANCE.md
"Conformance profiles"). A profile is claimed only when every criterion
of it has evidence (docs/CONFORMANCE_EVIDENCE.md), and only for what the
running deployment actually enables."""

from __future__ import annotations

PROTOCOL_VERSION = "0.2"
"""SPEC.md version; the exact revision is pinned in PROTOCOL_REVISION."""

SERVER_PROFILE = "server-project-records"


def claimed_profiles(transport: str, oauth=None, *, change_feed: bool = False) -> list[str]:
    """Profiles claimed for a server running on `transport` ("stdio" or
    "http"). `oauth` is the active `OAuthSettings` (None when OAuth is off):
    with it, HTTP + OAuth is claimed (M16 has real-client evidence: Claude,
    ChatGPT and Claude Code) plus each registration option that is enabled."""
    extra = ["server-change-feed"] if change_feed else []  # MAK-0008 §14, durable feed only
    if transport == "stdio":
        return [SERVER_PROFILE, *extra, "transport-stdio"]
    profiles = [SERVER_PROFILE, *extra, "transport-http-credential"]
    if oauth is not None:
        profiles.append("transport-http-oauth")
        if getattr(oauth, "enable_cimd", False):
            profiles.append("oauth-cimd")
        if getattr(oauth, "enable_dcr", False):
            profiles.append("oauth-dcr")
    return profiles
