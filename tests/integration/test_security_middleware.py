"""The middleware that stands between the internet and the handler.

Each of these has a failure mode that is quiet rather than loud: a spoofable
client IP breaks rate limiting without erroring, a permissive CORS reflection
leaks a session, a debug traceback leaks a file path.  The tests are written
from the direction an attacker would come from.
"""

from __future__ import annotations

import pytest

from slowapi import Response, SlowAPI
from slowapi.middleware import (
    CORSMiddleware,
    GZipMiddleware,
    ProxyHeadersMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)


def _echo_app(*middlewares):
    app = SlowAPI()
    for middleware in middlewares:
        app.use(middleware)

    @app.get("/x")
    def handler(req, res: Response):
        client = req.client
        return res.json({"scheme": req.scheme, "client": client.host if client else None})

    return app


class TestProxyHeaders:
    def test_an_untrusted_peer_cannot_rewrite_its_own_address(self):
        """The whole point: otherwise any client picks its own rate-limit bucket."""
        app = _echo_app(ProxyHeadersMiddleware(trusted_hosts=["10.0.0.1"]))
        from slowapi.testing import TestClient

        client = TestClient(app, protocol="wsgi", client=("203.0.113.9", 5000))
        body = client.get("/x", headers={"x-forwarded-for": "1.2.3.4"}).json()

        assert body["client"] == "203.0.113.9"

    def test_a_trusted_proxy_is_believed(self):
        from slowapi.testing import TestClient

        app = _echo_app(ProxyHeadersMiddleware(trusted_hosts=["10.0.0.1"]))
        client = TestClient(app, protocol="wsgi", client=("10.0.0.1", 5000))

        body = client.get(
            "/x", headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1", "x-forwarded-proto": "https"}
        ).json()

        assert body["client"] == "1.2.3.4"
        assert body["scheme"] == "https"

    def test_a_cidr_range_can_be_trusted(self):
        from slowapi.testing import TestClient

        app = _echo_app(ProxyHeadersMiddleware(trusted_hosts=["10.0.0.0/8"]))
        client = TestClient(app, protocol="wsgi", client=("10.42.7.1", 5000))

        assert (
            client.get("/x", headers={"x-forwarded-for": "1.2.3.4"}).json()["client"] == "1.2.3.4"
        )

    def test_a_peer_outside_the_range_is_not_trusted(self):
        from slowapi.testing import TestClient

        app = _echo_app(ProxyHeadersMiddleware(trusted_hosts=["10.0.0.0/8"]))
        client = TestClient(app, protocol="wsgi", client=("192.168.1.1", 5000))

        assert (
            client.get("/x", headers={"x-forwarded-for": "1.2.3.4"}).json()["client"] != "1.2.3.4"
        )

    def test_the_leftmost_hop_is_the_client(self):
        from slowapi.testing import TestClient

        app = _echo_app(ProxyHeadersMiddleware(trusted_hosts=["*"]))
        client = TestClient(app, protocol="wsgi", client=("10.0.0.1", 5000))

        body = client.get("/x", headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8, 9.10.11.12"}).json()

        assert body["client"] == "1.2.3.4"


class TestTrustedHost:
    def test_an_unlisted_host_header_is_refused(self, make_client):
        app = _echo_app(TrustedHostMiddleware(allowed_hosts=["example.com"]))
        assert make_client(app).get("/x", headers={"host": "evil.com"}).status_code == 400

    def test_a_listed_host_passes(self, make_client):
        app = _echo_app(TrustedHostMiddleware(allowed_hosts=["example.com"]))
        assert make_client(app).get("/x", headers={"host": "example.com"}).status_code == 200

    def test_a_wildcard_subdomain_matches(self, make_client):
        app = _echo_app(TrustedHostMiddleware(allowed_hosts=["*.example.com"]))
        assert make_client(app).get("/x", headers={"host": "api.example.com"}).status_code == 200

    def test_a_port_does_not_defeat_the_match(self, make_client):
        app = _echo_app(TrustedHostMiddleware(allowed_hosts=["example.com"]))
        assert make_client(app).get("/x", headers={"host": "example.com:8000"}).status_code == 200


class TestSecurityHeaders:
    def test_the_default_set_is_applied(self, make_client):
        headers = make_client(_echo_app(SecurityHeadersMiddleware())).get("/x").headers

        assert headers["x-content-type-options"] == "nosniff"
        assert "x-frame-options" in headers
        assert "referrer-policy" in headers

    def test_hsts_is_off_by_default(self):
        """Opt-in on purpose: an accidental HSTS header is hard to walk back."""
        assert SecurityHeadersMiddleware().hsts_seconds is None

    def test_hsts_is_sent_over_https(self, make_client):
        app = _echo_app(SecurityHeadersMiddleware(hsts_seconds=31536000))
        client = make_client(app, base_url="https://testserver")

        header = client.get("/x").headers["strict-transport-security"]

        assert "max-age=31536000" in header and "includeSubDomains" in header

    def test_hsts_is_withheld_over_plain_http(self, make_client):
        """Browsers ignore it there, and sending it anyway misleads an auditor."""
        app = _echo_app(SecurityHeadersMiddleware(hsts_seconds=31536000))
        assert "strict-transport-security" not in make_client(app).get("/x").headers

    def test_a_content_security_policy_can_be_set(self, make_client):
        app = _echo_app(SecurityHeadersMiddleware(content_security_policy="default-src 'self'"))
        assert make_client(app).get("/x").headers["content-security-policy"] == "default-src 'self'"


class TestCORS:
    def test_an_unlisted_origin_is_not_reflected(self, make_client):
        app = _echo_app(CORSMiddleware(allow_origins=["https://app.example.com"]))

        headers = make_client(app).get("/x", headers={"origin": "https://evil.com"}).headers

        assert "access-control-allow-origin" not in headers

    def test_a_listed_origin_is_allowed(self, make_client):
        app = _echo_app(CORSMiddleware(allow_origins=["https://app.example.com"]))

        headers = make_client(app).get("/x", headers={"origin": "https://app.example.com"}).headers

        assert headers["access-control-allow-origin"] == "https://app.example.com"

    def test_a_preflight_is_answered_without_reaching_the_handler(self, make_client):
        app = _echo_app(
            CORSMiddleware(allow_origins=["https://app.example.com"], allow_methods=["POST"])
        )

        response = make_client(app).options(
            "/x",
            headers={
                "origin": "https://app.example.com",
                "access-control-request-method": "POST",
            },
        )

        assert response.status_code in (200, 204)
        assert "POST" in response.headers["access-control-allow-methods"]

    def test_credentials_with_a_wildcard_are_refused_at_construction(self):
        """Browsers reject that pair, so a running app that sends it is broken."""
        from slowapi.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="List the origins explicitly"):
            CORSMiddleware(allow_origins=["*"], allow_credentials=True)

    def test_a_reflected_origin_varies_so_caches_do_not_share_it(self, make_client):
        app = _echo_app(
            CORSMiddleware(
                allow_origins=["https://a.example.com", "https://b.example.com"],
                allow_credentials=True,
            )
        )

        headers = make_client(app).get("/x", headers={"origin": "https://a.example.com"}).headers

        assert headers["access-control-allow-origin"] == "https://a.example.com"
        assert headers["access-control-allow-credentials"] == "true"
        assert "origin" in headers.get("vary", "").lower()


