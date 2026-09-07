"""The built-in interceptors and pipes, and what they cost.

The behavioural assertions matter, but so does the last test in each class:
a helper that quietly moves a synchronous route onto an event loop is a poor
trade for a timing header, so the built-ins are expected not to.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from slowfw import BadRequest, Response, SlowAPI
from slowfw.concurrency import _LoopThread, shutdown_loop_thread
from slowfw.interceptors import (
    CacheInterceptor,
    EnvelopeInterceptor,
    Interceptor,
    TimingInterceptor,
    use_interceptors,
)
from slowfw.pipes import (
    ClampPipe,
    DefaultValuePipe,
    LowercasePipe,
    NotEmptyPipe,
    ParseIntPipe,
    TrimPipe,
    use_pipes,
)
from slowfw.testing import TestClient


class TestTimingInterceptor:
    def test_it_reports_handler_time_on_the_response(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        @use_interceptors(TimingInterceptor())
        def handler(res: Response):
            return {"ok": True}

        response = make_client(app).get("/x")
        assert response.headers["x-handler-time"].endswith("ms")

    def test_the_header_is_set_even_when_the_handler_raises(self, make_client):
        app = SlowAPI()

        @app.get("/boom")
        @use_interceptors(TimingInterceptor(header="X-Took"))
        def handler(res: Response):
            raise ValueError("nope")

        response = make_client(app).get("/boom")
        assert response.status_code == 500
        assert "x-took" in response.headers


class TestEnvelopeInterceptor:
    def test_plain_payloads_are_wrapped(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        @use_interceptors(EnvelopeInterceptor())
        def handler(res: Response):
            return {"id": 1}

        body = make_client(app).get("/x").json()
        assert body["data"] == {"id": 1}
        assert body["meta"]["path"] == "/x"

    def test_meta_can_be_omitted_and_the_key_renamed(self, make_client):
        app = SlowAPI()

        @app.get("/x")
        @use_interceptors(EnvelopeInterceptor(key="result", include_meta=False))
        def handler(res: Response):
            return [1, 2]

        assert make_client(app).get("/x").json() == {"result": [1, 2]}

    def test_an_explicit_response_is_left_alone(self, make_client):
        """Otherwise a file download would arrive wrapped in JSON."""
        app = SlowAPI()

        @app.get("/raw")
        @use_interceptors(EnvelopeInterceptor())
        def handler(res: Response):
            return res.text("plain")

        response = make_client(app).get("/raw")
        assert response.text == "plain"


class TestCacheInterceptor:
    def test_a_repeat_read_is_served_from_the_cache(self, make_client):
        app = SlowAPI()
        calls: list[int] = []

        @app.get("/x")
        @use_interceptors(CacheInterceptor(ttl=60))
        def handler(res: Response):
            calls.append(1)
            return {"n": len(calls)}

        client = make_client(app)
        first = client.get("/x")
        second = client.get("/x")

        assert first.headers["x-cache"] == "MISS"
        assert second.headers["x-cache"] == "HIT"
        assert second.json() == {"n": 1} and len(calls) == 1

    def test_the_query_string_is_part_of_the_key(self, make_client):
        app = SlowAPI()
        calls: list[str] = []

        @app.get("/x")
        @use_interceptors(CacheInterceptor(ttl=60))
        def handler(res: Response, q: str = "a"):
            calls.append(q)
            return {"q": q}

        client = make_client(app)
        client.get("/x?q=a")
        client.get("/x?q=b")
        assert calls == ["a", "b"]

    def test_writes_are_never_cached(self, make_client):
        app = SlowAPI()
        calls: list[int] = []

        @app.post("/x")
        @use_interceptors(CacheInterceptor(ttl=60))
        def handler(res: Response):
            calls.append(1)
            return {"n": len(calls)}

        client = make_client(app)
        client.post("/x", json={})
        client.post("/x", json={})
        assert len(calls) == 2

    def test_the_store_is_bounded(self, make_client):
        """An unbounded cache is a memory leak with a friendly name."""
        app = SlowAPI()
        cache = CacheInterceptor(ttl=60, max_entries=2)

        @app.get("/x")
        @use_interceptors(cache)
        def handler(res: Response, q: str = ""):
            return {"q": q}

        client = make_client(app)
        for value in "abcd":
            client.get(f"/x?q={value}")
        assert len(cache._store) <= 2


class TestCustomInterceptors:
    def test_a_subclass_sees_the_return_value_not_the_bytes(self, make_client):
        app = SlowAPI()

        class Double(Interceptor):
            async def intercept(self, ctx, call_next):
                result = await call_next()
                return {"n": result["n"] * 2}

        @app.get("/x")
        @use_interceptors(Double())
        def handler(res: Response):
            return {"n": 21}

        assert make_client(app).get("/x").json() == {"n": 42}

    def test_interceptors_nest_in_declaration_order(self, make_client):
        app = SlowAPI()
        order: list[str] = []

        class Outer(Interceptor):
            async def intercept(self, ctx, call_next):
                order.append("outer-in")
                result = await call_next()
                order.append("outer-out")
                return result

        class Inner(Interceptor):
            async def intercept(self, ctx, call_next):
                order.append("inner-in")
                result = await call_next()
                order.append("inner-out")
                return result

        @app.get("/x")
        @use_interceptors(Outer(), Inner())
        def handler(res: Response):
            return {}

        make_client(app).get("/x")
        assert order == ["outer-in", "inner-in", "inner-out", "outer-out"]


class TestPipes:
    @pytest.mark.parametrize(
        ("pipe", "value", "expected"),
        [
            (TrimPipe(), "  hi  ", "hi"),
            (LowercasePipe(), "HI", "hi"),
            (ParseIntPipe(), "42", 42),
            (ClampPipe(1, 10), 50, 10),
            (ClampPipe(1, 10), -5, 1),
            (DefaultValuePipe("fallback"), None, "fallback"),
            (DefaultValuePipe("fallback"), "given", "given"),
        ],
    )
    def test_transformations(self, pipe, value, expected):
        assert pipe.transform(value, None) == expected

    def test_not_empty_rejects_empty_input(self):
        meta = SimpleNamespace(name="q")
        with pytest.raises(BadRequest):
            NotEmptyPipe().transform("", meta)
        with pytest.raises(BadRequest):
            NotEmptyPipe().transform(None, meta)

    def test_not_empty_is_length_based_so_whitespace_needs_a_trim_first(self):
        """Documented behaviour, pinned so it cannot drift silently."""
        meta = SimpleNamespace(name="q")
        assert NotEmptyPipe().transform("   ", meta) == "   "
        # Trimmed first, the same value is correctly rejected.
        with pytest.raises(BadRequest):
            NotEmptyPipe().transform(TrimPipe().transform("   ", meta), meta)

    def test_parse_int_rejects_what_is_not_a_number(self):
        with pytest.raises(BadRequest):
            ParseIntPipe().transform("many", SimpleNamespace(name="n"))

    def test_pipes_apply_to_a_handler_parameter(self, make_client):
        app = SlowAPI()

        @app.get("/echo")
        @use_pipes(TrimPipe(), LowercasePipe())
        def handler(res: Response, q: str = ""):
            return {"q": q}

        assert make_client(app).get("/echo?q=%20HELLO%20").json() == {"q": "hello"}


class TestFastPathCost:
    """A helper should not silently opt a synchronous route out of the fast path."""

    @pytest.mark.parametrize(
        "interceptor",
        [TimingInterceptor(), EnvelopeInterceptor(), CacheInterceptor()],
        ids=["timing", "envelope", "cache"],
    )
    def test_built_in_interceptors_keep_the_loop_free_path(self, interceptor):
        shutdown_loop_thread()
        app = SlowAPI()

        @app.get("/x")
        @use_interceptors(interceptor)
        def handler(res: Response):
            return {"ok": True}

        assert TestClient(app, protocol="wsgi").get("/x").status_code == 200
        assert _LoopThread._instance is None
