"""The sync/async bridge that lets one handler run under two protocols.

SlowAPI's central promise is that a handler you write once runs unchanged on a
WSGI server (gunicorn sync workers) and on an ASGI server (uvicorn), regardless
of whether the handler itself is ``def`` or ``async def``.  Four cases exist:

===============  ==================  ==========================================
Handler          Server              Strategy
===============  ==================  ==========================================
``def``          WSGI                Called directly.  Zero overhead.
``async def``    ASGI                Awaited directly.  Zero overhead.
``def``          ASGI                Offloaded to a worker thread so the event
                                     loop is never blocked.
``async def``    WSGI                Driven on a shared background event loop
                                     thread, one per process.
===============  ==================  ==========================================

The last case is what most frameworks refuse to do.  We do it with a single
long-lived loop thread rather than ``asyncio.run`` per request, so connection
pools, ``asyncio`` locks, and background tasks created by user code survive
across requests instead of being torn down every time.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
from collections.abc import Callable, Coroutine, Iterable
from typing import Any, TypeVar

T = TypeVar("T")

__all__ = [
    "call_maybe_async",
    "drive",
    "is_async_callable",
    "iterate_in_threadpool",
    "maybe_await",
    "run_coroutine_sync",
    "run_in_threadpool",
    "shutdown_loop_thread",
]


def drive(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion in the calling thread, with no event loop.

    This is the trick behind SlowAPI's zero-overhead WSGI path.  A coroutine
    only suspends when it awaits something that is not yet done -- a future, a
    sleep, socket I/O.  A dispatch chain in which *every* participant is an
    ordinary ``def`` never does that, so stepping it with ``send(None)`` runs it
    straight through without ever needing a loop, a thread hop, or a task.

    The application decides which requests qualify (see
    ``Route.is_fully_sync``); if a coroutine driven here does suspend, that is a
    framework bug and the ``RuntimeError`` below says so rather than deadlocking.
    """
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise RuntimeError(
        "A dispatch chain marked fully synchronous suspended on an await. "
        "Report this as a bug: slowapi.concurrency.drive() cannot service it."
    )


def is_async_callable(obj: Any) -> bool:
    """Return ``True`` if calling ``obj`` produces an awaitable.

    Handles plain coroutine functions, ``functools.partial`` wrappers, and
    class instances whose ``__call__`` is ``async def``.
    """
    while isinstance(obj, functools.partial):
        obj = obj.func
    if inspect.iscoroutinefunction(obj):
        return True
    call = getattr(obj, "__call__", None)  # noqa: B004 - intentional
    return call is not None and inspect.iscoroutinefunction(call)


async def run_in_threadpool(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking callable in a worker thread, preserving context vars.

    Falls back to calling inline when no loop is running.  Offloading exists to
    keep a blocking call from stalling the loop *and every other request on it*;
    with no loop there is nothing to stall, and the alternative is a
    ``RuntimeError`` from :func:`asyncio.to_thread` -- which is how an upload's
    temporary file used to be left open on the loop-free WSGI path.
    """
    if kwargs:
        func = functools.partial(func, **kwargs)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return func(*args)
    return await asyncio.to_thread(func, *args)


async def iterate_in_threadpool(iterable: Iterable[T]) -> Any:
    """Consume a blocking iterator from async code without stalling the loop.

    Falls back to inline iteration when no loop is running, which is the case
    on the fully-synchronous WSGI fast path.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        for item in iterable:
            yield item
        return
    iterator = iter(iterable)
    sentinel: Any = object()

    def take() -> Any:
        return next(iterator, sentinel)

    while True:
        item = await asyncio.to_thread(take)
        if item is sentinel:
            break
        yield item


class _LoopThread:
    """A process-wide daemon thread hosting one asyncio event loop.

    Created lazily the first time synchronous code needs to await something.
    Because the loop outlives individual requests, resources bound to it (async
    database pools, ``aiohttp`` sessions, ``asyncio.Lock`` objects) keep working
    across the whole process lifetime.
    """

    _instance: _LoopThread | None = None
    _guard = threading.Lock()

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="slowapi-loop", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        self.loop.run_forever()

    @classmethod
    def instance(cls) -> _LoopThread:
        if cls._instance is None:
            with cls._guard:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def submit(self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)
        self.loop.close()


def shutdown_loop_thread() -> None:
    """Stop the shared background loop, if one was ever started.

    Only useful in tests and in ``atexit`` hooks; the thread is a daemon and
    does not keep the interpreter alive on its own.
    """
    with _LoopThread._guard:
        if _LoopThread._instance is not None:
            _LoopThread._instance.close()
            _LoopThread._instance = None


def run_coroutine_sync(
    coro: Coroutine[Any, Any, T], *, loop: asyncio.AbstractEventLoop | None = None
) -> T:
    """Await ``coro`` from synchronous code and return its result.

    :param loop:
        The event loop that is *serving the current request*, when one exists.
        The ASGI adapter passes it so that a ``def`` handler running in a worker
        thread can still read the request body, which is only obtainable from
        the serving loop.  When omitted, the shared background loop is used.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is not None:
        coro.close()
        raise RuntimeError(
            "run_coroutine_sync() was called from inside a running event loop. "
            "This usually means an 'async def' function called a blocking "
            "SlowAPI API; await the async variant instead (e.g. 'await "
            "request.body()' rather than 'request.body_sync()')."
        )

    if loop is not None and not loop.is_closed():
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    return _LoopThread.instance().submit(coro)


async def maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable, otherwise return it untouched."""
    if inspect.isawaitable(value):
        return await value
    return value


async def call_maybe_async(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``func`` from async code whether it is sync or async.

    Sync callables are pushed to a worker thread so a slow handler cannot stall
    the event loop and every other in-flight request with it.
    """
    if is_async_callable(func):
        return await func(*args, **kwargs)
    return await run_in_threadpool(func, *args, **kwargs)


def call_maybe_sync(
    func: Callable[..., Any],
    *args: Any,
    loop: asyncio.AbstractEventLoop | None = None,
    **kwargs: Any,
) -> Any:
    """Call ``func`` from sync code whether it is sync or async."""
    if is_async_callable(func):
        return run_coroutine_sync(func(*args, **kwargs), loop=loop)
    return func(*args, **kwargs)
