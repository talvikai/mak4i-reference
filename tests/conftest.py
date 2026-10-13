"""Suite-wide test settings."""

import logging
import os

import pytest


@pytest.fixture(autouse=True)
def _cli_audit_log_discarded(monkeypatch):
    """CLI commands write audit events to stderr by default (MAK-0006 §8.6),
    and can't switch them off. In-process CLI tests assert on stderr, so they
    send the events to the null device here; audit events still reach
    pytest's `caplog`. `tests/test_cli_audit_log.py` and
    `tests/test_write_attribution.py` cover the real destinations. Handlers
    the CLI attaches during a test are removed afterwards."""
    from mak4i.audit import LOGGER_NAME

    monkeypatch.setenv("MAK4I_AUDIT_LOG", os.devnull)
    logger = logging.getLogger(LOGGER_NAME)
    before = list(logger.handlers)
    yield
    for handler in logger.handlers[:]:
        if handler not in before:
            logger.removeHandler(handler)
            handler.close()
