import pytest

from mak4i.errors import ArtifactExistsError

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
    with pytest.raises(ArtifactExistsError) as exc:  # MAK-0008 §9 `artifact_exists`
        engine.create_artifact(**kwargs)
    assert exc.value.code == "artifact_exists"


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


def _supersede_n_times(engine, principal, project, artifact_id, n):
    ids = [artifact_id]
    for i in range(n):
        new = engine.supersede_artifact(
            principal=principal, project=project, old_id=ids[-1],
            content=f"revision {i + 2}", reason=f"update {i + 2}",
        )
        ids.append(new.artifact_id)
    return ids


@pytest.mark.parametrize(
    "artifact_id,artifact_type",
    [("doc-api-conventions", "documentation_fact"), ("cache-layer", "caching_decision")],
)
def test_repeated_supersedes_count_the_v_suffix_up(world, store, artifact_id, artifact_type):
    """`-vN` follows the version: doc -> doc-v2 -> doc-v3, never doc-v2-v2."""
    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(
        principal=principal, project=project, artifact_id=artifact_id,
        artifact_type=artifact_type, title="t", content="revision 1",
    )
    ids = _supersede_n_times(engine, principal, project, artifact_id, 5)
    assert ids == [artifact_id] + [f"{artifact_id}-v{n}" for n in range(2, 7)]

    history = engine.get_history(principal=principal, project=project, lineage_id=artifact_id)
    assert [a.artifact_id for a in history] == ids
    assert [a.version for a in history] == [f"{n}.0" for n in range(1, 7)]
    assert all(a.lineage_id == artifact_id for a in history)
    assert [a.superseded_by for a in history] == ids[1:] + [None]


@pytest.mark.parametrize(
    "artifact_id,artifact_type",
    [("decision-db-001", "database_decision"), ("decision-cache-007", "caching_decision")],
)
def test_numbered_ids_keep_counting_their_number(world, artifact_id, artifact_type):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(
        principal=principal, project=project, artifact_id=artifact_id,
        artifact_type=artifact_type, title="t", content="revision 1",
    )
    ids = _supersede_n_times(engine, principal, project, artifact_id, 4)
    prefix, number = artifact_id.rsplit("-", 1)
    assert ids == [f"{prefix}-{int(number) + i:03d}" for i in range(5)]


@pytest.mark.parametrize("artifact_type", ["documentation_fact", "database_decision"])
def test_a_legacy_repeated_suffix_head_gets_a_clean_successor(world, store, artifact_type):
    """Successors named before this rule (`x-v2-v2-v2`) keep their ids; the
    next one drops the repeated tail and carries the next version."""
    from datetime import datetime, timezone

    from mak4i.models import Artifact

    engine, principal, project = world["engine"], world["principal"], world["project"]
    org = world["organization_id"]
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    chain = ["open-issues", "open-issues-v2", "open-issues-v2-v2", "open-issues-v2-v2-v2"]
    for version, (artifact_id, successor) in enumerate(zip(chain, chain[1:] + [None]), start=1):
        store.put_new(Artifact(
            artifact_id=artifact_id, artifact_type=artifact_type, organization_id=org,
            project=project, title="t", content=f"revision {version}",
            status="active" if successor is None else "superseded", version=f"{version}.0",
            created_by=principal.principal_id, created_at=now, updated_at=now,
            lineage_id="open-issues", supersedes=chain[version - 2] if version > 1 else None,
            superseded_by=successor,
        ))
    new = engine.supersede_artifact(
        principal=principal, project=project, old_id="open-issues-v2-v2-v2",
        content="revision 5", reason="update",
    )
    assert new.artifact_id == "open-issues-v5" and new.version == "5.0"
    history = engine.get_history(principal=principal, project=project, lineage_id="open-issues")
    assert [a.artifact_id for a in history] == chain + ["open-issues-v5"]  # nothing renamed
    assert store.get(org, project, "open-issues-v2-v2-v2")[0].superseded_by == "open-issues-v5"


@pytest.mark.parametrize("artifact_type", ["documentation_fact", "caching_decision"])
def test_a_taken_successor_id_is_skipped(world, store, artifact_type):
    engine, principal, project = world["engine"], world["principal"], world["project"]
    for artifact_id in ("doc", "doc-v3"):  # doc-v3 is an unrelated record
        engine.create_artifact(
            principal=principal, project=project, artifact_id=artifact_id,
            artifact_type=artifact_type, title=artifact_id, content=artifact_id,
        )
    ids = _supersede_n_times(engine, principal, project, "doc", 2)
    assert ids == ["doc", "doc-v2", "doc-v4"]
    assert store.get(world["organization_id"], project, "doc-v3")[0].content == "doc-v3"  # untouched
    history = engine.get_history(principal=principal, project=project, lineage_id="doc")
    assert [a.version for a in history] == ["1.0", "2.0", "3.0"]


def test_an_id_that_already_ends_in_v_n_is_never_reused(world):
    """An original id like `api-v2` (version 1.0): its successor can't be
    itself, so it takes the next free -vN."""
    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(
        principal=principal, project=project, artifact_id="api-v2",
        artifact_type="documentation_fact", title="t", content="revision 1",
    )
    assert _supersede_n_times(engine, principal, project, "api-v2", 2) == ["api-v2", "api-v3", "api-v4"]


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


@pytest.mark.parametrize("artifact_type", ["caching_decision", "database_decision"])
@pytest.mark.parametrize("reason", ["", "   ", "\n\t"])
def test_supersede_requires_a_non_blank_reason(world, store, artifact_type, reason):  # MAK-0008 §10, CLI parity
    from mak4i.errors import ValidationFailedError

    engine, principal, project = world["engine"], world["principal"], world["project"]
    engine.create_artifact(principal=principal, project=project, artifact_id="d-001",
                           artifact_type=artifact_type, title="T", content="v1")
    with pytest.raises(ValidationFailedError):
        engine.supersede_artifact(principal=principal, project=project, old_id="d-001", content="v2", reason=reason)
    assert [a.artifact_id for a in engine.get_history(principal=principal, project=project, lineage_id="d-001")] == ["d-001"]
