"""The ASGI lifespan protocol, and what the access log records.

Lifespan is what a server uses to decide whether the app came up at all, so a
startup failure has to be reported as a failure rather than swallowed into a
process that accepts traffic it cannot serve.
"""

from __future__ import annotations

import logging

import pytest

from slowfw import Response, SlowAPI
from slowfw.adapters.asgi import ASGIAdapter
from slowfw.concurrency import run_coroutine_sync


def _drive_lifespan(app: SlowAPI, messages: list[dict]) -> list[dict]:
    """Play a lifespan conversation against the adapter and collect the replies."""
    inbox = list(messages)
    sent: list[dict] = []

    async def receive() -> dict:
        return inbox.pop(0) if inbox else {"type": "lifespan.shutdown"}

    async def send(message: dict) -> None:
        sent.append(message)

    run_coroutine_sync(ASGIAdapter(app)({"type": "lifespan"}, receive, send))
    return sent


class TestLifespan:
    def test_startup_and_shutdown_are_acknowledged(self):
        events: list[str] = []
        app = SlowAPI()
        app.on_event("startup")(lambda: events.append("up"))
        app.on_event("shutdown")(lambda: events.append("down"))

        sent = _drive_lifespan(app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

        assert [m["type"] for m in sent] == [
            "lifespan.startup.complete",
            "lifespan.shutdown.complete",
        ]
        assert events == ["up", "down"]

    def test_a_failing_startup_is_reported_not_swallowed(self):
        """A server told "complete" will send traffic to a broken process."""
        app = SlowAPI()

        @app.on_event("startup")
        def boom() -> None:
            raise RuntimeError("database unreachable")

        sent = _drive_lifespan(app, [{"type": "lifespan.startup"}])

        assert sent[0]["type"] == "lifespan.startup.failed"
        assert "database unreachable" in sent[0]["message"]

    def test_a_failing_shutdown_is_reported(self):
        app = SlowAPI()

        @app.on_event("shutdown")
        def boom() -> None:
            raise RuntimeError("could not drain")

        sent = _drive_lifespan(app, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

        assert sent[-1]["type"] == "lifespan.shutdown.failed"

    def test_a_broken_route_fails_startup_rather_than_the_first_request(self):
        from slowfw import Depends

        app = SlowAPI()

        @app.get("/broken")
        def broken(res: Response, thing: "Missing" = Depends()):  # noqa: F821, UP037
            return res.json({})

        sent = _drive_lifespan(app, [{"type": "lifespan.startup"}])
        assert sent[0]["type"] == "lifespan.startup.failed"


class TestWebSocketScope:
    def test_a_websocket_connection_is_closed_cleanly_not_hung(self):
        """WebSockets are on the roadmap; until then, refuse rather than hang."""
        sent: list[dict] = []

        async def receive() -> dict:
            return {"type": "websocket.connect"}

        async def send(message: dict) -> None:
            sent.append(message)

        run_coroutine_sync(
            ASGIAdapter(SlowAPI())({"type": "websocket", "path": "/ws"}, receive, send)
        )

        assert sent == [{"type": "websocket.close", "code": 1011}]


class _Capture(logging.Handler):
    """Collects records straight off the logger.

    ``configure_logging`` sets ``propagate = False`` so that application logs do
    not appear twice under a host that configures the root logger; that also
    means ``caplog``, which listens at the root, sees nothing.  Attaching here
    is the honest way to observe what the framework actually emits.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def access_records():
    from slowfw.logging import get_logger

    logger = get_logger("slowfw.access")
    handler = _Capture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


class TestAccessLogging:
    def _app(self, **kwargs):
        from slowfw.middleware import AccessLogMiddleware

        app = SlowAPI()
        app.use(AccessLogMiddleware(**kwargs))
        return app

    def test_a_successful_request_logs_at_info(self, make_client, access_records):
        app = self._app()

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        make_client(app).get("/x")

        record = access_records[-1]
        assert record.levelno == logging.INFO
        assert record.status == 200
        assert record.method == "GET"
        assert record.path == "/x"
        assert isinstance(record.durationMs, float)

    def test_a_client_error_logs_the_real_status_not_500(self, make_client, access_records):
        """The error middleware sits outside this one, so the exception is still
        in flight when the log line is written."""
        app = self._app()
        make_client(app).get("/nope")

        assert access_records[-1].status == 404
        assert access_records[-1].levelno == logging.WARNING

    def test_a_server_error_logs_at_error(self, make_client, access_records):
        app = self._app()

        @app.get("/boom")
        def handler(res: Response):
            raise ValueError("nope")

        make_client(app).get("/boom")

        assert access_records[-1].status == 500
        assert access_records[-1].levelno == logging.ERROR

    def test_a_slow_request_is_promoted_to_warning(self, make_client, access_records):
        app = self._app(slow_ms=0.0)

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        make_client(app).get("/x")
        assert access_records[-1].levelno == logging.WARNING

    def test_noisy_paths_can_be_skipped(self, make_client, access_records):
        """A liveness probe every second would otherwise dominate the log."""
        app = self._app(skip_paths=["/healthz"])

        @app.get("/healthz")
        def handler(res: Response):
            return res.json({})

        make_client(app).get("/healthz")
        assert access_records == []

    def test_the_request_id_is_carried_into_the_log_line(self, make_client, access_records):
        """This is what ties a user's report to a line in the log."""
        app = self._app()

        @app.get("/x")
        def handler(res: Response):
            return res.json({})

        make_client(app).get("/x", headers={"x-request-id": "trace-9"})
        assert access_records[-1].requestId == "trace-9"
