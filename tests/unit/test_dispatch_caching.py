"""The per-request caches added for speed must not change behaviour.

Two things are cached on the hot path: the *shape* of a middleware (its arity
and whether it is async), and the route a request matched. Both are only safe
under conditions worth asserting rather than assuming.
"""

from __future__ import annotations

import gc
import weakref

import pytest

from slowfw import SlowAPI
from slowfw.middleware.base import _SHAPES, middleware_shape
from slowfw.testing import TestClient


class DispatchMiddleware:
    async def dispatch(self, request, response, call_next):
        return await call_next()


class UnhashableMiddleware:
    __hash__ = None  # type: ignore[assignment]

    async def dispatch(self, request, response, call_next):
        return await call_next()


class TestMiddlewareShapeCache:
    def test_it_reports_the_same_shape_as_an_uncached_first_look(self):
        instance = DispatchMiddleware()
        first = middleware_shape(instance)
        second = middleware_shape(instance)
        assert first[1:] == second[1:] == (3, True)
        assert first[0].__func__ is DispatchMiddleware.dispatch

    def test_it_does_not_keep_the_middleware_alive(self):
        """A bound method stored as the value would pin its own weak key."""
        instance = DispatchMiddleware()
        middleware_shape(instance)
        assert instance in _SHAPES

        reference = weakref.ref(instance)
        del instance
        gc.collect()

        assert reference() is None

    def test_an_unhashable_middleware_still_works_uncached(self):
        instance = UnhashableMiddleware()
        target, arity, is_async = middleware_shape(instance)
        assert (arity, is_async) == (3, True)
        assert target.__func__ is UnhashableMiddleware.dispatch

    def test_a_function_and_a_class_are_told_apart(self):
        def two_arg(request, response):
            return None

        assert middleware_shape(two_arg)[1] == 2
        assert middleware_shape(two_arg)[2] is False


class TestRouteMatchMemo:
    def test_a_middleware_that_rewrites_the_path_re_matches(self):
        """The memo is keyed on the path, so a rewrite must not be ignored."""
        app = SlowAPI()

        @app.get("/new")
        def new_route() -> dict:
            return {"route": "new"}

        @app.get("/old")
        def old_route() -> dict:
            return {"route": "old"}

        async def rewrite(request, response, call_next):
            if request.path == "/old":
                request.scope["path"] = "/new"
            return await call_next()

        app.use(rewrite)

        with TestClient(app) as client:
            assert client.get("/old").json() == {"route": "new"}
            assert client.get("/new").json() == {"route": "new"}

    def test_path_params_survive_the_memo(self):
        app = SlowAPI()

        @app.get("/items/{item_id:int}")
        def item(item_id: int) -> dict:
            return {"item_id": item_id}

        with TestClient(app) as client:
            assert client.get("/items/1").json() == {"item_id": 1}
            assert client.get("/items/2").json() == {"item_id": 2}

    @pytest.mark.parametrize("path", ["/missing", "/items/not-an-int"])
    def test_an_unmatched_path_still_404s(self, path):
        app = SlowAPI()

        @app.get("/items/{item_id:int}")
        def item(item_id: int) -> dict:
            return {"item_id": item_id}

        with TestClient(app) as client:
            assert client.get(path).status_code == 404
