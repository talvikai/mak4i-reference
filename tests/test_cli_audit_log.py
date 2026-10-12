"""MAK-0006 §8.6: CLI operations (operator writes and refusals) emit audit
events; `MAK4I_AUDIT_LOG` picks the destination."""

from __future__ import annotations

import json
import logging

import pytest

from mak4i import cli
from mak4i.audit import LOGGER_NAME
from tests.test_cli import local_store_env, world  # noqa: F401  (fixtures)

TYPES = ["caching_decision", "database_decision"]


@pytest.fixture
def audit_file(tmp_path, monkeypatch):
    path = tmp_path / "audit.log"
    monkeypatch.setenv("MAK4I_AUDIT_LOG", str(path))
    logger = logging.getLogger(LOGGER_NAME)
    before = list(logger.handlers)
    yield path
    for handler in logger.handlers[:]:
        if handler not in before:
            logger.removeHandler(handler)
            handler.close()


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("artifact_type", TYPES)
def test_cli_writes_and_refusals_reach_the_audit_log(world, audit_file, artifact_type, capsys):  # noqa: F811
    _organization_id, owner_id, project_id = world
    args = ["create", "--principal", owner_id, "--project", project_id, "--artifact-id", "rec-1",
            "--artifact-type", artifact_type, "--title", "t", "--content", "c"]
    assert cli.main(args) == 0
    assert cli.main(args) == 1  # duplicate id
    events = _events(audit_file)
    created = [e for e in events if e["event"] == "CREATE"]
    refused = [e for e in events if e["event"] == "WRITE_REFUSED"]
    assert created and created[0]["auth_method"] == "operator"
    assert refused and refused[0]["error_code"] == "artifact_exists"
    assert refused[0]["principal_id"] == owner_id and refused[0]["operation"] == "create"
    assert "c" not in {refused[0].get("content")}  # no record content in refusals
