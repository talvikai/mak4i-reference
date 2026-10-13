"""An artifact_id is a storage name: it must never address anything outside
its own (organization, project) scope."""

from __future__ import annotations

import pytest

from mak4i.api import MAK4IEngine
from mak4i.identity import Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.store.base import InvalidArtifactIdError, is_safe_artifact_id
from mak4i.store.local_json import LocalJSONStore

TYPES = ["caching_decision", "database_decision"]
UNSAFE_IDS = ["../escaped", "../../../escaped", "..", ".", "nested/record", "nested\\record",
              "line\nbreak", "nul\x00byte", ""]
SAFE_IDS = ["decision-001", "Cache Decision", "unicode-ü", "with.dots", "..leading-dots", "v2..final"]


@pytest.fixture
def world(tmp_path):
    cp = ControlPlane(InMemoryControlPlaneStore())
    store = LocalJSONStore(tmp_path / "store" / "artifacts")
    engine = MAK4IEngine(store, authorizer=Authorizer(cp), control_plane=cp)
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    other_org, other_owner = cp.onboard_organization(organization_name="Other", owner_display_name="O2")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P")
    other_project = cp.create_project(actor=other_owner, organization_id=other_org.organization_id, name="Q")
    cp.grant(actor=owner, principal_id=owner.principal_id, project_id=project.project_id, permissions=["read", "write"])
    cp.grant(actor=other_owner, principal_id=other_owner.principal_id, project_id=other_project.project_id,
             permissions=["read", "write"])
    return {"engine": engine, "store": store, "tmp": tmp_path, "owner": owner, "other_owner": other_owner,
            "org": org.organization_id, "project": project.project_id,
            "other_org": other_org.organization_id, "other_project": other_project.project_id}


def _create(w, artifact_id, artifact_type, who="owner", project=None):
    return w["engine"].create_artifact(principal=w[who], project=project or w["project"], artifact_id=artifact_id,
                                       artifact_type=artifact_type, title="t", content="c")


def _files(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("artifact_id", UNSAFE_IDS)
def test_create_rejects_unsafe_ids(world, artifact_id, artifact_type):
    before = _files(world["tmp"])
    with pytest.raises(ValueError):
        _create(world, artifact_id, artifact_type)
    assert _files(world["tmp"]) == before


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("artifact_id", SAFE_IDS)
def test_ordinary_ids_keep_working(world, artifact_id, artifact_type):
    assert _create(world, artifact_id, artifact_type).artifact_id == artifact_id


@pytest.mark.parametrize("artifact_type", TYPES)
def test_store_refuses_unsafe_names_and_scopes_directly(world, artifact_type):
    store = world["store"]
    safe = _create(world, "shared-name", artifact_type)
    _create(world, "shared-name", artifact_type, who="other_owner", project=world["other_project"])
    before = _files(world["tmp"])
    for artifact_id in ("../escaped", "nested/record", "..", "a\x00b"):
        with pytest.raises(InvalidArtifactIdError):
            store.put_new(safe.model_copy(update={"artifact_id": artifact_id, "lineage_id": artifact_id}))
        with pytest.raises(InvalidArtifactIdError):
            store.get(world["org"], world["project"], artifact_id)
    for org, project in ((world["org"], f"../../{world['other_org']}/{world['other_project']}"),
                         (f"{world['org']}/../{world['other_org']}", world["other_project"]),
                         ("..", world["other_project"])):
        with pytest.raises(InvalidArtifactIdError):
            store.get(org, project, "shared-name")
    assert store.get(world["org"], world["other_project"], "shared-name") is None
    assert _files(world["tmp"]) == before


def test_safety_predicate():
    assert all(is_safe_artifact_id(i) for i in SAFE_IDS)
    assert not any(is_safe_artifact_id(i) for i in UNSAFE_IDS)
