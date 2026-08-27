"""Interceptors: wrap a handler call without touching the handler.

An interceptor sees the request on the way in and the *return value* on the way
out, which is where it differs from middleware: middleware works with bytes and
headers, an interceptor works with the Python object the handler produced.
That makes it the right place for response envelopes, caching, timing, and
audit trails.

::

    class Envelope(Interceptor):
        async def intercept(self, ctx, call_next):
            data = await call_next()
            return {"data": data, "requestId": ctx.request.request_id}
"""

from __future__ import annotations

import inspect
import time
import typing as t

from .concurrency import maybe_await
from .decorators import never_suspends
from .execution import ExecutionContext

__all__ = [
    "CacheInterceptor",
    "EnvelopeInterceptor",
    "Interceptor",
    "TimingInterceptor",
    "run_interceptors",
    "use_interceptors",
]

CallNext = t.Callable[[], t.Awaitable[t.Any]]


class Interceptor:
    """Base class for interceptors.  Override :meth:`intercept`."""

    async def intercept(self, ctx: ExecutionContext, call_next: CallNext) -> t.Any:
        return await call_next()


def use_interceptors(*interceptors: t.Any) -> t.Callable[[t.Any], t.Any]:
    """Attach interceptors to a handler or controller class."""

    def decorator(target: t.Any) -> t.Any:
        existing = tuple(getattr(target, "__slowapi_interceptors__", ()))
        target.__slowapi_interceptors__ = existing + interceptors
        return target

    return decorator


def collect_interceptors(handler: t.Any, controller: type | None) -> tuple[t.Any, ...]:
    class_level = tuple(getattr(controller, "__slowapi_interceptors__", ())) if controller else ()
    return class_level + tuple(getattr(handler, "__slowapi_interceptors__", ()))


async def run_interceptors(
    interceptors: t.Sequence[t.Any], ctx: ExecutionContext, terminal: CallNext
) -> t.Any:
    """Compose interceptors into an onion around ``terminal`` and run it."""
    if not interceptors:
        return await terminal()

    async def build(index: int) -> t.Any:
        if index == len(interceptors):
            return await terminal()
        item = interceptors[index]
        instance = await ctx.container.resolve(item) if inspect.isclass(item) else item
        hook = getattr(instance, "intercept", instance)
        return await maybe_await(hook(ctx, lambda: build(index + 1)))

    return await build(0)


@never_suspends
class TimingInterceptor(Interceptor):
    """Record handler duration on the response and in ``request.state``.

    Marked ``@never_suspends`` -- as are the other built-ins here -- because it
    awaits nothing but ``call_next()``.  Without the marker, adding any of them
    to a synchronous route would quietly move it onto the event loop, which is
    a strange price to pay for a timing header."""

    def __init__(self, header: str = "X-Handler-Time") -> None:
        self.header = header

    async def intercept(self, ctx: ExecutionContext, call_next: CallNext) -> t.Any:
        started = time.perf_counter()
        try:
            return await call_next()
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            ctx.request.state.handler_ms = elapsed_ms
            ctx.response.set(self.header, f"{elapsed_ms:.2f}ms")


@never_suspends
class EnvelopeInterceptor(Interceptor):
    """Wrap successful payloads in a consistent envelope.

    Responses that are already :class:`~slowapi.response.Response` objects are
    passed through untouched, so file downloads and streams are unaffected.
    """

    def __init__(self, key: str = "data", include_meta: bool = True) -> None:
        self.key = key
        self.include_meta = include_meta

    async def intercept(self, ctx: ExecutionContext, call_next: CallNext) -> t.Any:
        from .response import Response

        result = await call_next()
        if isinstance(result, Response) or result is None:
            return result
        payload: dict[str, t.Any] = {self.key: result}
        if self.include_meta:
            payload["meta"] = {
                "requestId": ctx.request.request_id,
                "path": ctx.request.path,
            }
        return payload


@never_suspends
class CacheInterceptor(Interceptor):
    """A minimal in-process response cache keyed by method, path and query.

    Intended for read-heavy endpoints in single-process deployments and as a
    worked example; swap in Redis for anything multi-process.
    """

    def __init__(self, ttl: float = 5.0, max_entries: int = 512) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, t.Any]] = {}

    async def intercept(self, ctx: ExecutionContext, call_next: CallNext) -> t.Any:
        if ctx.request.method not in ("GET", "HEAD"):
            return await call_next()
        key = f"{ctx.request.method} {ctx.request.path}?{ctx.request.url.query}"
        now = time.monotonic()
        hit = self._store.get(key)
        if hit is not None and now - hit[0] < self.ttl:
            ctx.response.set("X-Cache", "HIT")
            return hit[1]
        result = await call_next()
        if len(self._store) >= self.max_entries:
            oldest = min(self._store, key=lambda k: self._store[k][0])
            del self._store[oldest]
        self._store[key] = (now, result)
        ctx.response.set("X-Cache", "MISS")
        return result
