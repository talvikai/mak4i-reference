"""Operator CLI — the same MAK4IEngine used by tests and the MCP server,
callable directly by a human (requirements §9: "the internal engine should
remain callable by tests/CLI without MCP"). `doctor` and the control-plane
subcommands (`org`, `project`, `principal`, `grant`, `credential`) are
CLI-only and never model-callable (MVP_ARCHITECTURE.md §10).

`init` / `serve` / `access provision` are onboarding conveniences: each is
a thin wrapper over the same control-plane calls the granular subcommands
make. `init` + `serve` are the local/self-hosted first-run path (they read
and write `.mak4i/`, a gitignored local config — see `localconfig.py`);
`access provision` is an operator-only shortcut for giving one external
collaborator scoped access to a hosted or server deployment. None of them
add business logic, weaken authorization, or touch a hosted environment
from the local path.

`--principal <id>` on the artifact subcommands is a **trusted local/operator
mode only**: it acts as that principal by id without a credential, and is
audited with `auth_method="operator_impersonation"` so it is never confused
with a real credential-authenticated call in the trail. This flag is never
wired into `mcp_server.py` — the hosted MCP path always authenticates a
credential.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from mak4i import localconfig
from mak4i.api import OPERATOR_IMPERSONATION, ArtifactNotActiveError, MAK4IEngine
from mak4i.audit import AuditLogger
from mak4i.config import build_control_plane_from_env, build_store_from_env
from mak4i.identity import (
    AccessDeniedError,
    ControlPlane,
    CredentialInvalidError,
    IdentityError,
    Principal,
)
from mak4i.identity.authz import Authorizer
from mak4i.identity.errors import PrincipalNotFoundError
from mak4i.store.base import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ConcurrentModificationError,
)


class _CliError(Exception):
    """A user-facing CLI failure — printed to stderr with exit code 1 and no
    traceback (like the `IdentityError` / missing-env-var handling in
    `main`). Use for input validation in the convenience commands."""


def _make_control_plane() -> ControlPlane:
    # Every command that reaches here (org/project/principal/grant/
    # credential/create/search/get-current/history/supersede/doctor —
    # everything except `serve` and `init`, which each have their own
    # dedicated resolution) resolves an ambient local `.mak4i/`
    # environment first, but only when nothing has already been
    # explicitly configured — see the function's docstring for the
    # full precedence.
    localconfig.resolve_ambient_local_environment()
    return build_control_plane_from_env()


def _make_engine(control_plane: ControlPlane) -> MAK4IEngine:
    audit = AuditLogger()
    return MAK4IEngine(
        build_store_from_env(),
        audit=audit,
        authorizer=Authorizer(control_plane, audit),
        control_plane=control_plane,
    )


def _resolve_principal(control_plane: ControlPlane, principal_id: str) -> Principal:
    principal = control_plane.get_principal(principal_id)
    if principal is None:
        raise PrincipalNotFoundError(principal_id)
    return principal


def _resolve_actor(control_plane: ControlPlane, args: argparse.Namespace) -> Principal:
    """The one identity rule for every control-plane subcommand below:

        credential -> authentication -> principal -> actor

    `--actor <id>`, when given, is resolved exactly as before (a bare
    `get_principal()` lookup, kept only for backward compatibility — see
    `_resolve_principal`) — this function changes nothing about that
    path.

    When `--actor` is omitted, the actor is derived by *authenticating* a
    bearer credential, never by trusting a cached id:

    1. `MAK4I_TOKEN`, if already present in the environment (an
       Enterprise Self-Hosted operator's own exported credential, or an
       explicit local override) — the same precedence already
       established for `MAK4I_CONTROL_PLANE_DB` elsewhere in this file:
       an explicit value always wins over an ambient one.
    2. Otherwise, for an initialized local environment, the credential
       `mak4i init` saved to `.mak4i/credentials.json`.

    Either way the token is *authenticated* via
    `ControlPlane.authenticate()` — the same call the MCP hosted path
    already makes — which additionally verifies the credential is
    active, unexpired, and unrevoked, and that its principal and
    organization are both active. This is deliberately **not** a
    fallback to `.mak4i/config.json`'s cached `owner_principal_id`: a
    missing, invalid, expired, or revoked credential is a clear error
    here, never silently substituted with a weaker identity check.
    """
    if args.actor:
        return _resolve_principal(control_plane, args.actor)

    token = os.environ.get("MAK4I_TOKEN") or localconfig.load_token()
    if not token:
        raise _CliError(
            "no --actor given and no credential available to derive one "
            "automatically. Pass --actor <principal-id>, set MAK4I_TOKEN "
            "to a valid credential, or run `mak4i init` to create a local "
            "environment."
        )
    try:
        return control_plane.authenticate(token)
    except CredentialInvalidError as exc:
        # Deliberately does NOT suggest `mak4i init --force` here: it
        # does not repair or rotate a credential — it provisions a
        # brand-new organization/principal/project/grant/credential
        # alongside the existing one (verified directly: every row
        # count doubles, the old rows are never touched or deleted, and
        # the previously-current project becomes invisible under the
        # new identity) and overwrites `.mak4i/config.json`/
        # `credentials.json` to point at that new, disconnected
        # environment. It is not a safe recovery action to name here.
        # Bare `mak4i credential issue` (with no --actor) is also not a
        # standalone fix in this situation — it goes through this same
        # derivation and would fail with this same error — so the
        # example below always includes --actor explicitly.
        raise _CliError(
            f"{exc} — could not automatically derive --actor from the "
            "current credential. Pass --actor <principal-id> explicitly "
            "(local environments: see \"owner_principal_id\" in "
            ".mak4i/config.json) — for example, to issue yourself a new "
            "credential: `mak4i credential issue --actor <id> "
            "--principal-id <id>`."
        ) from exc


def _to_json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(_to_json_safe(value), indent=2))


def _split_tags(raw: str | None) -> list[str] | None:
    return raw.split(",") if raw else None


def _split_permissions(raw: str) -> list[str]:
    return raw.split(",")


# -- artifact commands (Authorizer-gated, operator impersonation) -----------


def _cmd_doctor(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    principal = _resolve_principal(control_plane, args.principal)
    engine = _make_engine(control_plane)
    errors = engine.check_integrity(organization_id=principal.organization_id, project=args.project)
    if not errors:
        print(f"OK — no integrity errors found in project {args.project!r}.")
        return 0
    print(
        f"FAILED — {len(errors)} integrity error(s) in project {args.project!r}:",
        file=sys.stderr,
    )
    for error in errors:
        print(f"  [{error.kind}] lineage {error.lineage_id!r}: {error.detail}", file=sys.stderr)
    return 1


def _cmd_create(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    principal = _resolve_principal(control_plane, args.principal)
    engine = _make_engine(control_plane)
    try:
        artifact = engine.create_artifact(
            principal=principal,
            project=args.project,
            artifact_id=args.artifact_id,
            artifact_type=args.artifact_type,
            title=args.title,
            content=args.content,
            rationale=args.rationale,
            tags=_split_tags(args.tags),
            auth_method=OPERATOR_IMPERSONATION,
        )
    except (ArtifactAlreadyExistsError, AccessDeniedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(artifact)
    return 0


def _cmd_supersede(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    principal = _resolve_principal(control_plane, args.principal)
    engine = _make_engine(control_plane)
    try:
        artifact = engine.supersede_artifact(
            principal=principal,
            project=args.project,
            old_id=args.old_id,
            content=args.content,
            reason=args.reason,
            title=args.title,
            tags=_split_tags(args.tags),
            auth_method=OPERATOR_IMPERSONATION,
        )
    except (
        ArtifactNotFoundError,
        ArtifactNotActiveError,
        ArtifactAlreadyExistsError,
        ConcurrentModificationError,
        AccessDeniedError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(artifact)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    principal = _resolve_principal(control_plane, args.principal)
    engine = _make_engine(control_plane)
    try:
        results = engine.search(
            principal=principal,
            project=args.project,
            artifact_type=args.artifact_type,
            tags=_split_tags(args.tags),
            status=args.status,
            auth_method=OPERATOR_IMPERSONATION,
        )
    except AccessDeniedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(results)
    return 0


def _cmd_get_current(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    principal = _resolve_principal(control_plane, args.principal)
    engine = _make_engine(control_plane)
    try:
        package = engine.get_current(
            principal=principal,
            project=args.project,
            artifact_type=args.artifact_type,
            tags=_split_tags(args.tags),
            auth_method=OPERATOR_IMPERSONATION,
        )
    except AccessDeniedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(package)
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    principal = _resolve_principal(control_plane, args.principal)
    engine = _make_engine(control_plane)
    try:
        results = engine.get_history(
            principal=principal,
            project=args.project,
            lineage_id=args.lineage_id,
            auth_method=OPERATOR_IMPERSONATION,
        )
    except AccessDeniedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(results)
    return 0


# -- control-plane commands (operator-only, never model-callable) -----------


def _cmd_org_create(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    organization, owner = control_plane.onboard_organization(
        organization_name=args.name, owner_display_name=args.owner_display_name
    )
    _print_json({"organization": organization, "owner": owner})
    return 0


def _cmd_org_list(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    _print_json(control_plane.list_organizations())
    return 0


def _cmd_org_show(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    organization = control_plane.get_organization(args.organization_id)
    if organization is None:
        print(f"error: no such organization: {args.organization_id!r}", file=sys.stderr)
        return 1
    _print_json(organization)
    return 0


def _cmd_project_create(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    project = control_plane.create_project(
        actor=actor, organization_id=args.organization_id, name=args.name
    )
    _print_json(project)
    return 0


def _cmd_project_list(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    # LIST = discovery: no --organization-id required — an omitted one
    # defaults to the authenticated actor's own organization (the only
    # one `_require_owner` would let them list anyway).
    organization_id = args.organization_id or actor.organization_id
    _print_json(control_plane.list_projects(actor=actor, organization_id=organization_id))
    return 0


def _cmd_project_show(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    _print_json(control_plane.show_project(actor=actor, project_id=args.project_id))
    return 0


def _cmd_principal_create(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    principal = control_plane.create_principal(
        actor=actor,
        organization_id=args.organization_id,
        type=args.type,
        display_name=args.display_name,
        role=args.role,
    )
    _print_json(principal)
    return 0


def _cmd_principal_list(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    # LIST = discovery: no --organization-id required — an omitted one
    # defaults to the authenticated actor's own organization (the only
    # one `_require_owner` would let them list anyway).
    organization_id = args.organization_id or actor.organization_id
    _print_json(control_plane.list_principals(actor=actor, organization_id=organization_id))
    return 0


def _cmd_principal_show(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    _print_json(control_plane.show_principal(actor=actor, principal_id=args.principal_id))
    return 0


def _cmd_grant_create(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    grant = control_plane.grant(
        actor=actor,
        principal_id=args.principal_id,
        project_id=args.project_id,
        permissions=_split_permissions(args.permissions),
    )
    _print_json(grant)
    return 0


def _cmd_grant_list(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    _print_json(control_plane.list_grants(actor=actor, principal_id=args.principal_id))
    return 0


def _cmd_grant_revoke(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    control_plane.revoke_grant(
        actor=actor, principal_id=args.principal_id, project_id=args.project_id
    )
    print(f"revoked grant for principal {args.principal_id!r} on project {args.project_id!r}")
    return 0


_CLAUDE_MCP_ADD_HTTP = (
    "  claude mcp add mak4i --transport http <your-mak4i-endpoint>/mcp \\\n"
    '    --header "Authorization: Bearer <token>"'
)
_CLAUDE_MCP_ADD_STDIO = "  claude mcp add mak4i -- mak4i serve"


def _print_connection_instructions(*, raw_token: str) -> None:
    """Generic, client-agnostic connection instructions shown after
    issuing a credential (requirements §9). Deliberately says nothing
    Talvik-hosted-specific; any MCP client that supports a static
    `Authorization: Bearer` header works the same way, not just Claude
    Code.

    When `MAK4I_PUBLIC_ENDPOINT` is set (the full client-facing MCP URL,
    the same variable and meaning `access provision` uses), this is an
    Enterprise Self-Hosted HTTP deployment: print the exact connect
    command for that endpoint and nothing about the local stdio path,
    which doesn't apply there. Otherwise fall back to both generic
    variants with `<your-mak4i-endpoint>` as a placeholder."""
    endpoint = os.environ.get("MAK4I_PUBLIC_ENDPOINT", "").strip()
    print("\nConnect an AI client:\n")
    if endpoint:
        print(f"MCP endpoint: {endpoint}\n")
        print(
            f"  claude mcp add mak4i --transport http {endpoint} \\\n"
            f'    --header "Authorization: Bearer {raw_token}"'
        )
    else:
        print("If you're running the hosted HTTP MCP server:")
        print(_CLAUDE_MCP_ADD_HTTP.replace("<token>", raw_token))
        print("\nIf you're running locally via `mak4i serve` (stdio):")
        print(_CLAUDE_MCP_ADD_STDIO)
    print(
        "\nAny MCP client that accepts a static Authorization header works the "
        "same way — swap the connect command for your client's equivalent."
    )


def _cmd_credential_issue(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    expires_at = datetime.fromisoformat(args.expires_at) if args.expires_at else None
    credential, raw_token = control_plane.issue_credential(
        actor=actor,
        principal_id=args.principal_id,
        display_name=args.display_name,
        expires_at=expires_at,
    )
    print("Credential created.\n")
    print(f"credential_id: {credential.credential_id}")
    print(f"Authorization: Bearer {raw_token}")
    print("\nSave this token now. It will not be shown again.")
    if not args.no_connection_help:
        _print_connection_instructions(raw_token=raw_token)
    return 0


def _credential_summary(c) -> dict:
    """Every CLI-facing view of a `Credential` goes through this — never
    `token_hash`. It's an internal lookup key, not the secret itself
    (only the raw token, shown once at issuance, authenticates anything),
    but omitting it everywhere a `Credential` is displayed is
    belt-and-suspenders on top of that, and keeps `credential list` and
    `credential revoke` consistent with each other (requirements §8:
    "must never show the raw token")."""
    return {
        "credential_id": c.credential_id,
        "principal_id": c.principal_id,
        "display_name": c.display_name,
        "status": c.status,
        "created_at": c.created_at.isoformat(),
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
    }


def _cmd_credential_list(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    credentials = control_plane.list_credentials(actor=actor, principal_id=args.principal_id)
    _print_json([_credential_summary(c) for c in credentials])
    return 0


def _cmd_credential_revoke(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_actor(control_plane, args)
    credential = control_plane.revoke_credential(actor=actor, credential_id=args.credential_id)
    _print_json(_credential_summary(credential))
    return 0


# -- onboarding convenience commands ---------------------------------------
#
# `init`, `serve`, and `access provision` add no business logic: each is a
# thin wrapper over the same `ControlPlane` calls the granular subcommands
# above make, plus (for init/serve) a small local config file so a
# developer doesn't hand-copy ids and a token between steps. The granular
# commands remain the source of truth for anything more complex.


def _prompt(label: str, provided: str | None) -> str:
    """Return `provided` if given, else read it interactively. Fails
    clearly (no traceback) when a value is missing and stdin is not a
    terminal — so scripted callers get told which `--flag` to pass."""
    if provided is not None and provided.strip():
        return provided.strip()
    if not sys.stdin.isatty():
        raise _CliError(
            f"{label!r} is required — pass the matching --flag when not running interactively"
        )
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print("  (a value is required)", file=sys.stderr)


def _cmd_init(args: argparse.Namespace) -> int:
    """Local/self-hosted first-run bootstrap: one organization, one owner
    principal, one project, a read+write grant, one credential — then save
    a local config so `mak4i serve` can start it with no further copying.

    Local development only. This never touches a hosted environment.
    """
    if localconfig.exists() and not args.force:
        existing = localconfig.load()
        print("A local MAK4I environment already exists.\n")
        if existing is not None:
            print(f"  Organization: {existing.organization_name} ({existing.organization_id})")
            print(f"  Principal:    {existing.owner_principal_id}")
            print(f"  Project:      {existing.project_name} ({existing.project_id})")
            print(f"  Config:       {localconfig.config_path()}")
        print("\nRun `mak4i serve` to start it, or `mak4i init --force` to")
        print("provision a fresh one (leaves the existing data in place).")
        return 0

    organization_name = _prompt("Organization name", args.org_name)
    owner_display_name = _prompt("Your display name", args.display_name)
    project_name = _prompt("First project name", args.project_name)

    print("\nInitializing local MAK4I...\n", file=sys.stderr)

    localconfig.ensure_home()
    # Deliberately NOT `setdefault`: a fresh local environment must always
    # provision into its own default local SQLite file and artifact
    # directory, never into whatever happens to already be exported in
    # the shell (e.g. a real MAK4I_CONTROL_PLANE_DB left over from
    # Enterprise Self-Hosted work) — that would silently provision a new
    # local organization into a database this command has no business
    # touching. `mak4i init --force` on an *existing* local environment
    # doesn't reach this branch at all — see the `localconfig.exists()`
    # check above.
    stray = [
        key
        for key in ("MAK4I_CONTROL_PLANE_DB", "MAK4I_STORE", "MAK4I_LOCAL_STORE_DIR")
        if key in os.environ
    ]
    os.environ["MAK4I_CONTROL_PLANE_DB"] = localconfig.default_control_plane_db()
    os.environ["MAK4I_STORE"] = "local"
    os.environ["MAK4I_LOCAL_STORE_DIR"] = localconfig.default_local_store_dir()
    if stray:
        print(
            f"note: provisioning a fresh local environment; ignoring "
            f"{', '.join(sorted(stray))} from the shell.",
            file=sys.stderr,
        )
    # `init` owns first-run schema creation for the local instance; the
    # deployed database is still migrated with Alembic (config.py's default
    # leaves this unset).
    os.environ["MAK4I_CONTROL_PLANE_CREATE_TABLES"] = "1"

    control_plane = _make_control_plane()
    organization, owner = control_plane.onboard_organization(
        organization_name=organization_name, owner_display_name=owner_display_name
    )
    print("✓ Organization created", file=sys.stderr)
    print("✓ Owner principal created", file=sys.stderr)
    project = control_plane.create_project(
        actor=owner, organization_id=organization.organization_id, name=project_name
    )
    print("✓ Project created", file=sys.stderr)
    control_plane.grant(
        actor=owner,
        principal_id=owner.principal_id,
        project_id=project.project_id,
        permissions=["read", "write"],
    )
    print("✓ READ/WRITE grant created", file=sys.stderr)
    credential, raw_token = control_plane.issue_credential(
        actor=owner, principal_id=owner.principal_id, display_name="mak4i init (local owner)"
    )
    print("✓ Credential issued", file=sys.stderr)

    config = localconfig.LocalConfig(
        organization_id=organization.organization_id,
        organization_name=organization.name,
        owner_principal_id=owner.principal_id,
        owner_display_name=owner.display_name,
        project_id=project.project_id,
        project_name=project.name,
        credential_id=credential.credential_id,
        control_plane_db=os.environ["MAK4I_CONTROL_PLANE_DB"],
        store=os.environ["MAK4I_STORE"],
        local_store_dir=os.environ.get("MAK4I_LOCAL_STORE_DIR"),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    localconfig.save(config)
    localconfig.save_token(raw_token)
    print("✓ Local configuration saved", file=sys.stderr)

    print("\nMAK4I is ready.\n")
    print(f"Organization: {organization.organization_id}")
    print(f"Principal:    {owner.principal_id}")
    print(f"Project:      {project.project_id}")
    print(f"\nLocal config: {localconfig.config_path()}  (credential in {localconfig.credentials_path().name}, gitignored)")
    print("\nNext:")
    print("  mak4i serve")
    print("\nConnect an AI client (once `mak4i serve` is running):")
    print(_CLAUDE_MCP_ADD_STDIO)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start the existing MCP server against the local environment created
    by `mak4i init`. This wraps `mak4i.mcp_server.main()` — it is not a
    second server implementation.

    `--transport`/`--host`/`--port` are conveniences over the same
    `MAK4I_TRANSPORT`/`MAK4I_HOST`/`MAK4I_PORT` environment variables
    `mcp_server.py` already reads: a flag here only sets the matching env
    var (when given), so `mcp_server.resolve_transport/host/port()` stay
    the single source of truth for precedence and validation, whether
    they're called from here or from a container's `python -m
    mak4i.mcp_server` with no CLI involved at all.
    """
    config = localconfig.load()
    if config is None:
        print("MAK4I has not been initialized locally.\n", file=sys.stderr)
        print("Run:", file=sys.stderr)
        print("  mak4i init", file=sys.stderr)
        return 1

    token = localconfig.load_token()
    if token is None:
        print(
            f"Local credential file missing ({localconfig.credentials_path()}).\n\n"
            "Run `mak4i init --force` to provision a fresh local credential.",
            file=sys.stderr,
        )
        return 1

    # `.mak4i/` is authoritative for `serve`: a stale MAK4I_TOKEN (or DB URL)
    # exported in the shell is replaced, not honored, so `mak4i init &&
    # mak4i serve` always uses the credential from that init. Unlike every
    # other command (see `_make_control_plane`), `serve`'s local
    # environment always wins even over an explicitly exported
    # MAK4I_CONTROL_PLANE_DB — this is the RC's existing shipped behavior,
    # preserved unchanged.
    overridden = localconfig.overridden_local_env_keys(config, token)
    localconfig.apply_to_env(config, token)
    if overridden:
        print(
            f"note: using the local `mak4i init` environment; ignoring "
            f"{', '.join(sorted(overridden))} from the shell.",
            file=sys.stderr,
        )

    from mak4i import mcp_server

    # A CLI flag overrides whatever's in the environment; when the flag
    # isn't given, the environment (or mcp_server's own default) decides —
    # this is the precedence requirements §8 specifies.
    if args.transport:
        os.environ["MAK4I_TRANSPORT"] = args.transport
    if args.host:
        os.environ["MAK4I_HOST"] = args.host
    if args.port:
        os.environ["MAK4I_PORT"] = str(args.port)

    transport = mcp_server.resolve_transport()
    os.environ["MAK4I_TRANSPORT"] = transport  # write back the normalized value

    # Banner goes to stderr — for the stdio transport, stdout carries the
    # MCP JSON-RPC stream and must not be written to.
    print("MAK4I local server starting...", file=sys.stderr)
    print(f"Organization: {config.organization_name}", file=sys.stderr)
    print(f"Project: {config.project_name}", file=sys.stderr)
    if transport == "stdio":
        print("Transport: stdio", file=sys.stderr)
    else:
        host = mcp_server.resolve_host()
        port = mcp_server.resolve_port()
        print("Transport: Streamable HTTP", file=sys.stderr)
        print(f"Host: {host}", file=sys.stderr)
        print(f"Port: {port}", file=sys.stderr)
        print(f"MCP endpoint: http://{host}:{port}/mcp", file=sys.stderr)
    print("\nMCP server ready.", file=sys.stderr)

    mcp_server.main()
    return 0


