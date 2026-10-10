from __future__ import annotations

import threading
import time
from collections import deque


class RateLimiter:
    """Fixed-window-per-key limiter for the sign-in, token, revocation and
    registration endpoints (MAK-0008 §11.1).

    In-memory and per process: correct for the single-replica deployments
    this Reference supports (documented). A multi-replica deployment would
    need a shared limiter in front of the service."""

    def __init__(self, limit_per_minute: int, *, clock=time.monotonic):
        self._limit = limit_per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            if len(self._hits) > 10000:  # bound memory under abuse
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True
