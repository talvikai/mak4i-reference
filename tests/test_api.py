import pytest

from mak4i.api import ArtifactNotActiveError, MAK4IEngine
from mak4i.identity import AccessDeniedError, Authorizer, ControlPlane
from mak4i.identity.memory_store import InMemoryControlPlaneStore
from mak4i.store.base import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ConcurrentModificationError,
)
from mak4i.store.local_json import LocalJSONStore


def _onboarded_world():
    """One org, one project ("schedovia"), one principal with read+write on
    it — the minimal authorized world every engine test starts from."""
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
    return control_plane, org.organization_id, project.project_id, principal


@pytest.fixture
def store(tmp_path) -> LocalJSONStore:
    return LocalJSONStore(tmp_path / "artifacts")


@pytest.fixture
def world(store):
    control_plane, organization_id, project_id, principal = _onboarded_world()
    engine = MAK4IEngine(
        store, authorizer=Authorizer(control_plane), control_plane=control_plane
    )
    return {
        "engine": engine,
        "control_plane": control_plane,
        "organization_id": organization_id,
        "project": project_id,
        "principal": principal,
    }


@pytest.fixture
def engine(world) -> MAK4IEngine:
    return world["engine"]


def test_create_artifact_stores_and_returns_it(world, store):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    artifact = engine.create_artifact(
        principal=principal,
        project=project,
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
        rationale="Fast shared caching, established client support.",
        tags=["caching", "redis", "architecture"],
    )

    assert artifact.status == "active"
    assert artifact.version == "1.0"
    assert artifact.lineage_id == "decision-cache-001"
    assert artifact.supersedes is None
    assert artifact.superseded_by is None
    assert artifact.organization_id == world["organization_id"]
    assert artifact.created_by == principal.principal_id

    fetched, _token = store.get(world["organization_id"], project, "decision-cache-001")
    assert fetched == artifact


def test_a_valid_credential_confers_no_more_access_than_its_principals_own_grants(tmp_path):
    """Enterprise self-hosted requirement (§12): "credential inherits
    principal authorization only." A `Credential` carries no permissions
    of its own — it only authenticates an identity; `Authorizer` decides
    access purely from that principal's `Grant`s. Issue a perfectly valid
    credential to a principal with no grant on the project at all, and
    prove the credential authenticates (identity is real) while the
    engine still denies every operation on it — through the exact
    `Authorizer` every MCP tool call goes through, not a mock.

    Self-contained rather than built on the `world` fixture: proving this
    needs the organization's *owner* (to create a second, ungranted
    principal and issue it a credential), which `_onboarded_world()`
    creates but doesn't return.
    """
    control_plane = ControlPlane(InMemoryControlPlaneStore())
    org, owner = control_plane.onboard_organization(
        organization_name="WD Technology Solutions", owner_display_name="Owner"
    )
    project = control_plane.create_project(
        actor=owner, organization_id=org.organization_id, name="schedovia"
    )
    no_grant_principal = control_plane.create_principal(
        actor=owner, organization_id=org.organization_id, type="human", display_name="No-grant dev"
    )
    _credential, raw_token = control_plane.issue_credential(
        actor=owner, principal_id=no_grant_principal.principal_id
    )

    # The credential is real: it authenticates to a principal.
    authenticated = control_plane.authenticate(raw_token)
    assert authenticated.principal_id == no_grant_principal.principal_id

    # But that principal has no grant on this project, so every engine
    # operation — the same calls every MCP tool makes — is denied.
    engine = MAK4IEngine(
        LocalJSONStore(tmp_path / "artifacts"),
        authorizer=Authorizer(control_plane),
        control_plane=control_plane,
    )
    with pytest.raises(AccessDeniedError):
        engine.create_artifact(
            principal=no_grant_principal,
            project=project.project_id,
            artifact_id="should-not-be-created",
            artifact_type="note",
            title="x",
            content="x",
        )
    with pytest.raises(AccessDeniedError):
        engine.get_current(principal=no_grant_principal, project=project.project_id)


