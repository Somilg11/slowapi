"""Built-in middleware, and the Express middleware contract."""

from __future__ import annotations

import pytest

from slowfw import SlowAPI
from slowfw.exceptions import ConfigurationError
from slowfw.middleware import (
    CORSMiddleware,
    GZipMiddleware,
    ProxyHeadersMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    SessionMiddleware,
    TrustedHostMiddleware,
)

SECRET = "unit-test-secret-key-0123456789abcdef"


@pytest.fixture
def app() -> SlowAPI:
    return SlowAPI(docs_url=None, redoc_url=None, openapi_url=None)


class TestMiddlewareContract:
    def test_three_argument_sync_middleware_wraps_the_handler(self, app, make_client):
        order: list[str] = []

        def outer(req, res, next):
            order.append("in")
            next()
            order.append("out")

        app.use(outer)

        @app.get("/x")
        def x(res):
            order.append("handler")
            res.text("ok")

        assert make_client(app).get("/x").text == "ok"
        assert order == ["in", "handler", "out"]

    def test_two_argument_middleware_runs_before_and_continues(self, app, make_client):
        def stamp(req, res):
            req.state.stamped = True

        app.use(stamp)

        @app.get("/x")
        def x(req, res):
            res.json({"stamped": req.state.get("stamped")})

        assert make_client(app).get("/x").json() == {"stamped": True}

    def test_async_middleware_can_post_process(self, app, make_client):
        async def tag(req, res, next):
            await next()
            res.set("X-Tag", "1")

        app.use(tag)

        @app.get("/x")
        def x(res):
            res.text("ok")

        assert make_client(app).get("/x").headers["x-tag"] == "1"

    def test_middleware_can_short_circuit_without_calling_next(self, app, make_client):
        def block(req, res, next):
            res.status(401).json({"error": "no"})

        app.use(block)

        @app.get("/x")
        def x(res):  # pragma: no cover - must not run
            res.text("should not happen")

        assert make_client(app).get("/x").status_code == 401

    def test_route_scoped_middleware_runs_only_for_that_route(self, app, make_client):
        def only_here(req, res, next):
            next()
            res.set("X-Route", "yes")

        @app.get("/with", middlewares=[only_here])
        def with_mw(res):
            res.text("a")

        @app.get("/without")
        def without_mw(res):
            res.text("b")

        client = make_client(app)
        assert client.get("/with").headers.get("x-route") == "yes"
        assert "x-route" not in client.get("/without").headers

    def test_a_bad_arity_is_reported_at_registration_not_at_request_time(self, app):
        def wrong(a, b, c, d):  # pragma: no cover
            pass

        app.use(wrong)

        @app.get("/x")
        def x(res):
            res.text("ok")

        from slowfw.testing import TestClient

        with pytest.raises(ConfigurationError, match="must take"):
            TestClient(app).get("/x")


class TestCORS:
    def test_preflight_is_answered_without_reaching_the_handler(self, app, make_client):
        app.use(CORSMiddleware(allow_origins=["https://app.example"]))

        @app.post("/x")
        def x(res):  # pragma: no cover - a preflight must not reach it
            res.text("nope")

        response = make_client(app).options(
            "/x",
            headers={
                "origin": "https://app.example",
                "access-control-request-method": "POST",
            },
        )
        assert response.status_code == 204
        assert response.headers["access-control-allow-origin"] == "https://app.example"

    def test_a_disallowed_origin_gets_no_cors_headers(self, app, make_client):
        app.use(CORSMiddleware(allow_origins=["https://app.example"]))

        @app.get("/x")
        def x(res):
            res.text("ok")

        response = make_client(app).get("/x", headers={"origin": "https://evil.example"})
        assert "access-control-allow-origin" not in response.headers

    def test_vary_origin_is_always_set_so_caches_stay_correct(self, app, make_client):
        app.use(CORSMiddleware(allow_origins=["https://app.example"]))

        @app.get("/x")
        def x(res):
            res.text("ok")

        response = make_client(app).get("/x", headers={"origin": "https://other.example"})
        assert "origin" in response.headers["vary"].lower()

    def test_wildcard_with_credentials_is_refused_at_construction(self):
        with pytest.raises(ConfigurationError, match="allow_credentials"):
            CORSMiddleware(allow_origins=["*"], allow_credentials=True)


