from datetime import timedelta

import pytest

from mak4i.identity import (
    AccessDeniedError,
    ControlPlane,
    CredentialInvalidError,
    CrossOrganizationGrantError,
    LastOwnerError,
    Organization,
    ProjectAlreadyExistsError,
    utc_now,
)
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.identity.sql_store import SqlControlPlaneStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryControlPlaneStore()
    return SqlControlPlaneStore(
        f"sqlite:///{tmp_path / 'control_plane.db'}", create_tables=True
    )


@pytest.fixture
def cp(store):
    return ControlPlane(store)


@pytest.fixture
def org_and_owner(cp):
    return cp.onboard_organization(
        organization_name="Example Org", owner_display_name="Owner One"
    )


# -- onboarding -----------------------------------------------------------


def test_onboard_creates_org_and_active_owner(cp):
    org, owner = cp.onboard_organization(
        organization_name="WD Technology Solutions", owner_display_name="Ada"
    )
    assert org.status == "active"
    assert owner.organization_id == org.organization_id
    assert owner.role == "owner"
    assert owner.status == "active"


# -- authentication -----------------------------------------------------------


def test_authenticate_round_trips_a_valid_credential(cp, org_and_owner):
    _org, owner = org_and_owner
    credential, raw = cp.issue_credential(actor=owner, principal_id=owner.principal_id)
    assert raw.startswith("mak4i_")
    assert raw != credential.token_hash

    resolved = cp.authenticate(raw)
    assert resolved.principal_id == owner.principal_id


def test_authenticate_rejects_unknown_token(cp):
    with pytest.raises(CredentialInvalidError) as exc:
        cp.authenticate("mak4i_nope")
    assert exc.value.reason == "unknown"
    assert str(exc.value) == "invalid or expired credential"


def test_authenticate_rejects_revoked_credential(cp, org_and_owner):
    _org, owner = org_and_owner
    credential, raw = cp.issue_credential(actor=owner, principal_id=owner.principal_id)
    cp.revoke_credential(actor=owner, credential_id=credential.credential_id)
    with pytest.raises(CredentialInvalidError) as exc:
        cp.authenticate(raw)
    assert exc.value.reason == "revoked"


def test_authenticate_rejects_expired_credential(cp, org_and_owner):
    _org, owner = org_and_owner
    _credential, raw = cp.issue_credential(
        actor=owner,
        principal_id=owner.principal_id,
        expires_at=utc_now() - timedelta(hours=1),
    )
    with pytest.raises(CredentialInvalidError) as exc:
        cp.authenticate(raw)
    assert exc.value.reason == "expired"


def test_authenticate_rejects_when_principal_deactivated(cp, store, org_and_owner):
    _org, owner = org_and_owner
    second_owner = cp.create_principal(
        actor=owner,
        organization_id=owner.organization_id,
        type="human",
        display_name="Owner Two",
        role="owner",
    )
    _credential, raw = cp.issue_credential(
        actor=owner, principal_id=second_owner.principal_id
    )
    cp.deactivate_principal(actor=owner, principal_id=second_owner.principal_id)
    with pytest.raises(CredentialInvalidError) as exc:
        cp.authenticate(raw)
    assert exc.value.reason == "principal_inactive"


def test_authenticate_rejects_when_organization_suspended(cp, store, org_and_owner):
    org, owner = org_and_owner
    _credential, raw = cp.issue_credential(actor=owner, principal_id=owner.principal_id)
    store.put_organization(org.model_copy(update={"status": "suspended"}))
    with pytest.raises(CredentialInvalidError) as exc:
        cp.authenticate(raw)
    assert exc.value.reason == "organization_inactive"


# -- owner gate on administration -------------------------------------------


def test_member_cannot_administer(cp, org_and_owner):
    _org, owner = org_and_owner
    member = cp.create_principal(
        actor=owner,
        organization_id=owner.organization_id,
        type="human",
        display_name="Member",
    )
    with pytest.raises(AccessDeniedError):
        cp.create_project(
            actor=member, organization_id=owner.organization_id, name="Nope"
        )


