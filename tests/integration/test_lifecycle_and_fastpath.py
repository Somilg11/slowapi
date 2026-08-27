"""Startup validation, shutdown parity, and who is allowed to keep the fast path.

These are the properties that separate "works on my laptop" from "safe to
deploy": a broken route should stop a process from starting rather than fail
the first request, and a WSGI worker should dispose what it opened.
"""

from __future__ import annotations

import pytest

from slowapi import ConfigurationError, Depends, Response, SlowAPI, never_suspends
from slowapi.adapters.wsgi import WSGIAdapter
from slowapi.concurrency import _LoopThread, shutdown_loop_thread
from slowapi.testing import TestClient


def _missing_dependency() -> None:  # pragma: no cover - never called
    raise AssertionError("should never run")


class TestStartupValidation:
    def test_a_broken_route_stops_startup_rather_than_a_request(self, make_client):
        app = SlowAPI()

        @app.get("/broken")
        def broken(res: Response, thing: NotAType = Depends()):  # noqa: F821
            return res.json({})

        with pytest.raises(ConfigurationError) as info:
            make_client(app).get("/broken")

        assert "GET /broken" in str(info.value)

    def test_every_broken_route_is_reported_not_just_the_first(self):
        app = SlowAPI()

        @app.get("/a")
        def a(res: Response, x: Nope = Depends()):  # noqa: F821
            return res.json({})

        @app.get("/b")
        def b(res: Response, y: AlsoNope = Depends()):  # noqa: F821
            return res.json({})

        with pytest.raises(ConfigurationError) as info:
            app.check()

        message = str(info.value)
        assert "2 route(s) failed validation" in message
        assert "GET /a" in message and "GET /b" in message

    def test_a_healthy_app_passes_and_warms_its_plans(self):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.json({"ok": True})

        assert app.check() is app
        # check() analysed every route, so the plans are already cached.
        assert app._prepared

    def test_check_is_reachable_before_any_request(self):
        """The point of the CLI hook: fail a build, not a deploy."""
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        app.check()
        assert not app._started  # checking is not starting


class TestShutdownParity:
    def test_wsgi_disposes_what_it_opened(self):
        """WSGI has no lifespan message, so the adapter registers an atexit hook."""
        events: list[str] = []
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        @app.on_event("shutdown")
        def bye() -> None:
            events.append("shutdown")

        adapter = WSGIAdapter(app)
        client = TestClient(app, protocol="wsgi")
        client.get("/x")

        assert app._atexit_registered is True
        # Simulate the interpreter exiting under gunicorn.
        adapter._run_shutdown()
        assert events == ["shutdown"]

    def test_the_shutdown_hook_never_raises_at_interpreter_exit(self):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        @app.on_event("shutdown")
        def bye() -> None:
            raise RuntimeError("teardown exploded")

        adapter = WSGIAdapter(app)
        TestClient(app, protocol="wsgi").get("/x")
        # atexit prints and moves on; raising here would mask the real exit code.
        adapter._run_shutdown()

    def test_response_background_runs_on_both_protocols(self, make_client):
        """The regression this file was written for: WSGI used to drop it."""
        ran: list[str] = []
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            res.background = lambda: ran.append("ran")
            return res.json({"ok": True})

        make_client(app).get("/x")
        assert ran == ["ran"]

    def test_background_runs_for_streaming_responses_too(self, make_client):
        ran: list[str] = []
        app = SlowAPI()

        @app.get("/stream")
        def handler(res: Response):
            res.background = lambda: ran.append("ran")
            return res.stream(iter([b"a", b"b"]))

        assert make_client(app).get("/stream").text == "ab"
        assert ran == ["ran"]


class TestNeverSuspends:
    def test_async_middleware_costs_the_fast_path_by_default(self):
        """The safe default: an await might suspend, so assume it will."""
        shutdown_loop_thread()
        app = SlowAPI()

        async def passthrough(req, res, call_next):
            return await call_next()

        app.use(passthrough)

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        TestClient(app, protocol="wsgi").get("/x")
        assert _LoopThread._instance is not None

    def test_the_marker_buys_the_fast_path_back(self):
        shutdown_loop_thread()
        app = SlowAPI()

        @never_suspends
        class Stamp:
            async def dispatch(self, req, res, call_next):
                result = await call_next()
                res.set("x-stamped", "1")
                return result

        app.use(Stamp())

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        response = TestClient(app, protocol="wsgi").get("/x")
        assert response.headers["x-stamped"] == "1"
        assert _LoopThread._instance is None

    def test_breaking_the_promise_names_itself_rather_than_hanging(self):
        shutdown_loop_thread()
        app = SlowAPI()

        @never_suspends
        class Liar:
            async def dispatch(self, req, res, call_next):
                import asyncio

                await asyncio.sleep(0)  # an actual suspension: the promise is false
                return await call_next()

        app.use(Liar())

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        with pytest.raises(RuntimeError, match="fully synchronous"):
            TestClient(app, protocol="wsgi").get("/x")
