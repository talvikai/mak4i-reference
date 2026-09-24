"""Local-only developer configuration for `mak4i init` / `mak4i serve`.

This is a **convenience for local/self-hosted development only** — it lets a
developer run one `mak4i init` and then `mak4i serve` without hand-copying
ids and a raw token between commands. It has nothing to do with the hosted
Developer Preview, which never reads or writes any of these files.

Layout (all under `.mak4i/` in the current directory, or `$MAK4I_HOME`):

    .mak4i/
      config.json        ids + names + resolved backend settings (NO secret)
      credentials.json    {"token": "mak4i_..."}  — file mode 0600, dir 0700
      control-plane.db    the local instance's SQLite control plane
      artifacts/          the local instance's LocalJSONStore

The raw credential token is stored here in plaintext **on purpose** — it is
the price of the `serve` convenience, and it is the *only* place the raw
token exists (the control-plane database stores only its SHA-256 hash, per
`identity/tokens.py`). `.mak4i/` is gitignored. Do not reuse this mechanism
for anything hosted.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_DIR_ENV_VAR = "MAK4I_HOME"
_DEFAULT_DIR = ".mak4i"
_CONFIG_FILE = "config.json"
_CREDENTIALS_FILE = "credentials.json"

DEFAULT_HTTP_PORT = 9090
"""Local-only default for `mak4i serve --transport http`. Distinct from
`mcp_server.resolve_port()`'s own default, which the container/Enterprise
entry point (`python -m mak4i.mcp_server`) keeps using unchanged."""


class LocalConfig(BaseModel):
    """What `mak4i init` records so `mak4i serve` can start the same local
    instance. No secret lives here — the raw token is in a separate file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    organization_id: str
    organization_name: str
    owner_principal_id: str
    owner_display_name: str
    project_id: str
    project_name: str
    credential_id: str
    control_plane_db: str
    store: str
    local_store_dir: str | None = None
    created_at: str
    # Optional so configs written before this field existed still load;
    # `effective_http_port` supplies the default for them.
    http_port: int | None = Field(default=None, ge=1, le=65535)


def effective_http_port(config: LocalConfig) -> int:
    """The Local Streamable HTTP port this environment uses when neither
    `--port` nor `MAK4I_PORT` is given: the port chosen at `mak4i init`, or
    `DEFAULT_HTTP_PORT` for a config that predates the setting."""
    return config.http_port if config.http_port is not None else DEFAULT_HTTP_PORT


def home_dir() -> Path:
    """The `.mak4i/` directory for this working tree, or `$MAK4I_HOME`."""
    return Path(os.environ.get(_DIR_ENV_VAR, _DEFAULT_DIR))


def config_path() -> Path:
    return home_dir() / _CONFIG_FILE


def credentials_path() -> Path:
    return home_dir() / _CREDENTIALS_FILE


def exists() -> bool:
    """True once `mak4i init` has written a config here."""
    return config_path().is_file()


def default_control_plane_db() -> str:
    """SQLite URL for a fresh local instance, kept inside `.mak4i/` so the
    whole local environment is one self-contained, gitignored directory."""
    return f"sqlite:///{(home_dir() / 'control-plane.db').as_posix()}"


def default_local_store_dir() -> str:
    return (home_dir() / "artifacts").as_posix()


def ensure_home() -> Path:
    """Create the `.mak4i/` directory (mode 0700) and return its path."""
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(stat.S_IRWXU)  # 0700
    except OSError:
        pass  # best-effort (e.g. some Windows / network filesystems)
    return d


_ensure_home = ensure_home  # internal alias


def save(config: LocalConfig) -> None:
    _ensure_home()
    config_path().write_text(json.dumps(config.model_dump(), indent=2) + "\n")


def load() -> LocalConfig | None:
    path = config_path()
    if not path.is_file():
        return None
    return LocalConfig.model_validate_json(path.read_text())


def save_token(raw_token: str) -> None:
    """Persist the raw credential for `serve`, with restricted permissions."""
    _ensure_home()
    path = credentials_path()
    path.write_text(json.dumps({"token": raw_token}) + "\n")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass


def load_token() -> str | None:
    path = credentials_path()
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    token = data.get("token")
    return token if isinstance(token, str) and token else None


#: env vars that together identify one initialized local environment — the
#: control plane, the artifact store, and the credential that was issued
#: *into* that control plane. `mak4i serve` treats `.mak4i/` as the source
#: of truth for these, so `mak4i init && mak4i serve` is deterministic even
#: with a stale `export MAK4I_TOKEN=…` (or a stale DB URL) in the shell.
LOCAL_ENV_KEYS = ("MAK4I_CONTROL_PLANE_DB", "MAK4I_STORE", "MAK4I_LOCAL_STORE_DIR", "MAK4I_TOKEN")


