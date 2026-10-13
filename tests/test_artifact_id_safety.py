"""An artifact_id is a storage name: it must never address anything outside
its own (organization, project) scope, whatever the store backend."""

from __future__ import annotations

import anyio
import pytest
from mcp.client import Client

from mak4i.api import MAK4IEngine
from mak4i.audit import AuditLogger
from mak4i.errors import ValidationFailedError
from mak4i.identity import Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.mcp_server import build_server, current_principal
from mak4i.store.base import ArtifactNotFoundError, InvalidArtifactIdError, is_safe_artifact_id
from mak4i.store.local_json import LocalJSONStore

TYPES = ["caching_decision", "database_decision"]
UNSAFE_IDS = [
    "../escaped",
    "../../../escaped",
    "..",
    ".",
    "nested/record",
    "nested\\record",
    "line\nbreak",
    "nul\x00byte",
    "",
]
SAFE_IDS = ["decision-001", "Cache Decision", "unicode-ü", "with.dots", "..leading-dots", "v2..final"]


@pytest.fixture
def world(tmp_path):
    cp = ControlPlane(InMemoryControlPlaneStore())
    audit = AuditLogger()
    store_root = tmp_path / "store" / "artifacts"
    store = LocalJSONStore(store_root)
    engine = MAK4IEngine(store, audit=audit, authorizer=Authorizer(cp, audit), control_plane=cp)
    org, owner = cp.onboard_organization(organization_name="Acme", owner_display_name="Owner")
    other_org, other_owner = cp.onboard_organization(organization_name="Other", owner_display_name="Other owner")
    project = cp.create_project(actor=owner, organization_id=org.organization_id, name="P")
    other_project = cp.create_project(actor=other_owner, organization_id=other_org.organization_id, name="Q")

    def principal(o, ow, p):
        x = cp.create_principal(actor=ow, organization_id=o.organization_id, type="human", display_name="W")
        cp.grant(actor=ow, principal_id=x.principal_id, project_id=p.project_id, permissions=["read", "write"])
        return x

    return {
        "engine": engine,
        "store": store,
        "tmp": tmp_path,
        "root": store_root,
        "project": project.project_id,
        "org": org.organization_id,
        "writer": principal(org, owner, project),
        "other_writer": principal(other_org, other_owner, other_project),
        "other_project": other_project.project_id,
        "other_org": other_org.organization_id,
    }


def _create(w, artifact_id, artifact_type, who="writer", project=None):
    return w["engine"].create_artifact(
        principal=w[who], project=project or w["project"], artifact_id=artifact_id,
        artifact_type=artifact_type, title="t", content="c", tags=[],
    )


def _files(path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file())


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("artifact_id", UNSAFE_IDS)
def test_create_rejects_ids_that_could_leave_the_scope(world, artifact_id, artifact_type):
    before = _files(world["tmp"])
    with pytest.raises(ValidationFailedError):
        _create(world, artifact_id, artifact_type)
    assert _files(world["tmp"]) == before  # nothing written anywhere, inside or outside the store


