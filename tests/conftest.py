"""Suite-wide test settings."""

import pytest


@pytest.fixture(autouse=True)
def _cli_audit_log_off(monkeypatch):
    """CLI commands write audit events to stderr or `.mak4i/audit.log` by
    default (MAK-0006 §8.6). Tests assert on CLI stderr, so the CLI's own
    handler is off here; audit events still reach pytest's `caplog`.
    `tests/test_cli_audit_log.py` covers the CLI handler itself."""
    monkeypatch.setenv("MAK4I_AUDIT_LOG", "off")
