"""Versioned configuration schema and validation (MAK-0008 §12, R5)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mak4i.settings import ConfigError, describe, load_settings, resolve_secret, settings_json_schema

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_are_a_valid_local_stdio_configuration():
    s = load_settings({})
    assert (s.schema_version, s.deployment_mode, s.transport, s.port) == ("1", "local", "stdio", 8080)
    assert s.control_plane.database.source == "default"
    assert s.auth.oauth is None and s.auth.principal_credentials is True


def test_self_hosted_http_with_oauth():
    s = load_settings({
        "MAK4I_TRANSPORT": "http", "MAK4I_HOST": "0.0.0.0", "MAK4I_PUBLIC_ENDPOINT": "https://m.example.com/mcp",
        "MAK4I_OAUTH_ENABLED": "1", "MAK4I_OAUTH_DCR": "1", "MAK4I_TRUSTED_PROXIES": "172.18.0.0/16, 10.0.0.5",
        "MAK4I_LOG_LEVEL": "warning",
    })
    assert s.deployment_mode == "self-hosted" and s.transport == "streamable-http"
    assert s.auth.oauth.issuer == "https://m.example.com" and s.auth.oauth.dynamic_client_registration
    assert s.proxy.trusted_proxies == ["172.18.0.0/16", "10.0.0.5"] and s.logging.level == "WARNING"


def test_every_problem_is_reported_at_once():
    with pytest.raises(ConfigError) as exc:
        load_settings({
            "MAK4I_TRANSPORT": "carrier-pigeon", "MAK4I_PORT": "99999", "MAK4I_STORE": "gcs",
            "MAK4I_TRUSTED_PROXIES": "*", "MAK4I_LOG_LEVEL": "LOUD", "MAK4I_DEPLOYMENT_MODE": "cloud",
            "MAK4I_OAUTH_ENABLED": "1",
        })
    text = "\n".join(exc.value.problems)
    for fragment in ("MAK4I_TRANSPORT", "MAK4I_PORT", "MAK4I_GCS_BUCKET", "TRUSTED_PROXIES", "MAK4I_LOG_LEVEL",
                     "MAK4I_DEPLOYMENT_MODE", "MAK4I_PUBLIC_ENDPOINT"):
        assert fragment in text, fragment


def test_oauth_is_http_only():
    with pytest.raises(ConfigError, match="HTTP transport only"):
        load_settings({"MAK4I_OAUTH_ENABLED": "1", "MAK4I_PUBLIC_ENDPOINT": "https://m.example.com/mcp"})


def test_secrets_by_file_reference_and_never_printed(tmp_path):
    secret = tmp_path / "db-url"
    secret.write_text("postgresql+psycopg://mak4i:TOP-SECRET@db/mak4i\n")
    env = {"MAK4I_CONTROL_PLANE_DB_FILE": str(secret), "MAK4I_TOKEN": "mak4i_supersecret"}
    s = load_settings(env)
    assert s.control_plane.database.source == "file"
    printed = json.dumps(describe(s))
    assert "TOP-SECRET" not in printed and "supersecret" not in printed
    assert resolve_secret("MAK4I_CONTROL_PLANE_DB", env) == "postgresql+psycopg://mak4i:TOP-SECRET@db/mak4i"
    with pytest.raises(ConfigError, match="not both"):
        load_settings({**env, "MAK4I_CONTROL_PLANE_DB": "sqlite://"})
    with pytest.raises(ConfigError, match="unreadable"):
        load_settings({"MAK4I_CONTROL_PLANE_DB_FILE": str(tmp_path / "missing")})


def test_published_schema_is_in_sync_and_validates_the_output():
    published = json.loads((ROOT / "docs" / "config.schema.json").read_text())
    assert published == settings_json_schema(), "regenerate with: mak4i config schema > docs/config.schema.json"
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator(published).validate(describe(load_settings({"MAK4I_TRANSPORT": "http"})))


def test_server_refuses_to_start_on_invalid_configuration(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT / "src"), "MAK4I_TRANSPORT": "http",
           "MAK4I_OAUTH_ENABLED": "1", "HOME": str(tmp_path)}
    proc = subprocess.run([sys.executable, "-m", "mak4i.mcp_server"], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2
    assert "MAK4I could not start" in proc.stderr and "MAK4I_PUBLIC_ENDPOINT" in proc.stderr
