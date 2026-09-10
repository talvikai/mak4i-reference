"""Operator CLI — the same MAK4IEngine used by tests and the MCP server,
callable directly by a human (requirements §9: "the internal engine should
remain callable by tests/CLI without MCP"). `doctor` and the control-plane
subcommands (`org`, `project`, `principal`, `grant`, `credential`) are
CLI-only and never model-callable (MVP_ARCHITECTURE.md §10).

`init` / `serve` / `preview provision` are onboarding conveniences: each is
a thin wrapper over the same control-plane calls the granular subcommands
make. `init` + `serve` are the local/self-hosted first-run path (they read
and write `.mak4i/`, a gitignored local config — see `localconfig.py`);
`preview provision` is an operator-only shortcut for onboarding one known
Talvik-hosted Developer Preview tester. None of them add business logic,
weaken authorization, or touch the hosted environment from the local path.

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

from mak4i import localconfig
from mak4i.api import OPERATOR_IMPERSONATION, ArtifactNotActiveError, MAK4IEngine
from mak4i.audit import AuditLogger
from mak4i.config import build_control_plane_from_env, build_store_from_env
from mak4i.identity import AccessDeniedError, ControlPlane, IdentityError, Principal
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


def _cmd_project_create(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_principal(control_plane, args.actor)
    project = control_plane.create_project(
        actor=actor, organization_id=args.organization_id, name=args.name
    )
    _print_json(project)
    return 0


def _cmd_principal_create(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_principal(control_plane, args.actor)
    principal = control_plane.create_principal(
        actor=actor,
        organization_id=args.organization_id,
        type=args.type,
        display_name=args.display_name,
        role=args.role,
    )
    _print_json(principal)
    return 0


def _cmd_grant_create(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_principal(control_plane, args.actor)
    grant = control_plane.grant(
        actor=actor,
        principal_id=args.principal_id,
        project_id=args.project_id,
        permissions=_split_permissions(args.permissions),
    )
    _print_json(grant)
    return 0


def _cmd_grant_revoke(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_principal(control_plane, args.actor)
    control_plane.revoke_grant(
        actor=actor, principal_id=args.principal_id, project_id=args.project_id
    )
    print(f"revoked grant for principal {args.principal_id!r} on project {args.project_id!r}")
    return 0


def _cmd_credential_issue(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_principal(control_plane, args.actor)
    expires_at = datetime.fromisoformat(args.expires_at) if args.expires_at else None
    credential, raw_token = control_plane.issue_credential(
        actor=actor,
        principal_id=args.principal_id,
        display_name=args.display_name,
        expires_at=expires_at,
    )
    print(f"credential_id: {credential.credential_id}")
    print(f"token (shown once — store it now): {raw_token}")
    return 0


def _cmd_credential_revoke(args: argparse.Namespace) -> int:
    control_plane = _make_control_plane()
    actor = _resolve_principal(control_plane, args.actor)
    credential = control_plane.revoke_credential(actor=actor, credential_id=args.credential_id)
    _print_json(credential)
    return 0


# -- onboarding convenience commands ---------------------------------------
#
# `init`, `serve`, and `preview provision` add no business logic: each is a
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
    os.environ.setdefault("MAK4I_CONTROL_PLANE_DB", localconfig.default_control_plane_db())
    os.environ.setdefault("MAK4I_STORE", "local")
    os.environ.setdefault("MAK4I_LOCAL_STORE_DIR", localconfig.default_local_store_dir())
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
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start the existing MCP server against the local environment created
    by `mak4i init`. This wraps `mak4i.mcp_server.main()` — it is not a
    second server implementation."""
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
    # mak4i serve` always uses the credential from that init.
    overridden = localconfig.overridden_local_env_keys(config, token)
    localconfig.apply_to_env(config, token)
    if overridden:
        print(
            f"note: using the local `mak4i init` environment; ignoring "
            f"{', '.join(sorted(overridden))} from the shell.",
            file=sys.stderr,
        )

    os.environ.setdefault("MAK4I_TRANSPORT", "stdio")
    transport = os.environ["MAK4I_TRANSPORT"]

    # Banner goes to stderr — for the stdio transport, stdout carries the
    # MCP JSON-RPC stream and must not be written to.
    print("MAK4I local server starting...", file=sys.stderr)
    print(f"Organization: {config.organization_name}", file=sys.stderr)
    print(f"Project: {config.project_name}", file=sys.stderr)
    print(f"Transport: {transport}", file=sys.stderr)
    print("\nMCP server ready.", file=sys.stderr)

    from mak4i import mcp_server

    mcp_server.main()
    return 0


def _cmd_preview_provision(args: argparse.Namespace) -> int:
    """Operator-only: provision one known external tester into the
    Talvik-hosted Developer Preview. Runs against whatever control plane
    the operator's environment points at. Not public self-service.

    Reuses the same `ControlPlane` calls as the granular commands: a new
    organization for the tester, a `member` principal (never an owner), a
    project, a read+write grant on that project only, and one credential.
    """
    # Fail closed BEFORE anything is provisioned: the tester needs the
    # endpoint, so there is no point minting an organization/credential we
    # then can't hand off.
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

    tester_display_name = _prompt("Tester display name", args.display_name)
    organization_name = _prompt("Organization name", args.org_name)
    project_name = _prompt("Initial project", args.project_name)

    print("\nProvisioning Developer Preview access...\n", file=sys.stderr)

    control_plane = _make_control_plane()
    organization, owner = control_plane.onboard_organization(
        organization_name=organization_name,
        owner_display_name=f"{organization_name} (operator)",
    )
    print("✓ Organization created", file=sys.stderr)
    tester = control_plane.create_principal(
        actor=owner,
        organization_id=organization.organization_id,
        type="human",
        display_name=tester_display_name,
        role="member",
    )
    print("✓ Principal created", file=sys.stderr)
    project = control_plane.create_project(
        actor=owner, organization_id=organization.organization_id, name=project_name
    )
    print("✓ Project created", file=sys.stderr)
    control_plane.grant(
        actor=owner,
        principal_id=tester.principal_id,
        project_id=project.project_id,
        permissions=["read", "write"],
    )
    print("✓ READ/WRITE grant created", file=sys.stderr)
    _credential, raw_token = control_plane.issue_credential(
        actor=owner, principal_id=tester.principal_id, display_name=tester_display_name
    )
    print("✓ Credential issued", file=sys.stderr)

    print("\nTester access:\n")
    print(f"Endpoint: {endpoint}")
    print(f"Project:  {project.project_id}")
    print(f"Token:    {raw_token}")

    if args.output:
        _write_tester_file(args.output, endpoint=endpoint, project_id=project.project_id, token=raw_token)
        print(f"\nWritten to {args.output} (contains the token — handle it securely).", file=sys.stderr)

    return 0