class TestRateLimit:
    def test_requests_over_the_limit_get_429(self, make_client):
        app = _echo_app(RateLimitMiddleware(limit=2, window=60))
        client = make_client(app)

        assert client.get("/x").status_code == 200
        assert client.get("/x").status_code == 200
        blocked = client.get("/x")

        assert blocked.status_code == 429
        assert "retry-after" in blocked.headers

    def test_the_remaining_budget_is_advertised(self, make_client):
        app = _echo_app(RateLimitMiddleware(limit=5, window=60))
        headers = make_client(app).get("/x").headers
        assert headers["x-ratelimit-limit"] == "5"
        assert headers["x-ratelimit-remaining"] == "4"


class TestCompression:
    def test_a_large_body_is_compressed_when_the_client_asks(self, make_client):
        app = SlowAPI()
        app.use(GZipMiddleware(minimum_size=10))

        @app.get("/big")
        def handler(res: Response):
            return res.text("x" * 5000)

        response = make_client(app).get("/big", headers={"accept-encoding": "gzip"})

        assert response.headers.get("content-encoding") == "gzip"
        assert response.text == "x" * 5000

    def test_a_client_that_did_not_ask_gets_plain_bytes(self, make_client):
        app = SlowAPI()
        app.use(GZipMiddleware(minimum_size=10))

        @app.get("/big")
        def handler(res: Response):
            return res.text("x" * 5000)

        assert "content-encoding" not in make_client(app).get("/big").headers

    def test_a_small_body_is_left_alone(self, make_client):
        """Compressing 20 bytes costs CPU and makes them bigger."""
        app = SlowAPI()
        app.use(GZipMiddleware(minimum_size=1000))

        @app.get("/small")
        def handler(res: Response):
            return res.text("tiny")

        response = make_client(app).get("/small", headers={"accept-encoding": "gzip"})
        assert "content-encoding" not in response.headers


