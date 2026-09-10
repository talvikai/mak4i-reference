"""The 15 required Developer Preview security tests (see the approved
plan's "Required security tests" section). Two organizations, each with
its own principal holding grants only within its own org, exercised
through the same engine/CLI/MCP code paths — no scenario-specific branching
anywhere these tests touch (CLAUDE.md).

Fixture shape: WD Technology Solutions (Schedovia, SheetCraft, WD Tech
Website) and Talvik (MAK4I, Talvik Website) — the same sample names the
seed script and docs/DEMO.md use. Principal A holds read+write on every WD
Tech project and nothing on Talvik; Principal B is the mirror image.
"""

import json
import logging

import pytest
from mcp.client import Client

from mak4i.api import MAK4IEngine
from mak4i.audit import LOGGER_NAME, AuditLogger
from mak4i.identity import AccessDeniedError, Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.mcp_server import build_server, current_principal
from mak4i.store.base import ArtifactNotFoundError
from mak4i.store.local_json import LocalJSONStore


def _records(caplog) -> list[dict]:
    return [json.loads(r.message) for r in caplog.records if r.name == LOGGER_NAME]


@pytest.fixture
def world(tmp_path):
    control_plane = ControlPlane(InMemoryControlPlaneStore())
    audit = AuditLogger()
    authorizer = Authorizer(control_plane, audit)
    store = LocalJSONStore(tmp_path / "artifacts")
    engine = MAK4IEngine(store, audit=audit, authorizer=authorizer, control_plane=control_plane)

    wd_org, wd_owner = control_plane.onboard_organization(
        organization_name="WD Technology Solutions", owner_display_name="WD Owner"
    )
    talvik_org, talvik_owner = control_plane.onboard_organization(
        organization_name="Talvik", owner_display_name="Talvik Owner"
    )

    schedovia = control_plane.create_project(
        actor=wd_owner, organization_id=wd_org.organization_id, name="Schedovia"
    )
    sheetcraft = control_plane.create_project(
        actor=wd_owner, organization_id=wd_org.organization_id, name="SheetCraft"
    )
    wd_website = control_plane.create_project(
        actor=wd_owner, organization_id=wd_org.organization_id, name="WD Tech Website"
    )
    mak4i_project = control_plane.create_project(
        actor=talvik_owner, organization_id=talvik_org.organization_id, name="MAK4I"
    )
    talvik_website = control_plane.create_project(
        actor=talvik_owner, organization_id=talvik_org.organization_id, name="Talvik Website"
    )

    principal_a = control_plane.create_principal(
        actor=wd_owner,
        organization_id=wd_org.organization_id,
        type="human",
        display_name="Principal A",
    )
    principal_b = control_plane.create_principal(
        actor=talvik_owner,
        organization_id=talvik_org.organization_id,
        type="human",
        display_name="Principal B",
    )

    for project in (schedovia, sheetcraft, wd_website):
        control_plane.grant(
            actor=wd_owner,
            principal_id=principal_a.principal_id,
            project_id=project.project_id,
            permissions=["read", "write"],
        )
    for project in (mak4i_project, talvik_website):
        control_plane.grant(
            actor=talvik_owner,
            principal_id=principal_b.principal_id,
            project_id=project.project_id,
            permissions=["read", "write"],
        )

    return {
        "engine": engine,
        "control_plane": control_plane,
        "audit": audit,
        "store": store,
        "wd_org": wd_org.organization_id,
        "talvik_org": talvik_org.organization_id,
        "schedovia": schedovia.project_id,
        "sheetcraft": sheetcraft.project_id,
        "wd_website": wd_website.project_id,
        "mak4i_project": mak4i_project.project_id,
        "talvik_website": talvik_website.project_id,
        "principal_a": principal_a,
        "principal_b": principal_b,
    }


def _create(world, principal, project, artifact_id, artifact_type="architecture_decision", **overrides):
    kwargs = dict(
        principal=principal,
        project=project,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        title="Title",
        content="Content",
    )
    kwargs.update(overrides)
    return world["engine"].create_artifact(**kwargs)


# 1. Principal A accesses an authorized WD Tech project — success.
def test_1_principal_a_reads_and_writes_an_authorized_wd_tech_project(world):
    engine, a = world["engine"], world["principal_a"]
    artifact = _create(world, a, world["schedovia"], "decision-cache-001")
    assert artifact.status == "active"

    package = engine.get_current(principal=a, project=world["schedovia"])
    assert [x.artifact_id for x in package.artifacts] == ["decision-cache-001"]


# 2. Principal A reading a Talvik project — AccessDeniedError.
def test_2_principal_a_reading_a_talvik_project_is_denied(world):
    engine, a = world["engine"], world["principal_a"]
    with pytest.raises(AccessDeniedError):
        engine.get_current(principal=a, project=world["mak4i_project"])


# 3. Principal A writing a Talvik project — AccessDeniedError.
def test_3_principal_a_writing_a_talvik_project_is_denied(world):
    a = world["principal_a"]
    with pytest.raises(AccessDeniedError):
        _create(world, a, world["talvik_website"], "doc-should-not-exist", artifact_type="documentation_fact")


