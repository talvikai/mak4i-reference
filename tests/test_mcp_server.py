import pytest
from mcp.client import Client

from mak4i.api import MAK4IEngine
from mak4i.identity import Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.mcp_server import _DURABLE_VS_CONVERSATION_GUIDANCE, build_server, current_principal
from mak4i.store.local_json import LocalJSONStore


def _onboarded_world():
    control_plane = ControlPlane(InMemoryControlPlaneStore())
    org, owner = control_plane.onboard_organization(
        organization_name="WD Technology Solutions", owner_display_name="Owner"
    )
    project = control_plane.create_project(
        actor=owner, organization_id=org.organization_id, name="schedovia"
    )
    principal = control_plane.create_principal(
        actor=owner,
        organization_id=org.organization_id,
        type="human",
        display_name="Dev",
    )
    control_plane.grant(
        actor=owner,
        principal_id=principal.principal_id,
        project_id=project.project_id,
        permissions=["read", "write"],
    )
    return control_plane, project.project_id, principal


@pytest.fixture
def world(tmp_path):
    control_plane, project_id, principal = _onboarded_world()
    engine = MAK4IEngine(
        LocalJSONStore(tmp_path / "artifacts"),
        authorizer=Authorizer(control_plane),
        control_plane=control_plane,
    )
    return {"engine": engine, "project": project_id, "principal": principal}


@pytest.fixture
def server(world):
    return build_server(world["engine"])


@pytest.fixture(autouse=True)
def _authenticated(world):
    """Every test in this module acts as `world["principal"]` — set once,
    before the in-process MCP client/server task pair is created, so the
    server-side task's copied context carries it for the whole test (see
    mcp_server.current_principal)."""
    token = current_principal.set(world["principal"])
    yield
    current_principal.reset(token)


async def test_lists_exactly_the_six_required_tools(server):
    async with Client(server=server) as client:
        result = await client.list_tools()
        names = {t.name for t in result.tools}
        assert names == {
            "mak4i_search",
            "mak4i_get_current",
            "mak4i_create",
            "mak4i_supersede",
            "mak4i_history",
            "mak4i_list_projects",
        }


async def test_create_and_supersede_tool_descriptions_carry_durable_guidance(server):
    async with Client(server=server) as client:
        result = await client.list_tools()
        by_name = {t.name: t for t in result.tools}
        assert _DURABLE_VS_CONVERSATION_GUIDANCE in by_name["mak4i_create"].description
        assert _DURABLE_VS_CONVERSATION_GUIDANCE in by_name["mak4i_supersede"].description


async def test_list_projects_returns_the_authorized_project(server, world):
    async with Client(server=server) as client:
        result = await client.call_tool("mak4i_list_projects", {})
        assert not result.is_error
        projects = result.structured_content["result"]
        assert [p["project_id"] for p in projects] == [world["project"]]
        assert set(projects[0]["permissions"]) == {"read", "write"}


async def test_create_then_get_current_round_trips(server, world):
    async with Client(server=server) as client:
        create_result = await client.call_tool(
            "mak4i_create",
            {
                "project": world["project"],
                "artifact_id": "decision-cache-001",
                "artifact_type": "architecture_decision",
                "title": "Application caching technology",
                "content": "Use Redis for application caching.",
                "rationale": "Fast shared caching.",
                "tags": ["caching", "redis", "architecture"],
            },
        )
        assert not create_result.is_error
        assert create_result.structured_content["artifact_id"] == "decision-cache-001"
        assert create_result.structured_content["status"] == "active"
        assert create_result.structured_content["created_by"] == world["principal"].principal_id

        get_result = await client.call_tool(
            "mak4i_get_current", {"project": world["project"], "tags": ["caching"]}
        )
        assert not get_result.is_error
        package = get_result.structured_content
        assert [a["artifact_id"] for a in package["artifacts"]] == ["decision-cache-001"]
        assert package["conflicts"] == []
        assert package["integrity_errors"] == []


