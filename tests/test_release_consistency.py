"""The release-consistency check (scripts/check_release_consistency.py):
the real tree must pass, and each kind of drift must be caught."""

from __future__ import annotations

from pathlib import Path

import pytest

import check_release_consistency as crc

TAG = "v0.1.0-rc.4"


def _fixture_repo(tmp_path: Path, *, version: str = "0.1.0rc4") -> Path:
    tag = crc.pep440_to_tag(version)
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "mak4i"\nversion = "{version}"\n')
    (tmp_path / "uv.lock").write_text(f'[[package]]\nname = "mak4i"\nversion = "{version}"\n')
    (tmp_path / "Dockerfile").write_text(f'LABEL org.opencontainers.image.version="{tag[1:]}"\n')
    (tmp_path / "README.md").write_text(f"**Current release: [`{tag}`](https://example)**\n")
    docs = tmp_path / "docs"
    docs.mkdir()
    for guide in ("LOCAL_SETUP.md", "ENTERPRISE_SELF_HOSTED.md"):
        (docs / guide).write_text(f"git clone --branch {tag} --depth 1 https://example.git\n")
    bootstrap = tmp_path / "deploy" / "bootstrap"
    bootstrap.mkdir(parents=True)
    (bootstrap / "mak4i-enterprise").write_text(f'readonly MAK4I_RELEASE="{tag}"\n')
    return tmp_path


def test_the_repository_is_consistent():
    errors, _ = crc.check(crc.ROOT)
    assert errors == [], "\n".join(map(str, errors))


def test_pep440_to_tag():
    assert crc.pep440_to_tag("0.1.0rc4") == TAG
    with pytest.raises(ValueError):
        crc.pep440_to_tag("0.1.0")


def test_a_consistent_fixture_passes(tmp_path):
    errors, _ = crc.check(_fixture_repo(tmp_path))
    assert errors == []


@pytest.mark.parametrize(
    ("path", "old", "new", "expected"),
    [
        ("uv.lock", 'version = "0.1.0rc4"', 'version = "0.1.0rc3"', "uv.lock"),
        ("Dockerfile", "0.1.0-rc.4", "0.1.0-rc.3", "image version label"),
        ("deploy/bootstrap/mak4i-enterprise", TAG, "v0.1.0-rc.3", "MAK4I_RELEASE"),
        ("README.md", TAG, "v0.1.0-rc.3", "Current release"),
        ("docs/LOCAL_SETUP.md", TAG, "v0.1.0-rc.3", "command selects v0.1.0-rc.3"),
        ("docs/ENTERPRISE_SELF_HOSTED.md", TAG, "v0.1.0-rc.5", "future release"),
    ],
)
def test_each_kind_of_drift_is_caught(tmp_path, path, old, new, expected):
    root = _fixture_repo(tmp_path)
    target = root / path
    target.write_text(target.read_text().replace(old, new))
    errors, _ = crc.check(root)
    assert any(expected in str(e) for e in errors), errors


def test_historical_prose_is_reported_not_rejected(tmp_path):
    root = _fixture_repo(tmp_path)
    guide = root / "docs" / "ENTERPRISE_SELF_HOSTED.md"
    guide.write_text(
        guide.read_text()
        + "Upgrading from v0.1.0-rc.3 keeps your data.\n"
        + "./deploy/bootstrap/mak4i-enterprise upgrade --from-version v0.1.0-rc.3\n"
    )
    errors, others = crc.check(root)
    assert errors == []
    assert sum("v0.1.0-rc.3" in str(o) for o in others) == 2


def test_a_marked_rollback_command_is_allowed(tmp_path):
    root = _fixture_repo(tmp_path)
    guide = root / "docs" / "ENTERPRISE_SELF_HOSTED.md"
    guide.write_text(guide.read_text() + "git checkout v0.1.0-rc.3   # rollback to the previous release\n")
    errors, _ = crc.check(root)
    assert errors == []


def test_an_old_checkout_command_is_rejected(tmp_path):
    root = _fixture_repo(tmp_path)
    guide = root / "docs" / "LOCAL_SETUP.md"
    guide.write_text(guide.read_text() + "git checkout v0.1.0-rc.3\n")
    errors, _ = crc.check(root)
    assert any("command selects v0.1.0-rc.3" in str(e) for e in errors)


def test_missing_bootstrap_is_an_error(tmp_path):
    root = _fixture_repo(tmp_path)
    (root / "deploy" / "bootstrap" / "mak4i-enterprise").unlink()
    errors, _ = crc.check(root)
    assert any("bootstrap script missing" in str(e) for e in errors)