class TestSecurityHeaders:
    def test_defaults_are_applied(self, app, make_client):
        app.use(SecurityHeadersMiddleware())

        @app.get("/x")
        def x(res):
            res.text("ok")

        headers = make_client(app).get("/x").headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "content-security-policy" in headers

    def test_hsts_is_not_sent_over_plain_http(self, app, make_client):
        app.use(SecurityHeadersMiddleware(hsts_seconds=31536000))

        @app.get("/x")
        def x(res):
            res.text("ok")

        assert "strict-transport-security" not in make_client(app).get("/x").headers


class TestTrustedHost:
    def test_an_unlisted_host_is_rejected(self, app, make_client):
        app.use(TrustedHostMiddleware(["api.example"], www_redirect=False))

        @app.get("/x")
        def x(res):
            res.text("ok")

        assert make_client(app).get("/x", headers={"host": "evil.example"}).status_code == 400

    def test_a_listed_host_passes(self, app, make_client):
        app.use(TrustedHostMiddleware(["api.example"]))

        @app.get("/x")
        def x(res):
            res.text("ok")

        assert make_client(app).get("/x", headers={"host": "api.example"}).status_code == 200


class TestGZip:
    def test_large_bodies_are_compressed_when_the_client_asks(self, app, make_client):
        app.use(GZipMiddleware(minimum_size=10))

        @app.get("/x")
        def x(res):
            res.text("hello " * 200)

        response = make_client(app).get("/x", headers={"accept-encoding": "gzip"})
        assert response.headers["content-encoding"] == "gzip"
        assert len(response.content) < 1200

    def test_small_bodies_are_left_alone(self, app, make_client):
        app.use(GZipMiddleware(minimum_size=500))

        @app.get("/x")
        def x(res):
            res.text("tiny")

        response = make_client(app).get("/x", headers={"accept-encoding": "gzip"})
        assert "content-encoding" not in response.headers


class TestSession:
    def test_the_session_survives_between_requests(self, app, make_client):
        app.use(SessionMiddleware(SECRET, secure=False))

        @app.get("/hit")
        def hit(req, res):
            session = req.state.session
            session["n"] = session.get("n", 0) + 1
            res.json({"n": session["n"]})

        client = make_client(app)
        assert [client.get("/hit").json()["n"] for _ in range(3)] == [1, 2, 3]

    def test_a_tampered_cookie_is_discarded_rather_than_trusted(self, app, make_client):
        app.use(SessionMiddleware(SECRET, secure=False))

        @app.get("/read")
        def read(req, res):
            res.json(dict(req.state.session))

        client = make_client(app)
        assert client.get("/read", cookies={"session": "forged.payload.signature"}).json() == {}


class TestRateLimit:
    def test_requests_past_the_limit_get_429(self, app, make_client):
        app.use(RateLimitMiddleware(limit=2, window=60))

        @app.get("/x")
        def x(res):
            res.text("ok")

        client = make_client(app)
        assert [client.get("/x").status_code for _ in range(4)] == [200, 200, 429, 429]

    def test_remaining_is_reported_in_a_header(self, app, make_client):
        app.use(RateLimitMiddleware(limit=5, window=60))

        @app.get("/x")
        def x(res):
            res.text("ok")

        assert make_client(app).get("/x").headers["x-ratelimit-remaining"] == "4"


class TestProxyHeaders:
    def test_forwarded_headers_are_ignored_from_an_untrusted_peer(self, app, make_client):
        app.use(ProxyHeadersMiddleware(trusted_hosts=["10.0.0.1"]))

        @app.get("/ip")
        def ip(req, res):
            res.json({"ip": req.ip})

        response = make_client(app).get("/ip", headers={"x-forwarded-for": "1.2.3.4"})
        assert response.json()["ip"] == "127.0.0.1"

    def test_forwarded_headers_are_honoured_from_a_trusted_peer(self, app, make_client):
        app.use(ProxyHeadersMiddleware(trusted_hosts=["127.0.0.1"]))

        @app.get("/ip")
        def ip(req, res):
            res.json({"ip": req.ip})

        response = make_client(app).get("/ip", headers={"x-forwarded-for": "1.2.3.4"})
        assert response.json()["ip"] == "1.2.3.4"