@pytest.mark.parametrize("artifact_type", TYPES)
def test_create_cannot_plant_a_record_in_another_tenants_project(world, artifact_type):
    _create(world, "victim", artifact_type, who="other_writer", project=world["other_project"])
    target = f"../../{world['other_org']}/{world['other_project']}/planted"
    with pytest.raises(ValidationFailedError):
        _create(world, target, artifact_type)
    assert not (world["root"] / world["other_org"] / world["other_project"] / "planted.json").exists()
    assert [a.artifact_id for a in world["engine"].search(
        principal=world["other_writer"], project=world["other_project"])] == ["victim"]


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("artifact_id", SAFE_IDS)
def test_ordinary_ids_keep_working(world, artifact_id, artifact_type):
    created = _create(world, artifact_id, artifact_type)
    nxt = world["engine"].supersede_artifact(
        principal=world["writer"], project=world["project"], old_id=created.artifact_id,
        content="c2", reason="update",
    )
    assert nxt.lineage_id == artifact_id
    history = world["engine"].get_history(principal=world["writer"], project=world["project"], lineage_id=artifact_id)
    assert [v.version for v in history] == ["1.0", "2.0"]
    assert all(p.startswith("store/artifacts/") for p in _files(world["tmp"]))


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("artifact_id", ["../x", "a/b", ".."])
def test_reads_and_supersedes_with_unsafe_ids_find_nothing(world, artifact_id, artifact_type):
    _create(world, "x", artifact_type)
    engine = world["engine"]
    assert engine.get_history(principal=world["writer"], project=world["project"], lineage_id=artifact_id) == []
    with pytest.raises(ArtifactNotFoundError):
        engine.supersede_artifact(principal=world["writer"], project=world["project"],
                                  old_id=artifact_id, content="c", reason="r")


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("artifact_id", ["../escaped", "nested/record", "..", "a\x00b"])
def test_local_store_refuses_unsafe_names_directly(world, artifact_id, artifact_type):
    # Backstop below the engine: even a caller that skips engine validation
    # can't make the store read or write outside the scope directory.
    store = world["store"]
    safe = _create(world, "template", artifact_type)
    before = _files(world["tmp"])
    with pytest.raises(InvalidArtifactIdError):
        store.put_new(safe.model_copy(update={"artifact_id": artifact_id, "lineage_id": artifact_id}))
    assert store.get(world["org"], world["project"], artifact_id) is None
    assert _files(world["tmp"]) == before


def test_safety_predicate():
    assert all(is_safe_artifact_id(i) for i in SAFE_IDS)
    assert not any(is_safe_artifact_id(i) for i in UNSAFE_IDS)


@pytest.mark.parametrize("artifact_type", TYPES)
def test_mcp_create_reports_validation_error(world, artifact_type):
    server = build_server(world["engine"])
    token = current_principal.set(world["writer"])
    try:
        async def _call():
            async with Client(server=server) as client:
                return await client.call_tool("mak4i_create", {
                    "project": world["project"], "artifact_id": "../../../escaped",
                    "artifact_type": artifact_type, "title": "t", "content": "c",
                })
        result = anyio.run(_call)
    finally:
        current_principal.reset(token)
    assert result.is_error
    assert result.structured_content["error"]["code"] == "validation_error"
    assert not (world["tmp"] / "escaped.json").exists()
    assert not any(p.name == "escaped.json" for p in world["tmp"].rglob("*"))


@pytest.mark.parametrize("artifact_type", TYPES)
def test_direct_store_calls_stay_inside_their_tenant(world, artifact_type):
    # A record is addressed by (organization, project, id); the same safe id
    # in another organization or project is a different, unreachable record.
    store = world["store"]
    mine = _create(world, "shared-name", artifact_type)
    theirs = _create(world, "shared-name", artifact_type, who="other_writer", project=world["other_project"])
    assert store.get(world["org"], world["project"], "shared-name")[0].content == mine.content
    assert store.get(world["org"], world["other_project"], "shared-name") is None
    assert store.get(world["other_org"], world["project"], "shared-name") is None
    assert store.get(world["other_org"], world["other_project"], "shared-name")[0].organization_id == theirs.organization_id
    # Scope segments are storage names too: a traversal into the other
    # tenant's directory (which stays inside the store root) is refused.
    escapes = [
        (world["org"], f"../../{world['other_org']}/{world['other_project']}"),
        (f"{world['org']}/../{world['other_org']}", world["other_project"]),
        ("..", world["other_project"]),
    ]
    for org, project in escapes:
        with pytest.raises(InvalidArtifactIdError):
            store.get(org, project, "shared-name")
        with pytest.raises(InvalidArtifactIdError):
            store.query(org, project)


@pytest.mark.parametrize("artifact_type", TYPES)
def test_unsafe_id_is_a_refused_write_event(world, artifact_type, caplog):
    import json
    import logging

    from mak4i.audit import LOGGER_NAME

    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    with pytest.raises(ValidationFailedError):
        _create(world, "../escaped", artifact_type)
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME]
    (refused,) = [e for e in events if e["event"] == "WRITE_REFUSED"]
    assert refused["error_code"] == "validation_error" and refused["principal_id"] == world["writer"].principal_id
    assert "../escaped" not in json.dumps(refused)  # the requested id is not echoed into the security log
