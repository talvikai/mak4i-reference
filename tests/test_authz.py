import json
import logging

import pytest

from mak4i.audit import LOGGER_NAME, AuditLogger
from mak4i.identity import AccessDeniedError, Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore


@pytest.fixture
def audit(caplog) -> AuditLogger:
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    return AuditLogger()


def _records(caplog) -> list[dict]:
    return [json.loads(r.message) for r in caplog.records if r.name == LOGGER_NAME]


@pytest.fixture
def world(audit):
    """Two orgs; dev_a has read+write on project_a, nothing on project_b."""
    cp = ControlPlane(InMemoryControlPlaneStore())
    _org_a, owner_a = cp.onboard_organization(
        organization_name="WD Technology Solutions", owner_display_name="Owner A"
    )
    _org_b, owner_b = cp.onboard_organization(
        organization_name="Talvik", owner_display_name="Owner B"
    )
    project_a = cp.create_project(
        actor=owner_a, organization_id=owner_a.organization_id, name="Schedovia"
    )
    project_b = cp.create_project(
        actor=owner_b, organization_id=owner_b.organization_id, name="Talvik Website"
    )
    dev_a = cp.create_principal(
        actor=owner_a,
        organization_id=owner_a.organization_id,
        type="human",
        display_name="Dev A",
    )
    cp.grant(
        actor=owner_a,
        principal_id=dev_a.principal_id,
        project_id=project_a.project_id,
        permissions=["read", "write"],
    )
    return {
        "authorizer": Authorizer(cp, audit),
        "dev_a": dev_a,
        "project_a": project_a.project_id,
        "project_b": project_b.project_id,
        "org_a": owner_a.organization_id,
    }


def test_require_returns_outcome_for_a_granted_permission(world, caplog):
    outcome = world["authorizer"].require(
        world["dev_a"], world["project_a"], "write", correlation_id="c"
    )
    assert outcome.organization_id == world["org_a"]
    assert outcome.project_id == world["project_a"]
    assert _records(caplog)[-1]["event"] == "ACCESS_GRANTED"


def test_require_denies_a_permission_not_in_the_grant(world, caplog):
    cp = world["authorizer"]._control_plane
    _org, owner = cp.onboard_organization(
        organization_name="ReadOnly Org", owner_display_name="O"
    )
    project = cp.create_project(
        actor=owner, organization_id=owner.organization_id, name="P"
    )
    reader = cp.create_principal(
        actor=owner, organization_id=owner.organization_id, type="human", display_name="R"
    )
    cp.grant(
        actor=owner,
        principal_id=reader.principal_id,
        project_id=project.project_id,
        permissions=["read"],
    )
    with pytest.raises(AccessDeniedError):
        world["authorizer"].require(
            reader, project.project_id, "write", correlation_id="c"
        )
    assert _records(caplog)[-1]["event"] == "ACCESS_DENIED"


def test_require_denies_cross_org_access_without_leaking_org(world, caplog):
    with pytest.raises(AccessDeniedError):
        world["authorizer"].require(
            world["dev_a"], world["project_b"], "read", correlation_id="c"
        )
    denied = _records(caplog)[-1]
    assert denied["event"] == "ACCESS_DENIED"
    # the denier's own org context is absent because the project did not
    # resolve within their org — nothing about Talvik leaks into the trail
    assert "organization_id" not in denied
    assert denied["project_id"] == world["project_b"]


def test_require_denies_unknown_project_identically(world):
    with pytest.raises(AccessDeniedError):
        world["authorizer"].require(
            world["dev_a"], "prj_does-not-exist", "read", correlation_id="c"
        )


def test_operator_impersonation_is_recorded_in_context(world, caplog):
    world["authorizer"].require(
        world["dev_a"],
        world["project_a"],
        "read",
        correlation_id="c",
        auth_method="operator_impersonation",
    )
    assert _records(caplog)[-1]["auth_method"] == "operator_impersonation"