def overridden_local_env_keys(config: LocalConfig, token: str | None) -> list[str]:
    """Which `LOCAL_ENV_KEYS` are currently set in the environment to a value
    that differs from this local config — i.e. what `apply_to_env` will
    override. Used only to print a heads-up (never the values)."""
    desired = _desired_local_env(config, token)
    return [k for k, v in desired.items() if k in os.environ and os.environ[k] != v]


def apply_to_env(config: LocalConfig, token: str | None) -> None:
    """Set the env vars `config.py` / `mcp_server.py` read so they describe
    the environment `mak4i init` created — **authoritatively**. A stale
    `MAK4I_TOKEN` / `MAK4I_CONTROL_PLANE_DB` in the shell is replaced, not
    respected, so `mak4i serve` reliably uses the initialized credential.

    This only affects the `mak4i serve` process. `python -m mak4i.mcp_server`
    is unchanged and still reads the environment as given.
    """
    for name, value in _desired_local_env(config, token).items():
        os.environ[name] = value


#: The three environment variables that together *define* a deployment
#: for the granular/operator/artifact commands — control plane, store
#: backend, and (for the local store) its directory. Presence of *any*
#: one of these is treated as "the caller already configured a target
#: explicitly" (see `resolve_ambient_local_environment`): the point is
#: to never mix an explicit choice for one of these with an ambient
#: `.mak4i/`'s value for another, which would produce a deployment that
#: matches neither the explicit configuration nor the local one.
_EXPLICIT_DEPLOYMENT_ENV_KEYS = ("MAK4I_CONTROL_PLANE_DB", "MAK4I_STORE", "MAK4I_LOCAL_STORE_DIR")


def resolve_ambient_local_environment() -> LocalConfig | None:
    """Resolution for every CLI command *except* `serve` and `init`
    (each of which has its own dedicated resolution — see their
    docstrings in `cli.py`): `org`/`project`/`principal`/`grant`/
    `credential`, the artifact commands, and `doctor`.

    Precedence:

    A. If the caller already explicitly configured any part of the
       deployment (`MAK4I_CONTROL_PLANE_DB`, `MAK4I_STORE`, or
       `MAK4I_LOCAL_STORE_DIR` already present in the environment),
       that is honored as-is and this function is a complete no-op —
       an ambient `.mak4i/` must never silently override an explicit
       target, and must never fill in the *other* pieces either (that
       would produce a mixed deployment: an explicit control-plane URL
       paired with an ambient artifact directory, or vice versa, is
       exactly the inconsistency this function exists to prevent).
    B. Otherwise, if an initialized local environment exists at
       `home_dir()`, its full configuration — control plane, store,
       and artifact directory together, as one unit — is applied.
    C. Otherwise, this is a no-op and the existing
       `config.py` fallback (`MAK4I_CONTROL_PLANE_DB` if set, else the
       hardcoded default) applies unchanged.

    This is deliberately different from `mak4i serve`'s resolution
    (`apply_to_env`, called directly from `_cmd_serve`), which treats an
    initialized local environment as authoritative even over an
    explicitly exported `MAK4I_CONTROL_PLANE_DB` — that is the RC's
    existing, preserved, shipped behavior for `serve` specifically, and
    is intentionally *not* extended to every other command: those
    commands are also the ones an operator is most likely to run
    against an explicitly-targeted Enterprise Self-Hosted deployment
    from an arbitrary working directory, where a leftover local
    `.mak4i/` silently winning would be a surprising, and in this
    session's own testing, actively harmful, hijack.

    Enterprise Self-Hosted is unaffected either way: that deployment
    shape always sets `MAK4I_CONTROL_PLANE_DB` explicitly (case A), so
    this function never touches its environment regardless of whether a
    `.mak4i/` happens to exist.

    Never applies `MAK4I_TOKEN` — none of the commands this serves read
    it (they authenticate via `--actor`/`--principal`, not a bearer
    credential).
    """
    if any(key in os.environ for key in _EXPLICIT_DEPLOYMENT_ENV_KEYS):
        return None
    config = load()
    if config is None:
        return None
    apply_to_env(config, token=None)
    return config


def _desired_local_env(config: LocalConfig, token: str | None) -> dict[str, str]:
    desired: dict[str, str] = {
        "MAK4I_CONTROL_PLANE_DB": config.control_plane_db,
        "MAK4I_STORE": config.store,
    }
    if config.local_store_dir is not None:
        desired["MAK4I_LOCAL_STORE_DIR"] = config.local_store_dir
    if token is not None:
        desired["MAK4I_TOKEN"] = token
    return desired