def test_create_artifact_rejects_duplicate_id(world):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    kwargs = dict(
        principal=principal,
        project=project,
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
    )
    engine.create_artifact(**kwargs)
    with pytest.raises(ArtifactAlreadyExistsError):
        engine.create_artifact(**kwargs)


def test_create_artifact_defaults_tags_to_empty_list(world):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    artifact = engine.create_artifact(
        principal=principal,
        project=project,
        artifact_id="doc-api-conventions-001",
        artifact_type="documentation_fact",
        title="API pagination convention",
        content="All list endpoints use cursor-based pagination.",
    )
    assert artifact.tags == []
    assert artifact.rationale is None


def test_supersede_artifact_marks_old_superseded_and_new_active(world, store):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(
        principal=principal,
        project=project,
        artifact_id="decision-db-001",
        artifact_type="architecture_decision",
        title="Primary database technology",
        content="Use PostgreSQL for the primary application database.",
        rationale="Strong relational support.",
        tags=["database", "postgresql", "architecture"],
    )

    new = engine.supersede_artifact(
        principal=principal,
        project=project,
        old_id="decision-db-001",
        content="Use MySQL for the primary application database.",
        reason="Team familiarity and managed-hosting availability.",
    )

    assert new.artifact_id == "decision-db-002"
    assert new.status == "active"
    assert new.version == "2.0"
    assert new.lineage_id == "decision-db-001"
    assert new.supersedes == "decision-db-001"
    assert new.rationale == "Team familiarity and managed-hosting availability."
    # title/tags inherited when not explicitly overridden
    assert new.title == "Primary database technology"
    assert new.tags == ["database", "postgresql", "architecture"]

    old, _token = store.get(world["organization_id"], project, "decision-db-001")
    assert old.status == "superseded"
    assert old.superseded_by == "decision-db-002"


def test_supersede_artifact_allows_overriding_title_and_tags(world):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(
        principal=principal,
        project=project,
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
        tags=["caching", "redis", "architecture"],
    )

    new = engine.supersede_artifact(
        principal=principal,
        project=project,
        old_id="decision-cache-001",
        content="Use Valkey for application caching.",
        reason="License concerns with Redis.",
        title="Application caching technology (Valkey)",
        tags=["caching", "valkey", "architecture"],
    )

    assert new.title == "Application caching technology (Valkey)"
    assert new.tags == ["caching", "valkey", "architecture"]


def test_supersede_nonexistent_artifact_raises_not_found(world):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    with pytest.raises(ArtifactNotFoundError):
        engine.supersede_artifact(
            principal=principal,
            project=project,
            old_id="does-not-exist",
            content="x",
            reason="x",
        )


def test_supersede_already_superseded_artifact_is_rejected_and_writes_nothing(world, store):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(
        principal=principal,
        project=project,
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
    )
    engine.supersede_artifact(
        principal=principal,
        project=project,
        old_id="decision-cache-001",
        content="Use Valkey for application caching.",
        reason="License concerns with Redis.",
    )

    with pytest.raises(ArtifactNotActiveError):
        engine.supersede_artifact(
            principal=principal,
            project=project,
            old_id="decision-cache-001",
            content="Use Memcached for application caching.",
            reason="Should never happen — already superseded.",
        )

    # Nothing further was written: the lineage still has exactly one active
    # member, and no decision-cache-003 was created.
    organization_id = world["organization_id"]
    assert store.get(organization_id, project, "decision-cache-003") is None
    active, _ = store.get(organization_id, project, "decision-cache-002")
    assert active.status == "active"


