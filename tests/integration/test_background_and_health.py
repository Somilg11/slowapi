"""Background tasks, health probes, and deadlines -- on both protocols.

The interesting assertions here are the parity ones.  Background work used to
run on ASGI and silently not run on WSGI, which is exactly the class of bug the
dual-protocol fixture exists to catch.
"""

from __future__ import annotations

import time

import pytest

from slowfw import BackgroundTasks, HealthCheck, Response, SlowAPI
from slowfw.concurrency import _LoopThread, shutdown_loop_thread
from slowfw.middleware import TimeoutMiddleware
from slowfw.testing import TestClient


class TestBackgroundTasks:
    def test_tasks_run_after_the_response_on_both_protocols(self, make_client):
        done: list[str] = []
        app = SlowAPI()

        @app.post("/signup")
        def signup(tasks: BackgroundTasks, res: Response, name: str = "anon"):
            tasks.add(done.append, f"welcome {name}")
            return res.status(201).json({"queued": len(tasks)})

        client = make_client(app)
        response = client.post("/signup", json={"name": "ada"})

        assert response.status_code == 201
        assert response.json() == {"queued": 1}
        assert done == ["welcome ada"]

    def test_a_failing_task_does_not_cancel_the_ones_behind_it(self, make_client):
        done: list[str] = []
        app = SlowAPI()

        def explode() -> None:
            raise RuntimeError("nope")

        @app.get("/x")
        def handler(tasks: BackgroundTasks, res: Response):
            tasks.add(done.append, "first")
            tasks.add(explode)
            tasks.add(done.append, "third")
            return res.json({"ok": True})

        assert make_client(app).get("/x").status_code == 200
        # The response is already sent; there is nobody left to report to, so
        # the queue continues and the failure is logged instead.
        assert done == ["first", "third"]

    def test_an_async_task_runs_from_a_sync_handler(self, make_client):
        done: list[str] = []
        app = SlowAPI()

        async def record() -> None:
            done.append("async ran")

        @app.get("/x")
        def handler(tasks: BackgroundTasks, res: Response):
            tasks.add(record)
            return res.json({"ok": True})

        make_client(app).get("/x")
        assert done == ["async ran"]

    def test_a_dependency_can_queue_work_the_handler_never_sees(self, make_client):
        done: list[str] = []
        app = SlowAPI()

        def audit(tasks: BackgroundTasks) -> str:
            tasks.add(done.append, "audited")
            return "ok"

        from slowfw import Depends

        @app.get("/x")
        def handler(res: Response, marker: str = Depends(audit)):
            return res.json({"marker": marker})

        assert make_client(app).get("/x").json() == {"marker": "ok"}
        assert done == ["audited"]

    def test_an_explicit_response_background_still_wins(self, make_client):
        done: list[str] = []
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response):
            res.background = lambda: done.append("explicit")
            return res.json({"ok": True})

        make_client(app).get("/x")
        assert done == ["explicit"]

    def test_sync_tasks_do_not_drag_a_wsgi_request_onto_the_loop(self):
        """The whole point of the fast path is that nothing quietly opts out."""
        shutdown_loop_thread()
        done: list[str] = []
        app = SlowAPI()

        @app.get("/x")
        def handler(tasks: BackgroundTasks, res: Response):
            tasks.add(done.append, "ran")
            return res.json({"ok": True})

        client = TestClient(app, protocol="wsgi")
        assert client.get("/x").status_code == 200
        assert done == ["ran"]
        assert _LoopThread._instance is None

    def test_add_rejects_a_non_callable_at_the_call_site(self):
        with pytest.raises(TypeError, match="needs a callable"):
            BackgroundTasks().add("not a function")  # type: ignore[arg-type]

    def test_empty_queue_is_falsy(self):
        tasks = BackgroundTasks()
        assert not tasks
        tasks.add_task(lambda: None)
        assert tasks and len(tasks) == 1