def test_owner_cannot_administer_another_organization(cp):
    _org_a, owner_a = cp.onboard_organization(
        organization_name="Org A", owner_display_name="A"
    )
    org_b, _owner_b = cp.onboard_organization(
        organization_name="Org B", owner_display_name="B"
    )
    with pytest.raises(AccessDeniedError):
        cp.create_project(
            actor=owner_a, organization_id=org_b.organization_id, name="Cross"
        )


def test_create_project_duplicate_active_name_is_rejected(cp, org_and_owner):
    _org, owner = org_and_owner
    original = cp.create_project(
        actor=owner, organization_id=owner.organization_id, name="Schedovia"
    )

    with pytest.raises(ProjectAlreadyExistsError) as exc:
        cp.create_project(
            actor=owner, organization_id=owner.organization_id, name="Schedovia"
        )
    assert "Schedovia" in str(exc.value)
    assert "already exists" in str(exc.value)
    # also a ValueError — existing callers that catch ValueError keep working.
    assert isinstance(exc.value, ValueError)

    # The original project row is untouched by the rejected attempt.
    projects = cp.list_projects(actor=owner, organization_id=owner.organization_id)
    assert [p.project_id for p in projects] == [original.project_id]


# -- grants -------------------------------------------------------------------


def test_grant_resolution_and_authorized_projects(cp, org_and_owner):
    _org, owner = org_and_owner
    project = cp.create_project(
        actor=owner, organization_id=owner.organization_id, name="Schedovia"
    )
    dev = cp.create_principal(
        actor=owner,
        organization_id=owner.organization_id,
        type="human",
        display_name="Dev",
    )

    assert cp.effective_permissions(dev, project.project_id) == []

    cp.grant(
        actor=owner,
        principal_id=dev.principal_id,
        project_id=project.project_id,
        permissions=["read", "write"],
    )
    assert cp.effective_permissions(dev, project.project_id) == ["read", "write"]

    authorized = cp.list_authorized_projects(dev)
    assert [ap.project.project_id for ap in authorized] == [project.project_id]
    assert cp.list_authorized_projects(dev, permission="write")
    cp.revoke_grant(
        actor=owner, principal_id=dev.principal_id, project_id=project.project_id
    )
    assert cp.effective_permissions(dev, project.project_id) == []
    assert cp.list_authorized_projects(dev) == []


def test_grant_cannot_cross_organizations(cp):
    _org_a, owner_a = cp.onboard_organization(
        organization_name="Org A", owner_display_name="A"
    )
    _org_b, owner_b = cp.onboard_organization(
        organization_name="Org B", owner_display_name="B"
    )
    project_b = cp.create_project(
        actor=owner_b, organization_id=owner_b.organization_id, name="B Project"
    )
    dev_a = cp.create_principal(
        actor=owner_a,
        organization_id=owner_a.organization_id,
        type="human",
        display_name="Dev A",
    )
    with pytest.raises(ValueError):
        cp.grant(
            actor=owner_a,
            principal_id=dev_a.principal_id,
            project_id=project_b.project_id,
            permissions=["read"],
        )


def test_effective_permissions_empty_for_archived_project(cp, store, org_and_owner):
    _org, owner = org_and_owner
    project = cp.create_project(
        actor=owner, organization_id=owner.organization_id, name="Old"
    )
    cp.grant(
        actor=owner,
        principal_id=owner.principal_id,
        project_id=project.project_id,
        permissions=["read"],
    )
    store.put_project(project.model_copy(update={"status": "archived"}))
    assert cp.effective_permissions(owner, project.project_id) == []


# -- credentials ------------------------------------------------------------


