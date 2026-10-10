"""Versioned, validated configuration (MAK-0008 §12; requirements R5).

MAK4I is configured through `MAK4I_*` environment variables (unchanged from
earlier releases — this module adds validation and a schema, not a second
configuration system). `load_settings()` turns the environment into one
validated `Settings` object, collecting *every* problem into a single
`ConfigError` so a misconfigured server refuses to start with a complete
explanation. `settings_json_schema()` is the published schema
(`docs/config.schema.json`, kept in sync by a test).

Secrets are never inlined in this model: the control-plane database URL
(which carries a password) and the stdio token can be given directly or as
a file reference (`MAK4I_CONTROL_PLANE_DB_FILE`, `MAK4I_TOKEN_FILE`) for
Docker/Kubernetes secrets, and `describe()` redacts them.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONFIG_SCHEMA_VERSION = "1"
SCHEMA_ID = "https://github.com/talvikai/mak4i-reference/blob/main/docs/config.schema.json"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("invalid MAK4I configuration:\n" + "\n".join(f"  - {p}" for p in problems))
        self.problems = problems


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SecretRef(_Model):
    """Where a secret comes from — never its value."""

    source: Literal["env", "file", "default", "unset"]
    reference: str | None = Field(None, description="Environment variable name or file path.")


class StorageSettings(_Model):
    backend: Literal["local", "gcs"] = "local"
    local_store_dir: str = "artifacts/local"
    gcs_bucket: str | None = None
    gcs_project: str | None = None


class ControlPlaneSettings(_Model):
    database: SecretRef
    create_tables: bool = Field(False, description="Throwaway databases only.")


class OAuthPolicy(_Model):
    issuer: str
    client_id_metadata_documents: bool
    dynamic_client_registration: bool
    cimd_allowed_hosts: list[str]
    authorization_code_ttl_seconds: int
    access_token_ttl_seconds: int
    refresh_idle_ttl_seconds: int
    refresh_absolute_ttl_seconds: int
    sign_in_code_ttl_seconds: int
    rate_limit_per_minute: int


class AuthSettings(_Model):
    principal_credentials: Literal[True] = Field(
        True, description="Bearer credentials are always accepted (MAK-0008 §2)."
    )
    oauth: OAuthPolicy | None = Field(None, description="Present when MAK4I_OAUTH_ENABLED=1.")
    stdio_token: SecretRef


class ProxySettings(_Model):
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description="IPs/CIDRs allowed to set X-Forwarded-For/-Proto (client address for rate limits "
        "and logs). Advertised URLs never come from request headers.",
    )


class LoggingSettings(_Model):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class Settings(_Model):
    """The effective configuration of one MAK4I process."""

    schema_version: Literal["1"] = CONFIG_SCHEMA_VERSION
    deployment_mode: Literal["local", "self-hosted"]
    transport: Literal["stdio", "streamable-http"]
    host: str
    port: int = Field(ge=1, le=65535)
    public_endpoint: str | None = None
    instance_name: str = "MAK4I"
    environment: str = "unspecified"
    storage: StorageSettings
    control_plane: ControlPlaneSettings
    auth: AuthSettings
    proxy: ProxySettings
    logging: LoggingSettings


def _secret_ref(env, name: str, *, default: str | None, problems: list[str]) -> tuple[SecretRef, str | None]:
    direct, file_name = env.get(name), env.get(f"{name}_FILE")
    if direct and file_name:
        problems.append(f"set either {name} or {name}_FILE, not both")
        return SecretRef(source="env", reference=name), direct
    if file_name:
        try:
            value = Path(file_name).read_text().strip()
        except OSError:
            problems.append(f"{name}_FILE points to an unreadable file")
            return SecretRef(source="file", reference=file_name), None
        if not value:
            problems.append(f"{name}_FILE is empty")
        return SecretRef(source="file", reference=file_name), value or None
    if direct:
        return SecretRef(source="env", reference=name), direct
    if default is not None:
        return SecretRef(source="default", reference=None), default
    return SecretRef(source="unset", reference=None), None


def resolve_secret(name: str, env=None, default: str | None = None) -> str | None:
    """The value of a secret that may be given as `NAME` or `NAME_FILE`."""
    problems: list[str] = []
    _ref, value = _secret_ref(os.environ if env is None else env, name, default=default, problems=problems)
    if problems:
        raise ConfigError(problems)
    return value


def load_settings(env=None) -> Settings:
    from mak4i.config import DEFAULT_CONTROL_PLANE_DB
    from mak4i.oauth.settings import OAuthConfigError, OAuthSettings

    env = os.environ if env is None else env
    problems: list[str] = []

    raw_transport = env.get("MAK4I_TRANSPORT", "stdio")
    transport = "streamable-http" if raw_transport in ("http", "streamable-http") else raw_transport
    if transport not in ("stdio", "streamable-http"):
        problems.append(f"MAK4I_TRANSPORT must be stdio or http, not {raw_transport!r}")
        transport = "stdio"
    host = env.get("MAK4I_HOST", "127.0.0.1")
    port_raw = env.get("MAK4I_PORT") or env.get("PORT") or "8080"
    try:
        port = int(port_raw)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        problems.append(f"MAK4I_PORT must be 1-65535, not {port_raw!r}")
        port = 8080

    mode = env.get("MAK4I_DEPLOYMENT_MODE") or ("self-hosted" if host not in _LOOPBACK else "local")
    if mode not in ("local", "self-hosted"):
        problems.append("MAK4I_DEPLOYMENT_MODE must be local or self-hosted")
        mode = "local"

    backend = env.get("MAK4I_STORE", "local")
    if backend not in ("local", "gcs"):
        problems.append("MAK4I_STORE must be local or gcs")
        backend = "local"
    if backend == "gcs" and not env.get("MAK4I_GCS_BUCKET"):
        problems.append("MAK4I_STORE=gcs requires MAK4I_GCS_BUCKET")
    storage = StorageSettings(
        backend=backend,
        local_store_dir=env.get("MAK4I_LOCAL_STORE_DIR", "artifacts/local"),
        gcs_bucket=env.get("MAK4I_GCS_BUCKET") or None,
        gcs_project=env.get("MAK4I_GCP_PROJECT") or None,
    )

    db_ref, _db = _secret_ref(env, "MAK4I_CONTROL_PLANE_DB", default=DEFAULT_CONTROL_PLANE_DB, problems=problems)
    token_ref, _tok = _secret_ref(env, "MAK4I_TOKEN", default=None, problems=problems)

    oauth_policy = None
    try:
        oauth = OAuthSettings.from_env(env)
    except OAuthConfigError as exc:
        problems.append(str(exc))
        oauth = None
    if oauth is not None:
        if transport != "streamable-http":
            problems.append("OAuth applies to the HTTP transport only (MAK4I_TRANSPORT=http); stdio uses MAK4I_TOKEN")
        oauth_policy = OAuthPolicy(
            issuer=oauth.issuer,
            client_id_metadata_documents=oauth.enable_cimd,
            dynamic_client_registration=oauth.enable_dcr,
            cimd_allowed_hosts=list(oauth.cimd_allowed_hosts),
            authorization_code_ttl_seconds=oauth.code_ttl,
            access_token_ttl_seconds=oauth.access_token_ttl,
            refresh_idle_ttl_seconds=oauth.refresh_idle_ttl,
            refresh_absolute_ttl_seconds=oauth.refresh_absolute_ttl,
            sign_in_code_ttl_seconds=oauth.sign_in_code_ttl,
            rate_limit_per_minute=oauth.rate_limit_per_minute,
        )

    proxies = [p.strip() for p in (env.get("MAK4I_TRUSTED_PROXIES") or "").split(",") if p.strip()]
    for proxy in proxies:
        try:
            ipaddress.ip_network(proxy, strict=False)
        except ValueError:
            problems.append(f"MAK4I_TRUSTED_PROXIES entry {proxy!r} is not an IP address or CIDR")
    if "*" in (env.get("MAK4I_TRUSTED_PROXIES") or ""):
        problems.append("MAK4I_TRUSTED_PROXIES must list addresses; '*' would let any client spoof its address")

    level = (env.get("MAK4I_LOG_LEVEL") or "INFO").upper()
    if level not in _LOG_LEVELS:
        problems.append(f"MAK4I_LOG_LEVEL must be one of {', '.join(_LOG_LEVELS)}")
        level = "INFO"

    endpoint = env.get("MAK4I_PUBLIC_ENDPOINT") or None

    if problems:
        raise ConfigError(problems)
    return Settings(
        deployment_mode=mode,
        transport=transport,
        host=host,
        port=port,
        public_endpoint=endpoint,
        instance_name=env.get("MAK4I_INSTANCE_NAME") or "MAK4I",
        environment=env.get("MAK4I_ENVIRONMENT") or "unspecified",
        storage=storage,
        control_plane=ControlPlaneSettings(
            database=db_ref, create_tables=env.get("MAK4I_CONTROL_PLANE_CREATE_TABLES") == "1"
        ),
        auth=AuthSettings(oauth=oauth_policy, stdio_token=token_ref),
        proxy=ProxySettings(trusted_proxies=proxies),
        logging=LoggingSettings(level=level),
    )


def describe(settings: Settings) -> dict:
    """The effective configuration, safe to print: secrets appear only as
    references (`SecretRef`), never values."""
    return settings.model_dump(mode="json")


def settings_json_schema() -> dict:
    schema = Settings.model_json_schema()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "MAK4I Reference effective configuration",
        "description": (
            "Produced by `mak4i config check` from MAK4I_* environment variables. "
            f"Schema version {CONFIG_SCHEMA_VERSION}; conforms to MAK-0008 §12."
        ),
        **{k: v for k, v in schema.items() if k not in ("title", "description")},
    }
    return schema