def _cmd_access_provision(args: argparse.Namespace) -> int:
    """Operator-only: give one external collaborator scoped access to a
    hosted or server MAK4I deployment. Runs against whatever control plane
    the operator's environment points at. Not public self-service.

    Reuses the same `ControlPlane` calls as the granular commands: a new
    organization, a `member` principal (never an owner), a project, a
    read+write grant on that project only, and one credential.
    """
    # Fail closed BEFORE anything is provisioned: the collaborator needs
    # the endpoint, so there is no point minting an organization/credential
    # we then can't hand off.
    endpoint = args.endpoint or os.environ.get("MAK4I_PUBLIC_ENDPOINT")
    if not endpoint:
        print(
            "MAK4I hosted endpoint is not configured.\n\n"
            "Set:\n"
            "  MAK4I_PUBLIC_ENDPOINT=https://...\n\n"
            "or pass:\n"
            "  --endpoint https://...\n\n"
            "Nothing was provisioned.",
            file=sys.stderr,
        )
        return 1

    collaborator_display_name = _prompt("Collaborator display name", args.display_name)
    organization_name = _prompt("Organization name", args.org_name)
    project_name = _prompt("Initial project", args.project_name)

    print("\nProvisioning access...\n", file=sys.stderr)

    control_plane = _make_control_plane()
    organization, owner = control_plane.onboard_organization(
        organization_name=organization_name,
        owner_display_name=f"{organization_name} (operator)",
    )
    print("✓ Organization created", file=sys.stderr)
    collaborator = control_plane.create_principal(
        actor=owner,
        organization_id=organization.organization_id,
        type="human",
        display_name=collaborator_display_name,
        role="member",
    )
    print("✓ Principal created", file=sys.stderr)
    project = control_plane.create_project(
        actor=owner, organization_id=organization.organization_id, name=project_name
    )
    print("✓ Project created", file=sys.stderr)
    control_plane.grant(
        actor=owner,
        principal_id=collaborator.principal_id,
        project_id=project.project_id,
        permissions=["read", "write"],
    )
    print("✓ READ/WRITE grant created", file=sys.stderr)
    _credential, raw_token = control_plane.issue_credential(
        actor=owner, principal_id=collaborator.principal_id, display_name=collaborator_display_name
    )
    print("✓ Credential issued", file=sys.stderr)

    print("\nCollaborator access:\n")
    print(f"Endpoint: {endpoint}")
    print(f"Project:  {project.project_id}")
    print(f"Token:    {raw_token}")

    if args.output:
        _write_access_file(args.output, endpoint=endpoint, project_id=project.project_id, token=raw_token)
        print(f"\nWritten to {args.output} (contains the token — handle it securely).", file=sys.stderr)

    return 0