def test_two_credentials_for_one_principal_are_independent(cp, org_and_owner):
    _org, owner = org_and_owner
    cred_a, raw_a = cp.issue_credential(actor=owner, principal_id=owner.principal_id)
    _cred_b, raw_b = cp.issue_credential(actor=owner, principal_id=owner.principal_id)

    cp.revoke_credential(actor=owner, credential_id=cred_a.credential_id)

    with pytest.raises(CredentialInvalidError):
        cp.authenticate(raw_a)
    assert cp.authenticate(raw_b).principal_id == owner.principal_id


# -- last-owner guard ------------------------------------------------------


def test_cannot_deactivate_the_last_active_owner(cp, org_and_owner):
    _org, owner = org_and_owner
    with pytest.raises(LastOwnerError):
        cp.deactivate_principal(actor=owner, principal_id=owner.principal_id)


def test_can_deactivate_an_owner_once_a_second_exists(cp, org_and_owner):
    _org, owner = org_and_owner
    owner_two = cp.create_principal(
        actor=owner,
        organization_id=owner.organization_id,
        type="human",
        display_name="Owner Two",
        role="owner",
    )
    result = cp.deactivate_principal(actor=owner_two, principal_id=owner.principal_id)
    assert result.status == "deactivated"


# -- self-service inspection (org/principal/project/grant/credential) -----


def test_list_organizations_is_trusted_operator_only_and_sees_everything(cp):
    org_a, _owner_a = cp.onboard_organization(organization_name="Org A", owner_display_name="A")
    org_b, _owner_b = cp.onboard_organization(organization_name="Org B", owner_display_name="B")
    ids = {org.organization_id for org in cp.list_organizations()}
    assert {org_a.organization_id, org_b.organization_id} <= ids
    assert cp.get_organization(org_a.organization_id).name == "Org A"
    assert cp.get_organization("org_does_not_exist") is None


def test_list_and_show_principals_scoped_to_owners_own_org(cp, org_and_owner):
    _org, owner = org_and_owner
    dev = cp.create_principal(
        actor=owner, organization_id=owner.organization_id, type="human", display_name="Dev"
    )
    listed_ids = {p.principal_id for p in cp.list_principals(actor=owner, organization_id=owner.organization_id)}
    assert {owner.principal_id, dev.principal_id} == listed_ids
    assert cp.show_principal(actor=owner, principal_id=dev.principal_id).display_name == "Dev"


def test_list_and_show_principals_deny_cross_org(cp):
    _org_a, owner_a = cp.onboard_organization(organization_name="Org A", owner_display_name="A")
    org_b, owner_b = cp.onboard_organization(organization_name="Org B", owner_display_name="B")
    dev_b = cp.create_principal(
        actor=owner_b, organization_id=org_b.organization_id, type="human", display_name="Dev B"
    )
    with pytest.raises(AccessDeniedError):
        cp.list_principals(actor=owner_a, organization_id=org_b.organization_id)
    with pytest.raises(AccessDeniedError):
        cp.show_principal(actor=owner_a, principal_id=dev_b.principal_id)


def test_list_and_show_projects_scoped_to_owners_own_org(cp, org_and_owner):
    _org, owner = org_and_owner
    project = cp.create_project(actor=owner, organization_id=owner.organization_id, name="Schedovia")
    listed_ids = {p.project_id for p in cp.list_projects(actor=owner, organization_id=owner.organization_id)}
    assert listed_ids == {project.project_id}
    assert cp.show_project(actor=owner, project_id=project.project_id).name == "Schedovia"


def test_list_and_show_projects_deny_cross_org(cp):
    _org_a, owner_a = cp.onboard_organization(organization_name="Org A", owner_display_name="A")
    org_b, owner_b = cp.onboard_organization(organization_name="Org B", owner_display_name="B")
    project_b = cp.create_project(actor=owner_b, organization_id=org_b.organization_id, name="B Project")
    with pytest.raises(AccessDeniedError):
        cp.list_projects(actor=owner_a, organization_id=org_b.organization_id)
    with pytest.raises(AccessDeniedError):
        cp.show_project(actor=owner_a, project_id=project_b.project_id)


