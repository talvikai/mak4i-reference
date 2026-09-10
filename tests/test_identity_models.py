from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from mak4i.identity import (
    Credential,
    Grant,
    Organization,
    Principal,
    Project,
    generate_token,
    hash_token,
    tokens_match,
)
from mak4i.identity.models import (
    new_credential_id,
    new_grant_id,
    new_organization_id,
    new_principal_id,
    new_project_id,
)

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc)


# -- id generation --------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "prefix"),
    [
        (new_organization_id, "org_"),
        (new_principal_id, "prn_"),
        (new_project_id, "prj_"),
        (new_grant_id, "grt_"),
        (new_credential_id, "cred_"),
    ],
)
def test_ids_have_entity_prefix_and_are_unique(factory, prefix):
    ids = {factory() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith(prefix) for i in ids)


# -- tokens -------------------------------------------------------------------


def test_generate_token_returns_prefixed_raw_and_matching_hash():
    raw, token_hash = generate_token()
    assert raw.startswith("mak4i_")
    assert token_hash == hash_token(raw)
    assert raw != token_hash
    assert len(token_hash) == 64  # sha-256 hex


def test_distinct_tokens_hash_differently():
    (raw_a, hash_a), (raw_b, hash_b) = generate_token(), generate_token()
    assert raw_a != raw_b
    assert hash_a != hash_b


def test_tokens_match_is_true_only_for_the_right_token():
    raw, token_hash = generate_token()
    assert tokens_match(raw, token_hash)
    assert not tokens_match(raw + "x", token_hash)
    assert not tokens_match("mak4i_wrong", token_hash)


# -- entity validation ------------------------------------------------------


def test_each_entity_validates_a_minimal_example():
    org = Organization(name="Example Org")
    prn = Principal(organization_id=org.organization_id, type="human", display_name="A")
    prj = Project(organization_id=org.organization_id, name="Example Project")
    grant = Grant(principal_id=prn.principal_id, project_id=prj.project_id, permissions=["read"])
    cred = Credential(principal_id=prn.principal_id, token_hash=hash_token("mak4i_x"))

    assert org.status == "active"
    assert prn.role == "member"
    assert prj.status == "active"
    assert grant.permissions == ["read"]
    assert cred.status == "active"


def test_entities_are_immutable_and_reject_unknown_fields():
    org = Organization(name="Example Org")
    with pytest.raises(ValidationError):
        org.name = "renamed"
    with pytest.raises(ValidationError):
        Organization(name="Example Org", plan="enterprise")


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_required_strings_are_rejected(blank):
    with pytest.raises(ValidationError):
        Organization(name=blank)
    with pytest.raises(ValidationError):
        Principal(organization_id=blank, type="human", display_name="A")


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValidationError):
        Organization(name="Example Org", created_at=datetime(2026, 9, 1, 15, 0))


# -- grant permissions ----------------------------------------------------


def test_grant_requires_at_least_one_permission():
    with pytest.raises(ValidationError):
        Grant(principal_id="prn_a", project_id="prj_b", permissions=[])


def test_grant_rejects_unknown_permission():
    with pytest.raises(ValidationError):
        Grant(principal_id="prn_a", project_id="prj_b", permissions=["read", "admin"])


def test_grant_permissions_are_deduped_and_ordered():
    grant = Grant(
        principal_id="prn_a",
        project_id="prj_b",
        permissions=["write", "read", "write"],
    )
    assert grant.permissions == ["read", "write"]
    assert grant.allows("read") and grant.allows("write")


# -- credential usability --------------------------------------------------


def test_active_credential_is_usable():
    cred = Credential(principal_id="prn_a", token_hash=hash_token("mak4i_x"))
    assert cred.is_usable(NOW)


def test_revoked_credential_is_not_usable():
    cred = Credential(
        principal_id="prn_a",
        token_hash=hash_token("mak4i_x"),
        status="revoked",
        revoked_at=NOW,
    )
    assert not cred.is_usable(NOW)


def test_expired_credential_is_not_usable():
    cred = Credential(
        principal_id="prn_a",
        token_hash=hash_token("mak4i_x"),
        expires_at=NOW - timedelta(seconds=1),
    )
    assert not cred.is_usable(NOW)
    assert cred.is_usable(NOW - timedelta(hours=1))