class _StaleTokenStore:
    """Wraps a real store but always hands back a stale version token from
    get(), so put_if_match's precondition check is guaranteed to fail —
    used to test that supersede_artifact writes nothing further when step
    4 (§5) rejects the operation."""

    def __init__(self, inner: LocalJSONStore):
        self._inner = inner

    def get(self, organization_id, project, artifact_id):
        result = self._inner.get(organization_id, project, artifact_id)
        if result is None:
            return None
        artifact, _real_token = result
        return artifact, "stale-token-that-will-never-match"

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_supersede_artifact_rejects_concurrent_modification_and_writes_nothing(tmp_path):
    real_store = LocalJSONStore(tmp_path / "artifacts")
    control_plane, organization_id, project, principal = _onboarded_world()
    authorizer = Authorizer(control_plane)

    engine_setup = MAK4IEngine(real_store, authorizer=authorizer, control_plane=control_plane)
    engine_setup.create_artifact(
        principal=principal,
        project=project,
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        title="Application caching technology",
        content="Use Redis for application caching.",
    )

    racy_engine = MAK4IEngine(
        _StaleTokenStore(real_store), authorizer=authorizer, control_plane=control_plane
    )
    with pytest.raises(ConcurrentModificationError):
        racy_engine.supersede_artifact(
            principal=principal,
            project=project,
            old_id="decision-cache-001",
            content="Use Valkey for application caching.",
            reason="Should be rejected.",
        )

    # Old artifact is untouched, and no successor was created.
    old, _token = real_store.get(organization_id, project, "decision-cache-001")
    assert old.status == "active"
    assert real_store.get(organization_id, project, "decision-cache-002") is None


def test_successor_id_falls_back_to_v_suffix_when_no_trailing_digits(world):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(
        principal=principal,
        project=project,
        artifact_id="doc-api-conventions",
        artifact_type="documentation_fact",
        title="API pagination convention",
        content="All list endpoints use cursor-based pagination.",
    )
    new = engine.supersede_artifact(
        principal=principal,
        project=project,
        old_id="doc-api-conventions",
        content="All list endpoints use offset-based pagination.",
        reason="Cursor pagination proved hard for the mobile client.",
    )
    assert new.artifact_id == "doc-api-conventions-v2"


def test_check_integrity_reports_zero_active_lineage(world, store):
    from datetime import datetime, timezone

    from mak4i.models import Artifact

    engine, principal, project = world["engine"], world["principal"], world["project"]
    organization_id = world["organization_id"]

    now = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)
    store.put_new(
        Artifact(
            artifact_id="decision-cache-001",
            artifact_type="architecture_decision",
            organization_id=organization_id,
            project=project,
            title="Application caching technology",
            content="Use Redis for application caching.",
            rationale=None,
            status="superseded",
            version="1.0",
            created_by=principal.principal_id,
            created_at=now,
            updated_at=now,
            lineage_id="decision-cache-001",
            supersedes=None,
            superseded_by="decision-cache-002",
            tags=["caching"],
        )
    )
    engine.create_artifact(
        principal=principal,
        project=project,
        artifact_id="decision-db-001",
        artifact_type="architecture_decision",
        title="Primary database technology",
        content="Use MySQL.",
    )

    errors = engine.check_integrity(organization_id=organization_id, project=project)

    assert len(errors) == 1
    assert errors[0].kind == "zero_active"
    assert errors[0].lineage_id == "decision-cache-001"


@pytest.mark.parametrize(
    ("artifact_id", "artifact_type", "title", "content"),
    [
        (
            "decision-cache-010",
            "architecture_decision",
            "Application caching technology",
            "Use Redis for application caching.",
        ),
        (
            "decision-db-010",
            "architecture_decision",
            "Primary database technology",
            "Use PostgreSQL for the primary application database.",
        ),
    ],
)
def test_generic_create_and_supersede_path_for_any_scenario(
    world, artifact_id, artifact_type, title, content
):
    """A caching decision and a database decision must exercise the exact
    same engine code path (no technology-specific branching)."""
    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(
        principal=principal,
        project=project,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        title=title,
        content=content,
    )
    new = engine.supersede_artifact(
        principal=principal,
        project=project,
        old_id=artifact_id,
        content=content + " (updated)",
        reason="Generic-path test.",
    )
    assert new.supersedes == artifact_id
    assert new.status == "active"