async def test_create_duplicate_id_surfaces_as_tool_error(server, world):
    async with Client(server=server) as client:
        payload = {
            "project": world["project"],
            "artifact_id": "decision-cache-001",
            "artifact_type": "architecture_decision",
            "title": "Application caching technology",
            "content": "Use Redis for application caching.",
        }
        await client.call_tool("mak4i_create", payload)
        result = await client.call_tool("mak4i_create", payload)

        assert result.is_error
        assert "already exists" in result.content[0].text


async def test_supersede_then_history_shows_both_versions_oldest_first(server, world):
    async with Client(server=server) as client:
        await client.call_tool(
            "mak4i_create",
            {
                "project": world["project"],
                "artifact_id": "decision-db-001",
                "artifact_type": "architecture_decision",
                "title": "Primary database technology",
                "content": "Use PostgreSQL.",
            },
        )
        supersede_result = await client.call_tool(
            "mak4i_supersede",
            {
                "project": world["project"],
                "old_id": "decision-db-001",
                "content": "Use MySQL.",
                "reason": "Team familiarity and managed-hosting availability.",
            },
        )
        assert not supersede_result.is_error
        assert supersede_result.structured_content["artifact_id"] == "decision-db-002"

        history_result = await client.call_tool(
            "mak4i_history", {"project": world["project"], "lineage_id": "decision-db-001"}
        )
        assert not history_result.is_error
        history = history_result.structured_content["result"]
        assert [a["artifact_id"] for a in history] == ["decision-db-001", "decision-db-002"]
        assert history[0]["status"] == "superseded"
        assert history[1]["status"] == "active"


async def test_supersede_nonexistent_old_id_surfaces_as_tool_error(server, world):
    async with Client(server=server) as client:
        result = await client.call_tool(
            "mak4i_supersede",
            {
                "project": world["project"],
                "old_id": "does-not-exist",
                "content": "x",
                "reason": "x",
            },
        )
        assert result.is_error
        assert "not found" in result.content[0].text


async def test_search_defaults_to_active_status(server, world):
    async with Client(server=server) as client:
        await client.call_tool(
            "mak4i_create",
            {
                "project": world["project"],
                "artifact_id": "decision-cache-001",
                "artifact_type": "architecture_decision",
                "title": "Application caching technology",
                "content": "Use Redis.",
                "tags": ["caching"],
            },
        )
        await client.call_tool(
            "mak4i_supersede",
            {
                "project": world["project"],
                "old_id": "decision-cache-001",
                "content": "Use Valkey.",
                "reason": "License concerns.",
            },
        )

        result = await client.call_tool(
            "mak4i_search", {"project": world["project"], "tags": ["caching"]}
        )
        assert not result.is_error
        ids = [a["artifact_id"] for a in result.structured_content["result"]]
        assert ids == ["decision-cache-002"]


async def test_get_current_surfaces_conflict_without_raising(server, world):
    async with Client(server=server) as client:
        await client.call_tool(
            "mak4i_create",
            {
                "project": world["project"],
                "artifact_id": "decision-cache-001",
                "artifact_type": "architecture_decision",
                "title": "Application caching technology",
                "content": "Use Redis for application caching.",
                "tags": ["caching", "redis", "architecture"],
            },
        )
        await client.call_tool(
            "mak4i_create",
            {
                "project": world["project"],
                "artifact_id": "decision-cache-004",
                "artifact_type": "architecture_decision",
                "title": "Application caching technology",
                "content": "Use Memcached for application caching.",
                "tags": ["caching", "memcached", "architecture"],
            },
        )

        result = await client.call_tool("mak4i_get_current", {"project": world["project"]})
        assert not result.is_error  # a conflict is a normal result, not a tool error
        package = result.structured_content
        assert package["artifacts"] == []
        assert len(package["conflicts"]) == 1


async def test_unauthenticated_call_is_a_tool_error(server):
    """No principal set (autouse fixture reset before entering the client
    context, simulating a call outside any authenticated request)."""
    current_principal.set(None)
    async with Client(server=server) as client:
        result = await client.call_tool(
            "mak4i_get_current", {"project": "prj_does-not-matter"}
        )
        assert result.is_error
        assert "not authenticated" in result.content[0].text
