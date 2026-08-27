"""Rate limiting with a pluggable backend.

The default backend is an in-process sliding window: correct for one worker,
approximate across several.  The :class:`RateLimitStore` protocol is the seam
where a Redis-backed store drops in for real deployments, and the shipped
in-memory store is a working reference for what that store must do.
"""

from __future__ import annotations

import threading
import time
import typing as t

from ..exceptions import TooManyRequests
from ..request import Request
from ..response import Response

__all__ = ["MemoryRateLimitStore", "RateLimitMiddleware", "RateLimitStore"]


class RateLimitStore(t.Protocol):
    """Backend contract: count a hit and report the current state."""

    def hit(self, key: str, limit: int, window: float) -> tuple[int, float]:
        """Return ``(count_in_window, seconds_until_reset)``."""
        ...


class MemoryRateLimitStore:
    """Sliding-window counter kept in this process's memory."""

    def __init__(self, max_keys: int = 100_000) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self.max_keys = max_keys

    def hit(self, key: str, limit: int, window: float) -> tuple[int, float]:
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            if len(self._hits) > self.max_keys:
                self._evict(cutoff)
            timestamps = [ts for ts in self._hits.get(key, ()) if ts > cutoff]
            timestamps.append(now)
            self._hits[key] = timestamps
            reset_in = window - (now - timestamps[0])
            return len(timestamps), max(0.0, reset_in)

    def _evict(self, cutoff: float) -> None:
        stale = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
        for key in stale:
            del self._hits[key]


class RateLimitMiddleware:
    """Reject callers that exceed ``limit`` requests per ``window`` seconds."""

    def __init__(
        self,
        limit: int = 100,
        window: float = 60.0,
        *,
        store: RateLimitStore | None = None,
        key: t.Callable[[Request], str] | None = None,
        skip: t.Callable[[Request], bool] | None = None,
        headers: bool = True,
    ) -> None:
        self.limit = limit
        self.window = window
        self.store = store or MemoryRateLimitStore()
        #: Defaults to the client IP.  Override to bucket by API key or user.
        self.key = key or (lambda request: request.ip or "anonymous")
        self.skip = skip
        self.headers = headers

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        if self.skip is not None and self.skip(request):
            return await call_next()

        count, reset_in = self.store.hit(self.key(request), self.limit, self.window)
        remaining = max(0, self.limit - count)

        if self.headers:
            response.set("X-RateLimit-Limit", str(self.limit))
            response.set("X-RateLimit-Remaining", str(remaining))
            response.set("X-RateLimit-Reset", str(int(reset_in)))

        if count > self.limit:
            raise TooManyRequests(
                retry_after=int(reset_in) + 1,
                detail=f"Rate limit of {self.limit} requests per {int(self.window)}s exceeded",
            )
        return await call_next()