_TESTER_DOCS_URL = "https://github.com/talvikai/mak4i-reference#hosted-developer-preview"


def _write_tester_file(path: str, *, endpoint: str, project_id: str, token: str) -> None:
    """Minimal onboarding file for one tester. Deliberately contains only
    what the tester needs to connect a client — never infrastructure,
    database, service-account, or operator detail."""
    body = (
        "MAK4I Developer Preview\n\n"
        f"Endpoint: {endpoint}\n"
        f"Token: {token}\n"
        f"Project: {project_id}\n\n"
        "Documentation:\n"
        f"{_TESTER_DOCS_URL}\n"
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
    serve.set_defaults(func=_cmd_serve)

    preview = subparsers.add_parser("preview", help="Operator: Talvik-hosted Developer Preview provisioning.")
    preview_sub = preview.add_subparsers(dest="preview_command", required=True)
    preview_provision = preview_sub.add_parser(
        "provision", help="Provision one known external tester (operator-only; not public self-service)."
    )
    preview_provision.add_argument("--display-name", help="tester display name (prompted if omitted)")
    preview_provision.add_argument("--org-name", help="tester organization name (prompted if omitted)")
    preview_provision.add_argument("--project-name", help="initial project name (prompted if omitted)")
    preview_provision.add_argument(
        "--endpoint",
        help="hosted endpoint URL to hand the tester — required (or set $MAK4I_PUBLIC_ENDPOINT)",
    )
    preview_provision.add_argument(
        "--output", help="also write a minimal tester onboarding file to this path"
    )
    preview_provision.set_defaults(func=_cmd_preview_provision)

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

    project = subparsers.add_parser("project", help="Control-plane: projects.")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_create = project_sub.add_parser("create", help="Create a project in an organization.")
    project_create.add_argument("--actor", required=True, help="owner principal id performing this action")
    project_create.add_argument("--organization-id", required=True)
    project_create.add_argument("--name", required=True)
    project_create.set_defaults(func=_cmd_project_create)

    principal = subparsers.add_parser("principal", help="Control-plane: principals.")
    principal_sub = principal.add_subparsers(dest="principal_command", required=True)
    principal_create = principal_sub.add_parser("create", help="Create a principal in an organization.")
    principal_create.add_argument("--actor", required=True, help="owner principal id performing this action")
    principal_create.add_argument("--organization-id", required=True)
    principal_create.add_argument("--type", required=True, choices=["human", "service", "agent"])
    principal_create.add_argument("--display-name", required=True)
    principal_create.add_argument("--role", default="member", choices=["member", "owner"])
    principal_create.set_defaults(func=_cmd_principal_create)

    grant = subparsers.add_parser("grant", help="Control-plane: grants (principal x project permissions).")
    grant_sub = grant.add_subparsers(dest="grant_command", required=True)
    grant_create = grant_sub.add_parser("create", help="Grant a principal permissions on a project.")
    grant_create.add_argument("--actor", required=True, help="owner principal id performing this action")
    grant_create.add_argument("--principal-id", required=True)
    grant_create.add_argument("--project-id", required=True)
    grant_create.add_argument("--permissions", required=True, help="comma-separated: read,write")
    grant_create.set_defaults(func=_cmd_grant_create)
    grant_revoke = grant_sub.add_parser("revoke", help="Revoke a principal's grant on a project.")
    grant_revoke.add_argument("--actor", required=True, help="owner principal id performing this action")
    grant_revoke.add_argument("--principal-id", required=True)
    grant_revoke.add_argument("--project-id", required=True)
    grant_revoke.set_defaults(func=_cmd_grant_revoke)

    credential = subparsers.add_parser("credential", help="Control-plane: credentials.")
    credential_sub = credential.add_subparsers(dest="credential_command", required=True)
    credential_issue = credential_sub.add_parser(
        "issue", help="Issue a credential for a principal — prints the raw token once."
    )
    credential_issue.add_argument("--actor", required=True, help="owner principal id performing this action")
    credential_issue.add_argument("--principal-id", required=True)
    credential_issue.add_argument("--display-name")
    credential_issue.add_argument("--expires-at", help="ISO 8601, e.g. 2027-01-01T00:00:00+00:00")
    credential_issue.set_defaults(func=_cmd_credential_issue)
    credential_revoke = credential_sub.add_parser("revoke", help="Revoke a credential.")
    credential_revoke.add_argument("--actor", required=True, help="owner principal id performing this action")
    credential_revoke.add_argument("--credential-id", required=True)
    credential_revoke.set_defaults(func=_cmd_credential_revoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
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


if __name__ == "__main__":
    raise SystemExit(main())