class TestHealthChecks:
    def test_liveness_never_consults_a_dependency(self, make_client):
        app = SlowAPI()
        probed: list[str] = []
        health = HealthCheck()
        health.add("database", lambda: probed.append("db"))
        health.install(app)

        response = make_client(app).get("/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert probed == []

    def test_readiness_reports_each_check(self, make_client):
        app = SlowAPI()
        health = HealthCheck()
        health.add("database", lambda: True)
        health.install(app)

        body = make_client(app).get("/readyz").json()

        assert body["status"] == "ok"
        assert body["checks"]["database"]["status"] == "ok"
        assert "took_ms" in body["checks"]["database"]

    def test_a_failing_critical_check_returns_503(self, make_client):
        app = SlowAPI()
        health = HealthCheck()
        health.add("database", lambda: (_ for _ in ()).throw(ConnectionError("refused")))
        health.install(app)

        response = make_client(app).get("/readyz")

        assert response.status_code == 503
        # The class name only: probe exceptions carry connection strings.
        assert response.json()["checks"]["database"]["error"] == "ConnectionError"
        assert "refused" not in response.text

    def test_a_failing_non_critical_check_is_reported_but_stays_ready(self, make_client):
        app = SlowAPI()
        health = HealthCheck()
        health.add("cache", lambda: False, critical=False)
        health.install(app)

        response = make_client(app).get("/readyz")

        assert response.status_code == 200
        assert response.json()["checks"]["cache"]["status"] == "fail"

    def test_tolerate_absorbs_a_blip_before_failing(self, make_client):
        app = SlowAPI()
        outcomes = iter([False, False])
        health = HealthCheck()
        health.add("flaky", lambda: next(outcomes, False), tolerate=1)
        health.install(app)

        client = make_client(app)
        assert client.get("/readyz").status_code == 200  # first strike tolerated
        assert client.get("/readyz").status_code == 503  # second is not

    def test_details_can_be_withheld(self, make_client):
        app = SlowAPI()
        health = HealthCheck(include_details=False)
        health.add("database", lambda: True)
        health.install(app)

        assert make_client(app).get("/readyz").json() == {"status": "ok"}

    def test_probes_stay_out_of_the_published_schema(self, make_client):
        app = SlowAPI()
        HealthCheck().install(app)
        assert "/healthz" not in app.openapi()["paths"]

    def test_duplicate_names_are_rejected_at_registration(self):
        health = HealthCheck().add("db", lambda: True)
        with pytest.raises(ValueError, match="already registered"):
            health.add("db", lambda: True)

    def test_the_decorator_form_registers_the_probe(self, make_client):
        app = SlowAPI()
        health = HealthCheck()

        @health.check("database")
        def probe() -> bool:
            return True

        health.install(app)
        assert make_client(app).get("/readyz").json()["checks"]["database"]["status"] == "ok"

    def test_async_probes_run(self):
        from slowfw.concurrency import run_coroutine_sync

        health = HealthCheck()

        async def probe() -> bool:
            return True

        health.add("database", probe)
        ready, results = run_coroutine_sync(health.run())
        assert ready and results["database"]["status"] == "ok"


class TestTimeouts:
    def test_a_slow_handler_becomes_504_on_both_protocols(self, make_client):
        app = SlowAPI()
        app.use(TimeoutMiddleware(seconds=0.05))

        @app.get("/slow")
        def slow(res: Response):
            time.sleep(0.2)
            return res.json({"never": "sent"})

        response = make_client(app).get("/slow")

        assert response.status_code == 504
        assert "never" not in response.text

    def test_a_fast_handler_is_untouched(self, make_client):
        app = SlowAPI()
        app.use(TimeoutMiddleware(seconds=5))

        @app.get("/fast")
        def fast(res: Response):
            return res.json({"ok": True})

        assert make_client(app).get("/fast").json() == {"ok": True}

    def test_per_path_none_exempts_a_deliberately_long_route(self, make_client):
        app = SlowAPI()
        app.use(TimeoutMiddleware(seconds=0.05, per_path={"/export": None}))

        @app.get("/export")
        def export(res: Response):
            time.sleep(0.15)
            return res.json({"rows": 3})

        assert make_client(app).get("/export").json() == {"rows": 3}

    def test_an_async_handler_is_cancelled_rather_than_awaited(self, make_client):
        import asyncio

        app = SlowAPI()
        app.use(TimeoutMiddleware(seconds=0.05))
        finished: list[str] = []

        @app.get("/slow")
        async def slow(res: Response):
            await asyncio.sleep(0.3)
            finished.append("completed")
            return res.json({"never": "sent"})

        assert make_client(app).get("/slow").status_code == 504
        assert finished == []

    def test_the_deadline_does_not_cost_the_loop_free_fast_path(self):
        """A deadline that forces every request onto a loop is worse than none."""
        shutdown_loop_thread()
        app = SlowAPI()
        app.use(TimeoutMiddleware(seconds=5))

        @app.get("/x")
        def handler(res: Response):
            return res.json({"ok": True})

        assert TestClient(app, protocol="wsgi").get("/x").status_code == 200
        assert _LoopThread._instance is None

    def test_a_non_positive_deadline_is_rejected_at_construction(self):
        with pytest.raises(ValueError, match="must be positive"):
            TimeoutMiddleware(seconds=0)
