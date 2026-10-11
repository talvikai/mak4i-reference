from __future__ import annotations

from mak4i.identity.models import (
    Credential,
    Grant,
    Organization,
    Principal,
    Project,
)


class InMemoryControlPlaneStore:
    """Dict-backed `ControlPlaneStore` for unit tests and offline dev.

    Mirrors `LocalJSONStore`'s role for artifacts: never where deployed
    data lives, just a fast, dependency-free implementation of the exact
    same contract `SqlControlPlaneStore` implements, so the control-plane
    and authorization tests can run without a database.
    """

    def __init__(self) -> None:
        self._organizations: dict[str, Organization] = {}
        self._principals: dict[str, Principal] = {}
        self._projects: dict[str, Project] = {}
        self._grants: dict[tuple[str, str], Grant] = {}
        self._credentials: dict[str, Credential] = {}

    # -- organizations --
    def get_organization(self, organization_id: str) -> Organization | None:
        return self._organizations.get(organization_id)

    def put_organization(self, organization: Organization) -> None:
        self._organizations[organization.organization_id] = organization

    def list_organizations(self) -> list[Organization]:
        return list(self._organizations.values())

    # -- principals --
    def get_principal(self, principal_id: str) -> Principal | None:
        return self._principals.get(principal_id)

    def put_principal(self, principal: Principal) -> None:
        self._principals[principal.principal_id] = principal

    def get_principal_by_agent_id(self, organization_id: str, agent_id: str) -> Principal | None:
        return next(
            (
                p
                for p in self._principals.values()
                if p.organization_id == organization_id and p.agent_id == agent_id
            ),
            None,
        )

    def list_principals(self, organization_id: str) -> list[Principal]:
        return [
            p for p in self._principals.values() if p.organization_id == organization_id
        ]

    # -- projects --
    def get_project(self, project_id: str) -> Project | None:
        return self._projects.get(project_id)

    def put_project(self, project: Project) -> None:
        self._projects[project.project_id] = project

    def list_projects(self, organization_id: str) -> list[Project]:
        return [
            p for p in self._projects.values() if p.organization_id == organization_id
        ]

    # -- grants --
    def get_grant(self, principal_id: str, project_id: str) -> Grant | None:
        return self._grants.get((principal_id, project_id))

    def put_grant(self, grant: Grant) -> None:
        self._grants[(grant.principal_id, grant.project_id)] = grant

    def delete_grant(self, principal_id: str, project_id: str) -> None:
        self._grants.pop((principal_id, project_id), None)

    def list_grants_for_principal(self, principal_id: str) -> list[Grant]:
        return [g for g in self._grants.values() if g.principal_id == principal_id]

    # -- credentials --
    def get_credential(self, credential_id: str) -> Credential | None:
        return self._credentials.get(credential_id)

    def put_credential(self, credential: Credential) -> None:
        self._credentials[credential.credential_id] = credential

    def list_credentials(self, principal_id: str) -> list[Credential]:
        return [
            c for c in self._credentials.values() if c.principal_id == principal_id
        ]

    def lookup_credential_by_hash(self, token_hash: str) -> Credential | None:
        for credential in self._credentials.values():
            if credential.token_hash == token_hash:
                return credential
        return None
