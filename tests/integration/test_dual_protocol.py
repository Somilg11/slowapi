"""The central promise: identical behaviour on WSGI and ASGI.

Rather than asserting behaviour per protocol, these tests run each scenario
through *both* adapters and assert the results are equal.  A regression that
only affects one transport cannot slip through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from slowfw import SlowAPI
from slowfw.concurrency import _LoopThread, shutdown_loop_thread
from slowfw.testing import TestClient


@dataclass
class Item:
    name: str
    qty: int = 1


def build_app() -> SlowAPI:
    app = SlowAPI(title="dual", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/sync/{id:int}")
    def sync_handler(id: int, res):
        res.json({"id": id, "kind": "sync"})

    @app.get("/async/{id:int}")
    async def async_handler(id: int) -> dict:
        return {"id": id, "kind": "async"}

    @app.post("/items")
    def create(item: Item) -> dict:
        return {"name": item.name, "qty": item.qty}

    @app.get("/headers")
    def headers(req, res):
        res.json({"agent": req.get("user-agent"), "host": req.get("host")})

    @app.get("/stream")
    def stream(res):
        res.stream((f"line{i}\n".encode() for i in range(3)), media_type="text/plain")

    @app.get("/boom")
    def boom():
        raise RuntimeError("intentional")

    return app


@pytest.fixture
def clients():
    app = build_app()
    return TestClient(app, protocol="wsgi"), TestClient(app, protocol="asgi")


def _without_request_id(payload: bytes) -> bytes:
    """Strip the one field that is legitimately different between two runs."""
    return re.sub(rb'"requestId":"[0-9a-f]+",?', b"", payload)


def compare(clients, method: str, path: str, **kwargs):
    """Run one request through both adapters and assert they agree."""
    wsgi, asgi = clients
    a = wsgi.request(method, path, **kwargs)
    b = asgi.request(method, path, **kwargs)
    assert a.status_code == b.status_code, f"{path}: {a.status_code} vs {b.status_code}"
    assert _without_request_id(a.content) == _without_request_id(b.content), (
        f"{path}: {a.content!r} vs {b.content!r}"
    )
    return a


class TestParity:
    def test_sync_handler(self, clients):
        assert compare(clients, "GET", "/sync/7").json() == {"id": 7, "kind": "sync"}

    def test_async_handler(self, clients):
        assert compare(clients, "GET", "/async/7").json() == {"id": 7, "kind": "async"}

    def test_json_body_and_validation(self, clients):
        assert compare(clients, "POST", "/items", json={"name": "x", "qty": "3"}).json() == {
            "name": "x",
            "qty": 3,
        }

    def test_validation_failure(self, clients):
        assert compare(clients, "POST", "/items", json={"qty": "many"}).status_code == 422

    def test_request_headers_are_visible(self, clients):
        response = compare(clients, "GET", "/headers", headers={"user-agent": "probe/1"})
        assert response.json()["agent"] == "probe/1"

    def test_streaming(self, clients):
        assert compare(clients, "GET", "/stream").text == "line0\nline1\nline2\n"

    def test_unhandled_exception_becomes_500_on_both(self, clients):
        assert compare(clients, "GET", "/boom").status_code == 500

    def test_not_found(self, clients):
        assert compare(clients, "GET", "/absent").status_code == 404

    def test_method_not_allowed(self, clients):
        assert compare(clients, "DELETE", "/items").status_code == 405

    def test_every_response_carries_a_correlation_id(self, clients):
        for client in clients:
            response = client.get("/sync/1")
            assert len(response.headers["x-request-id"]) == 32

    def test_an_inbound_correlation_id_is_propagated(self, clients):
        for client in clients:
            response = client.get("/sync/1", headers={"x-request-id": "trace-abc"})
            assert response.headers["x-request-id"] == "trace-abc"

    def test_head_has_no_body_but_keeps_headers(self, clients):
        wsgi, asgi = clients
        for client in (wsgi, asgi):
            response = client.head("/sync/1")
            assert response.status_code == 200
            assert response.content == b""
            assert response.headers["content-type"].startswith("application/json")


class TestFastPath:
    """The performance claim, asserted rather than promised."""

    def test_a_fully_sync_wsgi_request_creates_no_event_loop(self):
        shutdown_loop_thread()
        app = SlowAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.get("/plain")
        def plain(res):
            res.text("ok")

        client = TestClient(app, protocol="wsgi")
        assert client.get("/plain").text == "ok"
        assert _LoopThread._instance is None, (
            "A fully synchronous WSGI request started the background event loop; "
            "the loop-free fast path has regressed."
        )

    def test_an_async_handler_on_wsgi_uses_the_shared_loop(self):
        shutdown_loop_thread()
        app = SlowAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.get("/waits")
        async def waits() -> dict:
            import asyncio

            await asyncio.sleep(0.001)
            return {"ok": True}

        try:
            client = TestClient(app, protocol="wsgi")
            assert client.get("/waits").json() == {"ok": True}
            assert _LoopThread._instance is not None
        finally:
            shutdown_loop_thread()
