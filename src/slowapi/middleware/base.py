"""Middleware plumbing: the onion, and the executor that drives it.

Middleware here is Express-shaped -- ``(req, res, next)`` -- because that model
is easy to reason about and lets a single function both pre-process and
post-process.  Four spellings are accepted and normalised into one::

    def logger(req, res, next):            # sync, full control
        next()
        print(res.status_code)

    async def logger(req, res, next):      # async, full control
        await next()

    def stamp(req, res):                   # pre-only, continues automatically
        req.state.at = time.time()

    class Timing:                          # class-based, DI-constructed
        async def dispatch(self, req, res, next): ...
"""

from __future__ import annotations

import asyncio
import inspect
import typing as t

from ..concurrency import is_async_callable, maybe_await, run_in_threadpool
from ..exceptions import ConfigurationError

if t.TYPE_CHECKING:  # pragma: no cover
    from ..request import Request
    from ..response import Response

__all__ = ["AsyncExecutor", "Executor", "Middleware", "SyncExecutor", "adapt", "build_chain"]

CallNext = t.Callable[[], t.Any]
Terminal = t.Callable[[], t.Awaitable[t.Any]]


class Middleware:
    """Optional base class.  Any callable with the right arity also works."""

    async def dispatch(self, request: Request, response: Response, call_next: CallNext) -> t.Any:
        return await call_next()


class Executor:
    """Decides how a user callable is invoked for the current request.

    Two implementations exist so that the fully-synchronous fast path never
    touches an event loop while the async path never blocks one.
    """

    #: True when awaiting inside this executor may actually suspend.
    can_suspend = True

    async def call(self, fn: t.Callable[..., t.Any], *args: t.Any, **kwargs: t.Any) -> t.Any:
        raise NotImplementedError


class SyncExecutor(Executor):
    """Calls everything inline.  Used on WSGI when no participant is async."""

    can_suspend = False

    async def call(self, fn: t.Callable[..., t.Any], *args: t.Any, **kwargs: t.Any) -> t.Any:
        if is_async_callable(fn):
            raise ConfigurationError(
                f"{getattr(fn, '__qualname__', fn)!r} is async but the route was "
                "classified as fully synchronous. This is a framework bug."
            )
        return fn(*args, **kwargs)


class AsyncExecutor(Executor):
    """Awaits async callables and offloads sync ones to a worker thread."""

    can_suspend = True

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None, offload: bool = True) -> None:
        self.loop = loop
        #: When False, sync callables run inline on the loop.  Correct only for
        #: callables known to be non-blocking.
        self.offload = offload

    async def call(self, fn: t.Callable[..., t.Any], *args: t.Any, **kwargs: t.Any) -> t.Any:
        if is_async_callable(fn):
            return await fn(*args, **kwargs)
        if self.offload:
            return await run_in_threadpool(fn, *args, **kwargs)
        return fn(*args, **kwargs)


def _arity(fn: t.Callable[..., t.Any]) -> int:
    """Count the positional parameters a middleware accepts."""
    target = fn
    if inspect.isclass(fn):
        raise ConfigurationError(
            f"Middleware {fn.__name__!r} was passed as a class. Instantiate it "
            f"({fn.__name__}()) or register it as a provider first."
        )
    if not inspect.isfunction(target) and not inspect.ismethod(target):
        for attribute in ("dispatch", "__call__"):
            candidate = getattr(target, attribute, None)
            if candidate is not None:
                target = candidate
                break
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Cannot inspect middleware {fn!r}: {exc}") from exc
    return sum(
        1
        for p in signature.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.name not in ("self", "cls")
    )


def middleware_target(fn: t.Any) -> t.Callable[..., t.Any]:
    """Return the callable that actually implements the middleware."""
    if inspect.isfunction(fn) or inspect.ismethod(fn):
        return fn
    return getattr(fn, "dispatch", None) or fn


def is_async_middleware(fn: t.Any) -> bool:
    return is_async_callable(middleware_target(fn))


def adapt(
    fn: t.Any, executor: Executor, loop: asyncio.AbstractEventLoop | None = None
) -> t.Callable[[Request, Response, Terminal], t.Awaitable[t.Any]]:
    """Normalise any accepted middleware spelling into one async signature."""
    target = middleware_target(fn)
    arity = _arity(fn)

    if arity == 2:

        async def pre_only(request: Request, response: Response, call_next: Terminal) -> t.Any:
            await executor.call(target, request, response)
            return await call_next()

        return pre_only

    if arity != 3:
        raise ConfigurationError(
            f"Middleware {getattr(fn, '__qualname__', fn)!r} must take (req, res) or "
            f"(req, res, next); it takes {arity} positional parameter(s)."
        )

    if is_async_callable(target):

        async def async_mw(request: Request, response: Response, call_next: Terminal) -> t.Any:
            return await target(request, response, call_next)

        return async_mw

    if not executor.can_suspend:
        # Fully synchronous chain: `next` is just a function call.
        async def sync_mw(request: Request, response: Response, call_next: Terminal) -> t.Any:
            from ..concurrency import drive

            holder: dict[str, t.Any] = {}

            def call_next_sync() -> t.Any:
                holder["result"] = drive(t.cast("t.Coroutine[t.Any, t.Any, t.Any]", call_next()))
                return holder["result"]

            outcome = target(request, response, call_next_sync)
            return outcome if outcome is not None else holder.get("result")

        return sync_mw

    async def bridged_mw(request: Request, response: Response, call_next: Terminal) -> t.Any:
        # A blocking middleware runs in a worker thread; its `next()` hops back
        # onto the serving loop so the rest of the chain stays async.
        running = loop or asyncio.get_running_loop()
        holder: dict[str, t.Any] = {}

        def call_next_sync() -> t.Any:
            future = asyncio.run_coroutine_threadsafe(_capture(call_next(), holder), running)
            return future.result()

        outcome = await run_in_threadpool(target, request, response, call_next_sync)
        return outcome if outcome is not None else holder.get("result")

    return bridged_mw


async def _capture(awaitable: t.Awaitable[t.Any], holder: dict[str, t.Any]) -> t.Any:
    holder["result"] = await awaitable
    return holder["result"]


def build_chain(
    middlewares: t.Sequence[t.Any],
    terminal: Terminal,
    request: Request,
    response: Response,
    executor: Executor,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Terminal:
    """Fold ``middlewares`` around ``terminal``, outermost first."""
    handler = terminal
    for entry in reversed(list(middlewares)):
        adapted = adapt(entry, executor, loop)
        handler = _bind(adapted, request, response, handler)
    return handler


def _bind(
    adapted: t.Callable[..., t.Awaitable[t.Any]],
    request: Request,
    response: Response,
    nxt: Terminal,
) -> Terminal:
    async def run() -> t.Any:
        return await maybe_await(adapted(request, response, nxt))

    return run
