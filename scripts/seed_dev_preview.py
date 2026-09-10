"""Seeds a multi-organization acceptance-flow fixture into a fresh
control-plane database: two organizations, each with its own projects and
one principal holding read+write on every project in its own org and
nothing elsewhere. Prints every id and both principals' raw tokens (shown
once, same as `mak4i credential issue`).

The organization and project names below are arbitrary sample data — the
seeding logic is entirely generic (see CLAUDE.md's "no scenario-specific
logic in the core" rule). Change them freely.

Not idempotent by design — a seed script populates a fresh database. Point
MAK4I_CONTROL_PLANE_DB at a new file for a clean run rather than rerunning
against one that's already seeded (rerunning against the same one just
creates a second copy of every organization/project/principal).

Usage:
    uv run python scripts/seed_dev_preview.py
    MAK4I_CONTROL_PLANE_DB=sqlite:///./dev-preview.db \
    MAK4I_CONTROL_PLANE_CREATE_TABLES=1 \
        uv run python scripts/seed_dev_preview.py
"""

from __future__ import annotations

import os

from mak4i.config import build_control_plane_from_env
from mak4i.identity import ControlPlane, Project

ORG_A_NAME = "Northwind Analytics"
ORG_B_NAME = "Meridian Software"

ORG_A_PROJECTS = ["Payments Service", "Mobile App", "Marketing Site"]
ORG_B_PROJECTS = ["Platform API", "Docs Portal"]


def _seed_org(
    control_plane: ControlPlane,
    *,
    org_name: str,
    owner_display_name: str,
    project_names: list[str],
    dev_display_name: str,
):
    organization, owner = control_plane.onboard_organization(
        organization_name=org_name, owner_display_name=owner_display_name
    )
    projects: list[Project] = [
        control_plane.create_project(actor=owner, organization_id=organization.organization_id, name=name)
        for name in project_names
    ]
    principal = control_plane.create_principal(
        actor=owner,
        organization_id=organization.organization_id,
        type="human",
        display_name=dev_display_name,
    )
    for project in projects:
        control_plane.grant(
            actor=owner,
            principal_id=principal.principal_id,
            project_id=project.project_id,
            permissions=["read", "write"],
        )
    _credential, raw_token = control_plane.issue_credential(
        actor=owner, principal_id=principal.principal_id, display_name="seed_dev_preview.py"
    )
    return organization, projects, principal, raw_token


def main() -> None:
    control_plane = build_control_plane_from_env()

    org_a, org_a_projects, principal_a, token_a = _seed_org(
        control_plane,
        org_name=ORG_A_NAME,
        owner_display_name="Org A Owner",
        project_names=ORG_A_PROJECTS,
        dev_display_name="Principal A",
    )
    org_b, org_b_projects, principal_b, token_b = _seed_org(
        control_plane,
        org_name=ORG_B_NAME,
        owner_display_name="Org B Owner",
        project_names=ORG_B_PROJECTS,
        dev_display_name="Principal B",
    )

    db = os.environ.get("MAK4I_CONTROL_PLANE_DB", "(default: sqlite:///./mak4i-control-plane.db)")
    print(f"MAK4I_CONTROL_PLANE_DB = {db}")
    print()
    print(f"Organization: {ORG_A_NAME} ({org_a.organization_id})")
    for project in org_a_projects:
        print(f"  project {project.name!r}: {project.project_id}")
    print(f"  Principal A: {principal_a.principal_id}")
    print(f"  Principal A token (shown once — store it now): {token_a}")
    print()
    print(f"Organization: {ORG_B_NAME} ({org_b.organization_id})")
    for project in org_b_projects:
        print(f"  project {project.name!r}: {project.project_id}")
    print(f"  Principal B: {principal_b.principal_id}")
    print(f"  Principal B token (shown once — store it now): {token_b}")
    print()
    print(f"Principal A holds read+write on every {ORG_A_NAME} project and")
    print(f"nothing on {ORG_B_NAME}'s; Principal B is the mirror image. Try, e.g.:")
    print(f"  uv run mak4i get-current --principal {principal_a.principal_id} --project {org_a_projects[0].project_id}")
    print(
        f"  uv run mak4i get-current --principal {principal_a.principal_id} "
        f"--project {org_b_projects[0].project_id}  # denied"
    )


if __name__ == "__main__":
    main()
