"""Cross-platform exclusive file locks: `fcntl.flock` on macOS and Linux,
`msvcrt.locking` on Windows.

Both are operating-system locks between processes: a lock is held only
while the process holding it keeps its file descriptor open, so the OS
releases it when the process exits, including after a crash or a kill.
Nothing is left behind to clean up (unlike a PID file or a lock that
merely exists as a file). Neither works reliably on a network filesystem.

One `FileLock` object holds one lock at a time; within a process, two
`FileLock` objects on the same path exclude each other as two processes
would (each opens its own descriptor).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # byte 0; locking past EOF is allowed
        except OSError:
            return False
        return True

    def _lock_blocking(fd: int) -> None:
        # msvcrt has no indefinitely blocking mode (LK_LOCK gives up after
        # about 10 seconds), so poll the non-blocking one.
        while not _try_lock(fd):
            time.sleep(0.01)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _lock_blocking(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class FileLock:
    """An exclusive lock on `path` (created if missing, never deleted)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, *, blocking: bool = True) -> bool:
        """Take the lock. With `blocking=False`, return False at once if
        another holder has it."""
        if self._fd is not None:
            raise RuntimeError(f"lock already held: {self.path}")
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if blocking:
                _lock_blocking(fd)
            elif not _try_lock(fd):
                os.close(fd)
                return False
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