def test_list_grants_scoped_to_the_principals_own_org(cp, org_and_owner):
    _org, owner = org_and_owner
    project = cp.create_project(actor=owner, organization_id=owner.organization_id, name="Schedovia")
    dev = cp.create_principal(
        actor=owner, organization_id=owner.organization_id, type="human", display_name="Dev"
    )
    cp.grant(actor=owner, principal_id=dev.principal_id, project_id=project.project_id, permissions=["read"])
    grants = cp.list_grants(actor=owner, principal_id=dev.principal_id)
    assert [g.project_id for g in grants] == [project.project_id]


def test_list_grants_denies_cross_org(cp):
    _org_a, owner_a = cp.onboard_organization(organization_name="Org A", owner_display_name="A")
    org_b, owner_b = cp.onboard_organization(organization_name="Org B", owner_display_name="B")
    dev_b = cp.create_principal(
        actor=owner_b, organization_id=org_b.organization_id, type="human", display_name="Dev B"
    )
    with pytest.raises(AccessDeniedError):
        cp.list_grants(actor=owner_a, principal_id=dev_b.principal_id)


def test_list_credentials_scoped_to_the_principals_own_org_and_hashes_not_raw_tokens(cp, org_and_owner):
    _org, owner = org_and_owner
    dev = cp.create_principal(
        actor=owner, organization_id=owner.organization_id, type="human", display_name="Dev"
    )
    credential, raw_token = cp.issue_credential(actor=owner, principal_id=dev.principal_id, display_name="Claude Code")
    listed = cp.list_credentials(actor=owner, principal_id=dev.principal_id)
    assert [c.credential_id for c in listed] == [credential.credential_id]
    # The store's own model always carries only a hash — confirm it never
    # equals (or contains) the raw secret, so nothing downstream that
    # forgets to filter it could leak the token by accident.
    assert listed[0].token_hash != raw_token
    assert raw_token not in listed[0].token_hash


def test_list_credentials_denies_cross_org(cp):
    _org_a, owner_a = cp.onboard_organization(organization_name="Org A", owner_display_name="A")
    org_b, owner_b = cp.onboard_organization(organization_name="Org B", owner_display_name="B")
    dev_b = cp.create_principal(
        actor=owner_b, organization_id=org_b.organization_id, type="human", display_name="Dev B"
    )
    with pytest.raises(AccessDeniedError):
        cp.list_credentials(actor=owner_a, principal_id=dev_b.principal_id)


# -- migration parity ------------------------------------------------------


def test_alembic_upgrade_matches_metadata(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from mak4i.identity.schema import metadata

    db = tmp_path / "migrated.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{db}")
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    inspector = inspect(create_engine(f"sqlite:///{db}"))
    assert set(inspector.get_table_names()) == set(metadata.tables) | {"alembic_version"}
    for name, table in metadata.tables.items():
        migrated = {c["name"]: c for c in inspector.get_columns(name)}
        assert set(migrated) == set(table.columns.keys()), name
        for column in table.columns:
            assert migrated[column.name]["nullable"] == column.nullable, (name, column.name)
        migrated_indexes = {i["name"] for i in inspector.get_indexes(name)}
        assert {i.name for i in table.indexes} <= migrated_indexes, name


def test_alembic_downgrade_of_oauth_keeps_identity_data(tmp_path, monkeypatch):
    """Migration 0002 is additive and reversible: downgrading removes only
    the OAuth tables and leaves organizations/principals/credentials."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "migrated.db"
    monkeypatch.setenv("MAK4I_CONTROL_PLANE_DB", f"sqlite:///{db}")
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0001_initial")
    from mak4i.identity.sql_store import SqlControlPlaneStore

    cp = ControlPlane(SqlControlPlaneStore(f"sqlite:///{db}"))
    _org, owner = cp.onboard_organization(organization_name="Keep", owner_display_name="Me")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001_initial")
    tables = set(inspect(create_engine(f"sqlite:///{db}")).get_table_names())
    assert not any(t.startswith("oauth_") for t in tables)
    assert cp.get_principal(owner.principal_id) is not None