class TestErrorRendering:
    def test_an_unhandled_exception_never_leaks_internals_by_default(self, make_client):
        app = SlowAPI()

        @app.get("/boom")
        def handler(res: Response):
            raise ValueError("connection string: postgres://user:pw@host/db")

        response = make_client(app).get("/boom")

        assert response.status_code == 500
        assert "postgres://" not in response.text
        assert "Traceback" not in response.text

    def test_debug_mode_opts_into_the_detail(self, make_client):
        app = SlowAPI(debug=True)

        @app.get("/boom")
        def handler(res: Response):
            raise ValueError("the actual problem")

        body = make_client(app).get("/boom").text
        assert "the actual problem" in body

    def test_a_custom_handler_takes_over(self, make_client):
        app = SlowAPI()

        class OutOfStock(Exception):
            pass

        @app.exception_handler(OutOfStock)
        def handle(req, exc, res):
            return res.status(409).json({"error": "out of stock"})

        @app.get("/buy")
        def handler(res: Response):
            raise OutOfStock

        response = make_client(app).get("/buy")
        assert response.status_code == 409
        assert response.json() == {"error": "out of stock"}

    def test_a_status_code_handler_takes_over(self, make_client):
        app = SlowAPI()

        @app.exception_handler(404)
        def handle(req, exc, res):
            return res.status(404).json({"error": "nothing here"})

        assert make_client(app).get("/nope").json() == {"error": "nothing here"}

    @pytest.mark.parametrize(("path", "expected"), [("/nope", 404), ("/x", 405)])
    def test_routing_failures_carry_the_right_status(self, make_client, path, expected):
        app = _echo_app()
        client = make_client(app)
        response = client.post(path, json={}) if path == "/x" else client.get(path)
        assert response.status_code == expected


class TestExceptionHandlerArgumentOrder:
    """Handler arguments are matched by role, not by position.

    The old rule was positional ``(req, exc, res)``, which contradicts the
    ``(req, res)`` order used everywhere else in the framework; getting it
    backwards produced an ``AttributeError`` from inside error handling rather
    than anything that named the mistake.
    """

    def _app_with(self, handler):
        app = SlowAPI()
        app.exception_handlers.add(ValueError, handler)

        @app.get("/boom")
        def boom(res: Response):
            raise ValueError("expected")

        return app

    def test_the_historical_order_still_works(self, make_client):
        def handle(req, exc, res):
            return res.status(418).json({"detail": str(exc), "path": req.path})

        body = make_client(self._app_with(handle)).get("/boom")
        assert body.status_code == 418
        assert body.json() == {"detail": "expected", "path": "/boom"}

    def test_the_framework_order_also_works(self, make_client):
        def handle(req, res, exc):
            return res.status(418).json({"detail": str(exc), "path": req.path})

        assert make_client(self._app_with(handle)).get("/boom").json()["detail"] == "expected"

    def test_annotations_settle_it_regardless_of_name(self, make_client):
        from slowapi import Request

        def handle(a: Response, b: ValueError, c: Request):
            return a.status(418).json({"detail": str(b), "path": c.path})

        assert make_client(self._app_with(handle)).get("/boom").status_code == 418

    def test_a_handler_can_take_fewer_arguments(self, make_client):
        def handle(req, exc):
            return {"detail": str(exc)}

        response = make_client(self._app_with(handle)).get("/boom")
        assert response.json() == {"detail": "expected"}

    def test_a_method_handler_skips_self(self, make_client):
        class Handlers:
            def handle(self, req, res, exc):
                return res.status(418).json({"detail": str(exc)})

        assert make_client(self._app_with(Handlers().handle)).get("/boom").status_code == 418
