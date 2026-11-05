"""Successor ids from historical heads (regression for PR #56).

Records named before #56 still carry repeated suffixes (`x-v2-v2-v2`,
`x-v1-v2`), and MAK4I never renames them. These tests seed lineages in
those shapes, the ones found in a real installation, and check that a
fresh supersede from such a head gets a clean, unused id, keeps the
lineage and leaves every old id as it is, on both artifact stores."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mak4i.api import ArtifactNotActiveError, MAK4IEngine
from mak4i.identity import Authorizer
from mak4i.models import Artifact
from mak4i.store.gcs import GCSArtifactStore
from mak4i.store.local_json import LocalJSONStore
from tests.test_api import _onboarded_world
from tests.test_gcs_store import _FakeGCSClient

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
TYPES = ["documentation_fact", "database_decision"]


@pytest.fixture(params=["local_json", "gcs"])
def world(request, tmp_path):
    if request.param == "local_json":
        store = LocalJSONStore(tmp_path / "artifacts")
    else:
        store = GCSArtifactStore("example-mak4i-artifacts", client=_FakeGCSClient())
    control_plane, organization_id, project, principal = _onboarded_world()
    engine = MAK4IEngine(store, authorizer=Authorizer(control_plane), control_plane=control_plane)
    return {"engine": engine, "store": store, "org": organization_id, "project": project, "principal": principal}


def _seed_lineage(world, artifact_type: str, chain: list[str]) -> None:
    """Write `chain` as one lineage exactly as an older server left it:
    version N for the N-th id, every id but the last superseded."""
    for n, artifact_id in enumerate(chain, start=1):
        head = n == len(chain)
        world["store"].put_new(Artifact(
            artifact_id=artifact_id, artifact_type=artifact_type, organization_id=world["org"],
            project=world["project"], title="t", content=f"revision {n}",
            status="active" if head else "superseded", version=f"{n}.0",
            created_by=world["principal"].principal_id, created_at=NOW, updated_at=NOW,
            lineage_id=chain[0], supersedes=chain[n - 2] if n > 1 else None,
            superseded_by=None if head else chain[n],
        ))


def _supersede(world, old_id: str, n: int):
    return world["engine"].supersede_artifact(
        principal=world["principal"], project=world["project"], old_id=old_id,
        content=f"revision {n}", reason=f"update {n}",
    )


def _history_ids(world, lineage_id: str) -> list[str]:
    history = world["engine"].get_history(
        principal=world["principal"], project=world["project"], lineage_id=lineage_id
    )
    return [a.artifact_id for a in history]


# Shapes found among one installation's 324 active heads on 2026-11-05
# (ids shortened; content is synthetic). Expected: the base without its
# suffix run, plus -v<next major version>.
LEGACY_CHAINS = [
    pytest.param(
        ["design-review", "design-review-v2", "design-review-v2-v2", "design-review-v2-v2-v2"],
        ["design-review-v5", "design-review-v6"],
        id="repeated-v2-x3",
    ),
    pytest.param(
        ["release-plan"] + ["release-plan" + "-v2" * k for k in range(1, 6)],
        ["release-plan-v7", "release-plan-v8"],
        id="repeated-v2-x5",
    ),
    pytest.param(
        ["outreach-skytek-v1", "outreach-skytek-v1-v2"],
        ["outreach-skytek-v3", "outreach-skytek-v4"],
        id="mixed-v1-v2",
    ),
    pytest.param(
        ["requirements-reference-v2"],
        ["requirements-reference-v3", "requirements-reference-v4"],
        id="original-id-ends-in-v2",
    ),
]


@pytest.mark.parametrize("artifact_type", TYPES)
@pytest.mark.parametrize("chain,expected", LEGACY_CHAINS)
def test_fresh_supersedes_from_a_legacy_head(world, artifact_type, chain, expected):
    _seed_lineage(world, artifact_type, chain)

    first = _supersede(world, chain[-1], len(chain) + 1)
    second = _supersede(world, first.artifact_id, len(chain) + 2)

    assert [first.artifact_id, second.artifact_id] == expected
    assert [first.version, second.version] == [f"{len(chain) + 1}.0", f"{len(chain) + 2}.0"]
    assert first.lineage_id == second.lineage_id == chain[0]
    # Nothing renamed: the old ids are history, linked to the new head.
    assert _history_ids(world, chain[0]) == chain + expected
    old_head, _ = world["store"].get(world["org"], world["project"], chain[-1])
    assert (old_head.status, old_head.superseded_by) == ("superseded", first.artifact_id)
    current = world["engine"].get_current(
        principal=world["principal"], project=world["project"], artifact_type=artifact_type
    )
    assert [a.artifact_id for a in current.artifacts] == [second.artifact_id]


@pytest.mark.parametrize("artifact_type", TYPES)
def test_a_legacy_head_skips_a_clean_id_another_record_holds(world, artifact_type):
    """The clean successor of `cache-v2-v2` is `cache-v4`... unless an
    unrelated record already holds it; then the next free id."""
    _seed_lineage(world, artifact_type, ["cache", "cache-v2", "cache-v2-v2"])
    world["engine"].create_artifact(
        principal=world["principal"], project=world["project"], artifact_id="cache-v4",
        artifact_type=artifact_type, title="unrelated", content="unrelated",
    )

    new = _supersede(world, "cache-v2-v2", 4)

    assert (new.artifact_id, new.version, new.lineage_id) == ("cache-v5", "4.0", "cache")
    unrelated, _ = world["store"].get(world["org"], world["project"], "cache-v4")
    assert (unrelated.content, unrelated.lineage_id, unrelated.status) == ("unrelated", "cache-v4", "active")


@pytest.mark.parametrize("artifact_type", TYPES)
def test_a_superseded_legacy_id_cannot_be_superseded_again(world, artifact_type):
    """A client holding a stale legacy id is refused, not given a fork."""
    chain = ["plan", "plan-v2", "plan-v2-v2"]
    _seed_lineage(world, artifact_type, chain)
    new = _supersede(world, "plan-v2-v2", 4)

    for stale in chain:
        with pytest.raises(ArtifactNotActiveError):
            _supersede(world, stale, 5)
    assert _history_ids(world, "plan") == chain + [new.artifact_id]
