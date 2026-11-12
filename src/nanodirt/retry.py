"""When to try a failed download again: network errors back off, malformed data never retries."""

from __future__ import annotations

import http.client
import time
import urllib.error
from collections.abc import Callable, Hashable

RETRY_FIRST = 5.0  # seconds before the first retry; doubles per failure
RETRY_MAX = 300.0
ERRORS = (OSError, ValueError, http.client.HTTPException)  # what a download and decode raise


def transient(exc: BaseException) -> bool:
    """Whether *exc* may not recur: a network or disk error, or HTTP 408, 425, 429 or 5xx."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 425, 429) or exc.code >= 500
    return isinstance(exc, (OSError, http.client.HTTPException))


class Backoff:
    """Per key, when a transient failure may be retried. Not thread-safe."""

    def __init__(
        self,
        first: float = RETRY_FIRST,
        cap: float = RETRY_MAX,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.first, self.cap, self.clock = first, cap, clock
        self._next: dict[Hashable, tuple[float, float]] = {}  # key: (retry at, last delay)

    def failed(self, key: Hashable) -> float:
        """Record a failure of *key*; returns the delay before it may be retried."""
        last = self._next.get(key)
        delay = self.first if last is None else min(last[1] * 2, self.cap)
        self._next[key] = (self.clock() + delay, delay)
        return delay

    def waiting(self, key: Hashable) -> bool:
        entry = self._next.get(key)
        return entry is not None and self.clock() < entry[0]

    def succeeded(self, key: Hashable) -> None:
        self._next.pop(key, None)
