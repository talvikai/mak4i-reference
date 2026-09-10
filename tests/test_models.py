from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mak4i.models import Artifact, bump_version

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


def _fields(**overrides):
    base = dict(
        artifact_id="decision-cache-001",
        artifact_type="architecture_decision",
        organization_id="org_test",
        project="schedovia",
        title="Application caching technology",
        content="Use Redis for application caching.",
        rationale="Selected for fast shared caching and established client support.",
        status="active",
        version="1.0",
        created_by="claude.ai",
        created_at=NOW,
        updated_at=NOW,
        lineage_id="decision-cache-001",
        supersedes=None,
        superseded_by=None,
        tags=["caching", "redis", "architecture"],
    )
    base.update(overrides)
    return base


def test_minimal_example_from_requirements_doc_validates():
    artifact = Artifact(**_fields())
    assert artifact.artifact_id == "decision-cache-001"
    assert artifact.status == "active"
    assert artifact.tags == ["caching", "redis", "architecture"]


@pytest.mark.parametrize(
    "fields",
    [
        # A caching decision (Redis) ...
        _fields(),
        # ... and a database decision (MySQL) must validate through the
        # identical model with no technology-specific branching (§20/§22).
        _fields(
            artifact_id="decision-db-002",
            artifact_type="architecture_decision",
            title="Primary database technology",
            content="Use MySQL for the primary application database.",
            rationale="Team familiarity and managed-hosting availability.",
            version="2.0",
            lineage_id="decision-db-001",
            supersedes="decision-db-001",
            tags=["database", "mysql", "architecture"],
        ),
        # A plain documentation fact, no "decision" flavor at all.
        _fields(
            artifact_id="doc-api-conventions-001",
            artifact_type="documentation_fact",
            title="API pagination convention",
            content="All list endpoints use cursor-based pagination.",
            rationale=None,
            lineage_id="doc-api-conventions-001",
            tags=["api", "documentation"],
        ),
    ],
)
def test_generic_model_accepts_any_scenario(fields):
    Artifact(**fields)


@pytest.mark.parametrize(
    "field", ["artifact_id", "artifact_type", "organization_id", "project", "title", "content", "version", "created_by", "lineage_id"]
)
def test_blank_required_string_fields_rejected(field):
    with pytest.raises(ValidationError):
        Artifact(**_fields(**{field: "   "}))


def test_missing_required_field_rejected():
    fields = _fields()
    del fields["status"]
    with pytest.raises(ValidationError):
        Artifact(**fields)


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        Artifact(**_fields(status="draft"))


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        Artifact(**_fields(created_at=datetime(2026, 9, 1, 15, 0)))


def test_blank_tag_rejected():
    with pytest.raises(ValidationError):
        Artifact(**_fields(tags=["caching", "  "]))


def test_rationale_is_optional():
    artifact = Artifact(**_fields(rationale=None))
    assert artifact.rationale is None


def test_artifact_is_immutable():
    artifact = Artifact(**_fields())
    with pytest.raises(ValidationError):
        artifact.status = "superseded"


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        Artifact(**_fields(technology="redis"))


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.0", "2.0"), ("2.0", "3.0"), ("9.0", "10.0")],
)
def test_bump_version(version, expected):
    assert bump_version(version) == expected


def test_bump_version_rejects_non_numeric():
    with pytest.raises(ValueError):
        bump_version("latest")