# 4. Unauthorized access raises an explicit denial (never an empty list/empty-success).
def test_4_unauthorized_access_raises_rather_than_returning_empty_success(world):
    engine, a = world["engine"], world["principal_a"]
    # If this silently returned [] / an empty ContextPackage instead of
    # raising, that would be indistinguishable from "authorized, no data"
    # — exactly the empty-success failure mode this test rules out.
    with pytest.raises(AccessDeniedError):
        engine.search(principal=a, project=world["mak4i_project"])
    with pytest.raises(AccessDeniedError):
        engine.get_history(principal=a, project=world["mak4i_project"], lineage_id="anything")


# 5. Unauthorized access emits an ACCESS_DENIED audit event with principal + project.
def test_5_unauthorized_access_emits_access_denied_with_principal_and_project(world, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    engine, a = world["engine"], world["principal_a"]

    with pytest.raises(AccessDeniedError):
        engine.get_current(principal=a, project=world["mak4i_project"])

    denied = [r for r in _records(caplog) if r["event"] == "ACCESS_DENIED"]
    assert len(denied) == 1
    assert denied[0]["principal_id"] == a.principal_id
    assert denied[0]["project_id"] == world["mak4i_project"]
    assert denied[0]["permission"] == "read"


# 6. list_projects for Principal A returns only WD Tech projects (no Talvik rows).
def test_6_list_projects_for_principal_a_returns_only_wd_tech_projects(world):
    engine, a = world["engine"], world["principal_a"]
    authorized = engine.list_projects(principal=a)
    project_ids = {ap.project.project_id for ap in authorized}
    assert project_ids == {world["schedovia"], world["sheetcraft"], world["wd_website"]}
    assert world["mak4i_project"] not in project_ids
    assert world["talvik_website"] not in project_ids


# 7. search_authorized (no project arg) for Principal A returns only WD Tech artifacts.
def test_7_search_authorized_with_no_project_scopes_to_wd_tech_only(world):
    engine, a, b = world["engine"], world["principal_a"], world["principal_b"]
    _create(world, a, world["schedovia"], "decision-cache-001")
    _create(world, a, world["sheetcraft"], "doc-sheetcraft-001", artifact_type="documentation_fact")
    _create(world, b, world["mak4i_project"], "decision-talvik-001")

    results = engine.search_authorized(principal=a)
    ids = {artifact.artifact_id for artifact in results}
    assert ids == {"decision-cache-001", "doc-sheetcraft-001"}
    assert "decision-talvik-001" not in ids


# 8. Denial and cross-project search leak no unauthorized project metadata.
def test_8_denial_and_search_leak_no_unauthorized_project_metadata(world):
    engine, a, b = world["engine"], world["principal_a"], world["principal_b"]
    _create(world, b, world["mak4i_project"], "decision-talvik-001")

    with pytest.raises(AccessDeniedError) as excinfo:
        engine.get_current(principal=a, project=world["mak4i_project"])
    message = str(excinfo.value)
    assert "Talvik" not in message
    assert world["mak4i_project"] not in message
    assert "MAK4I" not in message

    # A denial for a real project and a denial for a nonexistent project
    # id must look identical to the caller — no existence oracle.
    with pytest.raises(AccessDeniedError) as excinfo_fake:
        engine.get_current(principal=a, project="prj_does-not-exist")
    assert str(excinfo_fake.value) == message

    results = engine.search_authorized(principal=a)
    assert all(artifact.project != world["mak4i_project"] for artifact in results)


# 9. created_by / audit actor equal the authenticated principal, not any caller value.
def test_9_created_by_and_actor_equal_the_authenticated_principal(world, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    a = world["principal_a"]
    artifact = _create(world, a, world["schedovia"], "decision-cache-001")

    assert artifact.created_by == a.principal_id
    create_events = [r for r in _records(caplog) if r["event"] == "CREATE"]
    assert create_events[-1]["actor"] == a.principal_id
    assert create_events[-1]["principal_id"] == a.principal_id


# 10. A tool call passing a spoofed created_by/actor cannot override authenticated identity.
def test_10_spoofed_created_by_cannot_override_authenticated_identity(world):
    """`created_by`/`actor` do not exist as parameters anywhere in the
    engine or MCP tool signatures — there is no field to spoof through.
    Attempting to smuggle one in as an engine kwarg is rejected outright;
    attempting it through the MCP tool payload is either rejected by the
    tool's schema or silently ignored, and the resulting artifact's
    `created_by` is always the ContextVar-resolved principal either way."""
    engine, a = world["engine"], world["principal_a"]

    with pytest.raises(TypeError):
        engine.create_artifact(
            principal=a,
            project=world["schedovia"],
            artifact_id="decision-cache-001",
            artifact_type="architecture_decision",
            title="Title",
            content="Content",
            created_by="attacker@example.com",  # not a real parameter
        )

    server = build_server(engine)
    token = current_principal.set(a)
    try:
        import anyio

        async def _call():
            async with Client(server=server) as client:
                return await client.call_tool(
                    "mak4i_create",
                    {
                        "project": world["schedovia"],
                        "artifact_id": "decision-cache-002",
                        "artifact_type": "architecture_decision",
                        "title": "Title",
                        "content": "Content",
                        "created_by": "attacker@example.com",
                    },
                )

        result = anyio.run(_call)
    finally:
        current_principal.reset(token)

    if not result.is_error:
        assert result.structured_content["created_by"] == a.principal_id
        assert result.structured_content["created_by"] != "attacker@example.com"


# 11. A revoked credential is rejected at authenticate().
def test_11_revoked_credential_is_rejected_at_authenticate(world):
    from mak4i.identity import CredentialInvalidError

    control_plane = world["control_plane"]
    owner = _owner_of(world, world["principal_a"])
    credential, raw_token = control_plane.issue_credential(actor=owner, principal_id=owner.principal_id)

    assert control_plane.authenticate(raw_token).principal_id == owner.principal_id

    control_plane.revoke_credential(actor=owner, credential_id=credential.credential_id)
    with pytest.raises(CredentialInvalidError):
        control_plane.authenticate(raw_token)


# 12. Two credentials for one principal operate independently; revoking one leaves the other valid.
def test_12_two_credentials_for_one_principal_are_independent(world):
    control_plane = world["control_plane"]
    a = world["principal_a"]

    cred_1, token_1 = control_plane.issue_credential(
        actor=_owner_of(world, a), principal_id=a.principal_id, display_name="laptop"
    )
    cred_2, token_2 = control_plane.issue_credential(
        actor=_owner_of(world, a), principal_id=a.principal_id, display_name="ci-bot"
    )

    control_plane.revoke_credential(actor=_owner_of(world, a), credential_id=cred_1.credential_id)

    from mak4i.identity import CredentialInvalidError

    with pytest.raises(CredentialInvalidError):
        control_plane.authenticate(token_1)
    assert control_plane.authenticate(token_2).principal_id == a.principal_id


def _owner_of(world, principal):
    control_plane = world["control_plane"]
    return next(
        p
        for p in control_plane._store.list_principals(principal.organization_id)
        if p.role == "owner"
    )


# 13. An artifact in project A cannot supersede an artifact in project B.
def test_13_cross_project_supersede_is_blocked(world):
    engine, a = world["engine"], world["principal_a"]
    _create(world, a, world["schedovia"], "decision-cache-001")

    # Attempting to supersede it "through" a different (also-authorized)
    # project must behave exactly like the artifact not existing there —
    # never find it via another project's scope.
    with pytest.raises(ArtifactNotFoundError):
        engine.supersede_artifact(
            principal=a,
            project=world["sheetcraft"],
            old_id="decision-cache-001",
            content="Use Valkey.",
            reason="Attempted cross-project supersede.",
        )

    # The original, untouched, is still active in its own project.
    package = engine.get_current(principal=a, project=world["schedovia"])
    assert [x.artifact_id for x in package.artifacts] == ["decision-cache-001"]


# 14. Both orgs hold a project with the same name; artifacts never collide.
def test_14_same_project_name_across_orgs_never_collides(world):
    control_plane, engine = world["control_plane"], world["engine"]
    a, b = world["principal_a"], world["principal_b"]

    wd_shared = control_plane.create_project(
        actor=_owner_of(world, a), organization_id=world["wd_org"], name="Shared Name"
    )
    talvik_shared = control_plane.create_project(
        actor=_owner_of(world, b), organization_id=world["talvik_org"], name="Shared Name"
    )
    assert wd_shared.project_id != talvik_shared.project_id

    control_plane.grant(
        actor=_owner_of(world, a),
        principal_id=a.principal_id,
        project_id=wd_shared.project_id,
        permissions=["read", "write"],
    )
    control_plane.grant(
        actor=_owner_of(world, b),
        principal_id=b.principal_id,
        project_id=talvik_shared.project_id,
        permissions=["read", "write"],
    )

    _create(world, a, wd_shared.project_id, "decision-shared-001")
    _create(world, b, talvik_shared.project_id, "decision-shared-001")

    wd_result = engine.get_current(principal=a, project=wd_shared.project_id)
    talvik_result = engine.get_current(principal=b, project=talvik_shared.project_id)
    assert wd_result.artifacts[0].organization_id == world["wd_org"]
    assert talvik_result.artifacts[0].organization_id == world["talvik_org"]
    assert wd_result.artifacts[0] != talvik_result.artifacts[0]


# 15. Coverage spans >= 2 artifact types across >= 2 organizations.
def test_15_fixture_coverage_spans_two_artifact_types_and_two_orgs(world):
    a, b = world["principal_a"], world["principal_b"]
    decision = _create(world, a, world["schedovia"], "decision-cache-001", artifact_type="architecture_decision")
    fact = _create(world, b, world["mak4i_project"], "doc-mak4i-001", artifact_type="documentation_fact")

    artifact_types = {decision.artifact_type, fact.artifact_type}
    organizations = {decision.organization_id, fact.organization_id}
    assert artifact_types == {"architecture_decision", "documentation_fact"}
    assert organizations == {world["wd_org"], world["talvik_org"]}
