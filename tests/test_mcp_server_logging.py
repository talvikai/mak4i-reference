import logging

from mak4i.audit import LOGGER_NAME, AuditLogger
from mak4i.mcp_server import configure_audit_logging


def test_configure_audit_logging_makes_info_events_actually_emit(capsys, monkeypatch):
    """Regression test: without configure_audit_logging(), AuditLogger's
    logger.info(...) calls are silent no-ops in a fresh process — Python's
    root logger defaults to WARNING with no handler attached, so nothing
    reaches stdout for Cloud Logging to capture. This was discovered as a
    real defect during M11: audit events never appeared in Cloud Run logs
    despite passing tests, because pytest's caplog fixture configures
    logging for us and masked the gap."""
    logger = logging.getLogger(LOGGER_NAME)
    monkeypatch.setattr(logger, "handlers", [], raising=False)
    monkeypatch.setattr(logger, "level", logging.NOTSET, raising=False)
    monkeypatch.setattr(logging.root, "handlers", [], raising=False)
    monkeypatch.setattr(logging.root, "level", logging.WARNING, raising=False)

    AuditLogger().log("RESPONSE", correlation_id="corr-1", actor="test", summary="before")
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""  # confirms the bug reproduces pre-fix

    configure_audit_logging()

    AuditLogger().log("RESPONSE", correlation_id="corr-2", actor="test", summary="after")
    # logging.basicConfig()'s default StreamHandler writes to stderr, not
    # stdout — Cloud Run/Cloud Logging captures both, so either is fine in
    # production, but the assertion has to match where it actually lands.
    err = capsys.readouterr().err
    assert '"event": "RESPONSE"' in err
    assert '"correlation_id": "corr-2"' in err
