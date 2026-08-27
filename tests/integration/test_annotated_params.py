"""``Annotated[...]`` as the preferred way to attach a parameter marker.

The default-value spelling (``q: str = Query(...)``) still works and always
will, but it forces every marked parameter to sit after the unmarked ones and
burns the default slot.  Putting the marker in the type frees both.
"""

from __future__ import annotations

from typing import Annotated

import pytest

from slowapi import (
    ConfigurationError,
    Cookie,
    Depends,
    Header,
    Inject,
    InjectionToken,
    Query,
    Response,
    SlowAPI,
)

SETTINGS = InjectionToken("SETTINGS")


def current_user(token: Annotated[str, Header(alias="x-token")] = "anon") -> dict:
    """Module level on purpose: ``get_type_hints`` cannot see a closure's locals."""
    return {"name": token}


class TestAnnotatedParameters:
    def test_query_constraints_apply(self, make_client):
        app = SlowAPI()

        @app.get("/search")
        def search(res: Response, q: Annotated[str, Query(min_length=2)]):
            return res.json({"q": q})

        client = make_client(app)
        assert client.get("/search?q=hi").json() == {"q": "hi"}
        assert client.get("/search?q=h").status_code == 422

    def test_a_marked_parameter_can_precede_an_unmarked_default(self, make_client):
        """The reason the spelling exists: no forced argument reordering."""
        app = SlowAPI()

        @app.get("/search")
        def search(res: Response, q: Annotated[str, Query()], page: int = 1):
            return res.json({"q": q, "page": page})

        assert make_client(app).get("/search?q=x&page=4").json() == {"q": "x", "page": 4}

    def test_repeated_values_collect_into_a_list(self, make_client):
        app = SlowAPI()

        @app.get("/filter")
        def filter_(res: Response, tags: Annotated[list[str], Query()] = ()):
            return res.json({"tags": tags})

        assert make_client(app).get("/filter?tags=a&tags=b").json() == {"tags": ["a", "b"]}

    def test_headers_and_cookies_work_the_same_way(self, make_client):
        app = SlowAPI()

        @app.get("/whoami")
        def whoami(
            res: Response,
            token: Annotated[str, Header(alias="x-token")],
            theme: Annotated[str, Cookie()] = "light",
        ):
            return res.json({"token": token, "theme": theme})

        client = make_client(app)
        response = client.get("/whoami", headers={"x-token": "abc", "cookie": "theme=dark"})
        assert response.json() == {"token": "abc", "theme": "dark"}

    def test_depends_resolves_through_the_annotation(self, make_client):
        app = SlowAPI()

        @app.get("/me")
        def me(res: Response, user: Annotated[dict, Depends(current_user)]):
            return res.json(user)

        assert make_client(app).get("/me", headers={"x-token": "ada"}).json() == {"name": "ada"}

    def test_inject_resolves_a_container_token(self, make_client):
        app = SlowAPI()
        app.provide(SETTINGS, value={"env": "test"})

        @app.get("/config")
        def config(res: Response, settings: Annotated[dict, Inject(SETTINGS)]):
            return res.json(settings)

        assert make_client(app).get("/config").json() == {"env": "test"}

    def test_unknown_metadata_passes_through_untouched(self, make_client):
        """Annotations shared with other tools must not confuse the resolver."""
        app = SlowAPI()

        @app.get("/items")
        def items(res: Response, limit: Annotated[int, "how many to return"] = 10):
            return res.json({"limit": limit})

        assert make_client(app).get("/items?limit=3").json() == {"limit": 3}

    def test_declaring_the_marker_twice_is_caught_before_serving(self):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, q: Annotated[str, Query()] = Query()):
            return res.json({})

        # Registration cannot decide this (providers may still be coming), so
        # the check runs at startup -- still before any request is served.
        with pytest.raises(ConfigurationError, match="Pick one"):
            app.check()

    def test_two_markers_inside_one_annotation_are_caught_before_serving(self):
        app = SlowAPI()

        @app.get("/x")
        def handler(res: Response, q: Annotated[str, Query(), Header()]):
            return res.json({})

        with pytest.raises(ConfigurationError, match="Keep one"):
            app.check()
