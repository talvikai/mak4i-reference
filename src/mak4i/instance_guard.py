"""One MAK4I server process per installation (DEPLOYMENT.md → Replicas).

A server's start-up finishes work that a *previous* process left pending
(`reconcile_change_feed`, `recover_pending_resolutions`), so it must know
no other server is still running against the same data. The server takes
an exclusive OS lock on a file in its artifact store before it builds the
control plane or the engine, and keeps it until the process exits. A
second server against the same store fails at once, before it reads or
changes anything; one AI client or many can still share the one server.

The lock is an OS file lock (`mak4i._filelock`), so it is released when
the process exits for any reason, including a crash or a kill: there is
nothing stale to clean up, and the next start recovers normally.

Only a local artifact store (`MAK4I_STORE=local`, the default) can be
guarded this way. With `MAK4I_STORE=gcs` there is no shared local
directory, and running one instance is the deployment's responsibility.
CLI commands don't take the lock: they may run alongside the one server.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from mak4i._filelock import FileLock

LOCK_FILE_NAME = ".mak4i-server.lock"
DEFAULT_LOCAL_STORE_DIR = "artifacts/local"


class AlreadyRunningError(RuntimeError):
    """Another server process holds this installation's lock."""

    def __init__(self, lock_path: Path) -> None:
        super().__init__(str(lock_path))
        self.lock_path = lock_path


_held: FileLock | None = None


def lock_path_from_env(env: Mapping[str, str] = os.environ) -> Path | None:
    """The lock file for the installation the environment describes, or
    None when its artifact store isn't local."""
    if env.get("MAK4I_STORE", "local") != "local":
        return None
    return Path(env.get("MAK4I_LOCAL_STORE_DIR", DEFAULT_LOCAL_STORE_DIR)) / LOCK_FILE_NAME


def acquire(lock_path: Path) -> None:
    """Hold the installation lock for the rest of this process, or raise
    AlreadyRunningError if another process holds it. Taking the lock this
    process already holds is a no-op."""
    global _held
    if _held is not None:
        if _held.path == lock_path:
            return
        release()  # one installation per process; only tests switch
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(lock_path)
    if not lock.acquire(blocking=False):
        raise AlreadyRunningError(lock_path)
    _held = lock


def release() -> None:
    """Give the lock up (tests; a process exiting releases it anyway)."""
    global _held
    if _held is not None:
        _held.release()
        _held = None