_ACCESS_DOCS_URL = "https://github.com/talvikai/mak4i-reference/blob/main/docs/DEMO.md#connecting-a-client"


def _write_access_file(path: str, *, endpoint: str, project_id: str, token: str) -> None:
    """Minimal onboarding file for one collaborator. Deliberately contains
    only what they need to connect a client — never infrastructure,
    database, service-account, or operator detail."""
    body = (
        "MAK4I access\n\n"
        f"Endpoint: {endpoint}\n"
        f"Token: {token}\n"
        f"Project: {project_id}\n\n"
        "Documentation:\n"
        f"{_ACCESS_DOCS_URL}\n"
    )
    p = os.path.abspath(path)
    with open(p, "w") as fh:
        fh.write(body)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mak4i", description="MAK4I operator CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser(
        "init",
        help="Bootstrap a local/self-hosted MAK4I environment (org, principal, project, grant, credential).",
    )
    init.add_argument("--org-name", help="organization name (prompted if omitted)")
    init.add_argument("--display-name", help="your display name (prompted if omitted)")
    init.add_argument("--project-name", help="first project name (prompted if omitted)")
    init.add_argument(
        "--force",
        action="store_true",
        help="provision a fresh local environment even if one already exists (existing data is left in place)",
    )
    init.set_defaults(func=_cmd_init)

    serve = subparsers.add_parser(
        "serve", help="Start the local MCP server using the config from `mak4i init`."
    )
    serve.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        help=(
            "transport to serve (default: stdio, or $MAK4I_TRANSPORT). "
            "'http' and 'streamable-http' are equivalent — the latter is "
            "kept for compatibility with existing container configuration."
        ),
    )
    serve.add_argument(
        "--host",
        help=(
            "bind host for --transport http (default: 127.0.0.1, or "
            "$MAK4I_HOST). Use --host 0.0.0.0 for a container/enterprise "
            "deployment that must accept connections from other hosts."
        ),
    )
    serve.add_argument(
        "--port",
        type=int,
        help="bind port for --transport http (default: 8080, or $MAK4I_PORT/$PORT).",
    )
    serve.set_defaults(func=_cmd_serve)

    access = subparsers.add_parser(
        "access", help="Operator: grant an external collaborator access to a hosted/server deployment."
    )
    access_sub = access.add_subparsers(dest="access_command", required=True)
    access_provision = access_sub.add_parser(
        "provision",
        help="Provision one external collaborator (operator-only; not public self-service).",
    )
    access_provision.add_argument("--display-name", help="collaborator display name (prompted if omitted)")
    access_provision.add_argument("--org-name", help="organization name (prompted if omitted)")
    access_provision.add_argument("--project-name", help="initial project name (prompted if omitted)")
    access_provision.add_argument(
        "--endpoint",
        help="hosted endpoint URL to hand the collaborator — required (or set $MAK4I_PUBLIC_ENDPOINT)",
    )
    access_provision.add_argument(
        "--output", help="also write a minimal onboarding file to this path"
    )
    access_provision.set_defaults(func=_cmd_access_provision)

    doctor = subparsers.add_parser(
        "doctor",
        help="Check lineage integrity — fail-closed report, CLI-only, never model-callable.",
    )
    doctor.add_argument("--principal", required=True, help="principal id to act as (operator mode)")
    doctor.add_argument("--project", required=True)
    doctor.set_defaults(func=_cmd_doctor)

    create = subparsers.add_parser("create", help="Create a new durable artifact.")
    create.add_argument("--principal", required=True, help="principal id to act as (operator mode)")
    create.add_argument("--project", required=True)
    create.add_argument("--artifact-id", required=True)
    create.add_argument("--artifact-type", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--content", required=True)
    create.add_argument("--rationale")
    create.add_argument("--tags", help="comma-separated")
    create.set_defaults(func=_cmd_create)

    supersede = subparsers.add_parser("supersede", help="Supersede an existing active artifact.")
    supersede.add_argument("--principal", required=True, help="principal id to act as (operator mode)")
    supersede.add_argument("--project", required=True)
    supersede.add_argument("--old-id", required=True)
    supersede.add_argument("--content", required=True)
    supersede.add_argument("--reason", required=True)
    supersede.add_argument("--title")
    supersede.add_argument("--tags", help="comma-separated")
    supersede.set_defaults(func=_cmd_supersede)

    search = subparsers.add_parser("search", help="Raw deterministic candidate lookup.")
    search.add_argument("--principal", required=True, help="principal id to act as (operator mode)")
    search.add_argument("--project", required=True)
    search.add_argument("--artifact-type")
    search.add_argument("--tags", help="comma-separated")
    search.add_argument("--status", default="active")
    search.set_defaults(func=_cmd_search)

    get_current = subparsers.add_parser(
        "get-current", help="Resolved, conflict/integrity-checked current context."
    )
    get_current.add_argument("--principal", required=True, help="principal id to act as (operator mode)")
    get_current.add_argument("--project", required=True)
    get_current.add_argument("--artifact-type")
    get_current.add_argument("--tags", help="comma-separated")
    get_current.set_defaults(func=_cmd_get_current)

    history = subparsers.add_parser("history", help="Full lineage history, oldest first.")
    history.add_argument("--principal", required=True, help="principal id to act as (operator mode)")
    history.add_argument("--project", required=True)
    history.add_argument("--lineage-id", required=True)
    history.set_defaults(func=_cmd_history)

    org = subparsers.add_parser("org", help="Control-plane: organizations.")
    org_sub = org.add_subparsers(dest="org_command", required=True)
    org_create = org_sub.add_parser("create", help="Create an organization and its first owner.")
    org_create.add_argument("--name", required=True)
    org_create.add_argument("--owner-display-name", required=True)
    org_create.set_defaults(func=_cmd_org_create)
    org_list = org_sub.add_parser(
        "list", help="List every organization in this deployment (trusted-operator scope, like `org create`)."
    )
    org_list.set_defaults(func=_cmd_org_list)
    org_show = org_sub.add_parser("show", help="Show one organization.")
    org_show.add_argument(
        "--organization-id", required=True, help="the specific organization to show"
    )
    org_show.set_defaults(func=_cmd_org_show)

    project = subparsers.add_parser("project", help="Control-plane: projects.")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_create = project_sub.add_parser("create", help="Create a project in an organization.")
    project_create.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    project_create.add_argument("--organization-id", required=True)
    project_create.add_argument("--name", required=True)
    project_create.set_defaults(func=_cmd_project_create)
    project_list = project_sub.add_parser("list", help="List every project in an organization (owner-only).")
    project_list.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    project_list.add_argument(
        "--organization-id",
        help="filter to one organization (default: the authenticated actor's own organization)",
    )
    project_list.set_defaults(func=_cmd_project_list)
    project_show = project_sub.add_parser("show", help="Show one project (owner-only, own org).")
    project_show.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    project_show.add_argument("--project-id", required=True, help="the specific project to show")
    project_show.set_defaults(func=_cmd_project_show)

    principal = subparsers.add_parser("principal", help="Control-plane: principals.")
    principal_sub = principal.add_subparsers(dest="principal_command", required=True)
    principal_create = principal_sub.add_parser("create", help="Create a principal in an organization.")
    principal_create.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    principal_create.add_argument("--organization-id", required=True)
    principal_create.add_argument("--type", required=True, choices=["human", "service", "agent"])
    principal_create.add_argument("--display-name", required=True)
    principal_create.add_argument("--role", default="member", choices=["member", "owner"])
    principal_create.set_defaults(func=_cmd_principal_create)
    principal_list = principal_sub.add_parser(
        "list", help="List every principal in an organization (owner-only)."
    )
    principal_list.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    principal_list.add_argument(
        "--organization-id",
        help="filter to one organization (default: the authenticated actor's own organization)",
    )
    principal_list.set_defaults(func=_cmd_principal_list)
    principal_show = principal_sub.add_parser("show", help="Show one principal (owner-only, own org).")
    principal_show.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    principal_show.add_argument(
        "--principal-id", required=True, help="the specific principal to show"
    )
    principal_show.set_defaults(func=_cmd_principal_show)

    grant = subparsers.add_parser("grant", help="Control-plane: grants (principal x project permissions).")
    grant_sub = grant.add_subparsers(dest="grant_command", required=True)
    grant_create = grant_sub.add_parser("create", help="Grant a principal permissions on a project.")
    grant_create.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    grant_create.add_argument("--principal-id", required=True)
    grant_create.add_argument("--project-id", required=True)
    grant_create.add_argument("--permissions", required=True, help="comma-separated: read,write")
    grant_create.set_defaults(func=_cmd_grant_create)
    grant_list = grant_sub.add_parser(
        "list", help="List every grant held by one principal (owner-only, own org)."
    )
    grant_list.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    grant_list.add_argument("--principal-id", required=True)
    grant_list.set_defaults(func=_cmd_grant_list)
    grant_revoke = grant_sub.add_parser("revoke", help="Revoke a principal's grant on a project.")
    grant_revoke.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    grant_revoke.add_argument("--principal-id", required=True)
    grant_revoke.add_argument("--project-id", required=True)
    grant_revoke.set_defaults(func=_cmd_grant_revoke)

    credential = subparsers.add_parser("credential", help="Control-plane: credentials.")
    credential_sub = credential.add_subparsers(dest="credential_command", required=True)
    credential_issue = credential_sub.add_parser(
        "issue", help="Issue a credential for a principal — prints the raw token once."
    )
    credential_issue.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    credential_issue.add_argument("--principal-id", required=True)
    credential_issue.add_argument("--display-name")
    credential_issue.add_argument("--expires-at", help="ISO 8601, e.g. 2027-01-01T00:00:00+00:00")
    credential_issue.add_argument(
        "--no-connection-help",
        action="store_true",
        help="skip printing generic AI-client connection instructions after issuing",
    )
    credential_issue.set_defaults(func=_cmd_credential_issue)
    credential_list = credential_sub.add_parser(
        "list", help="List every credential issued to one principal — never prints the raw token or its hash."
    )
    credential_list.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    credential_list.add_argument("--principal-id", required=True)
    credential_list.set_defaults(func=_cmd_credential_list)
    credential_revoke = credential_sub.add_parser("revoke", help="Revoke a credential.")
    credential_revoke.add_argument("--actor", help=(
        "owner principal id performing this action (default: derived from the authenticated credential — MAK4I_TOKEN, or the local `mak4i init` credential; pass explicitly to override)"
    ))
    credential_revoke.add_argument("--credential-id", required=True)
    credential_revoke.set_defaults(func=_cmd_credential_revoke)

    return parser


def _redact_db_url(url: str) -> str:
    """Strip any embedded credentials from a database URL before it can
    appear in a user-facing error message. `MAK4I_CONTROL_PLANE_DB` can
    be a `postgresql+psycopg://user:password@host/db` URL — already
    flagged as a secret in docs/DEPLOYMENT.md — so this must never be
    skipped just because the URL looks like a local SQLite path."""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable URL, redacted>"
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        parts = parts._replace(netloc=f"***:***@{host}" if host else "***:***")
    return urlunsplit(parts)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # No blanket local-environment resolution here: `serve` and
        # `init` each apply their own (see their functions), and every
        # other command resolves ambient `.mak4i/` lazily, inside
        # `_make_control_plane()`, only when nothing has already been
        # explicitly configured.
        return args.func(args)
    except _CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"error: missing required environment variable {exc}", file=sys.stderr)
        return 1
    except IdentityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        db_url = _redact_db_url(os.environ.get("MAK4I_CONTROL_PLANE_DB", ""))
        print(
            f"error: could not use the configured control-plane database "
            f"({db_url}) — {type(exc).__name__}.\n"
            "Check MAK4I_CONTROL_PLANE_DB, that the database is reachable, "
            "and that its schema has been created (`mak4i init` for a local "
            "environment, `MAK4I_CONTROL_PLANE_CREATE_TABLES=1` for a "
            "throwaway one, or `alembic upgrade head` for a real one).",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
