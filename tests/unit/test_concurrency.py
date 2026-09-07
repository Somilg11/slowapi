"""The sync/async bridge.

These tests protect the invariant the whole dual-protocol design rests on: a
chain of ordinary functions can be driven to completion without an event loop.
"""

from __future__ import annotations

import asyncio

import pytest

from slowfw.concurrency import (
    drive,
    is_async_callable,
    run_coroutine_sync,
    shutdown_loop_thread,
)


class TestIsAsyncCallable:
    def test_detects_coroutine_functions(self):
        async def f():
            pass

        assert is_async_callable(f)

    def test_detects_async_dunder_call(self):
        class C:
            async def __call__(self):
                pass

        assert is_async_callable(C())

    def test_sees_through_partials(self):
        import functools

        async def f(a):
            return a

        assert is_async_callable(functools.partial(f, 1))

    def test_plain_functions_are_not_async(self):
        assert not is_async_callable(len)


class TestDrive:
    def test_runs_a_never_suspending_coroutine_inline(self):
        async def inner():
            return 21

        async def outer():
            return await inner() * 2

        assert drive(outer()) == 42

    def test_no_event_loop_is_created(self):
        async def coro():
            return "done"

        assert drive(coro()) == "done"
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()

    def test_a_suspension_is_reported_rather_than_deadlocking(self):
        async def suspends():
            await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="fully synchronous"):
            drive(suspends())

    def test_exceptions_propagate_unchanged(self):
        async def boom():
            raise KeyError("nope")

        with pytest.raises(KeyError):
            drive(boom())


class TestRunCoroutineSync:
    def test_runs_genuinely_suspending_work(self):
        async def coro():
            await asyncio.sleep(0.001)
            return "ok"

        try:
            assert run_coroutine_sync(coro()) == "ok"
        finally:
            shutdown_loop_thread()

    def test_the_loop_thread_is_shared_across_calls(self):
        async def loop_id():
            return id(asyncio.get_running_loop())

        try:
            assert run_coroutine_sync(loop_id()) == run_coroutine_sync(loop_id())
        finally:
            shutdown_loop_thread()

    def test_calling_from_inside_a_loop_is_a_clear_error(self):
        async def outer():
            async def inner():
                return 1

            run_coroutine_sync(inner())

        with pytest.raises(RuntimeError, match="running event loop"):
            asyncio.run(outer())
