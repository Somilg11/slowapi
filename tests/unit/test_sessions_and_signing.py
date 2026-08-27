"""Signed cookies: tampering, expiry, rotation, and the size ceiling.

A signed cookie is only worth as much as its verification, so most of these
tests are attempts to get a forged or stale token accepted.
"""

from __future__ import annotations

import time

import pytest

from slowapi import Response, SlowAPI
from slowapi.exceptions import SlowAPIError
from slowapi.middleware.session import COOKIE_LIMIT, Session, SessionMiddleware
from slowapi.signing import BadSignature, SignatureExpired, Signer

SECRET = "test-secret-not-for-production"


class TestSigner:
    def test_a_round_trip_returns_the_payload(self):
        signer = Signer(SECRET)
        assert signer.unsign(signer.sign({"user": 1})) == {"user": 1}

    def test_a_tampered_payload_is_rejected(self):
        signer = Signer(SECRET)
        _body, stamp, signature = signer.sign({"admin": False}).split(".")
        forged = Signer(SECRET).sign({"admin": True}).split(".")[0]

        with pytest.raises(BadSignature):
            signer.unsign(f"{forged}.{stamp}.{signature}")

    def test_a_different_secret_cannot_verify(self):
        token = Signer(SECRET).sign({"user": 1})
        with pytest.raises(BadSignature):
            Signer("another-secret").unsign(token)

    def test_a_different_salt_cannot_verify(self):
        """Salts namespace tokens so a session cookie is not a password reset."""
        token = Signer(SECRET, salt="session").sign({"user": 1})
        with pytest.raises(BadSignature):
            Signer(SECRET, salt="password-reset").unsign(token)

    @pytest.mark.parametrize("token", ["", "a", "a.b", "a.b.c.d", "not-a-token"])
    def test_malformed_tokens_are_rejected_not_crashed_on(self, token):
        with pytest.raises(BadSignature):
            Signer(SECRET).unsign(token)

    def test_an_expired_token_is_rejected(self, monkeypatch):
        signer = Signer(SECRET)
        token = signer.sign({"user": 1})
        later = time.time() + 10_000_000
        monkeypatch.setattr(time, "time", lambda: later)
        with pytest.raises(SignatureExpired):
            signer.unsign(token, max_age=60)

    def test_a_fallback_secret_verifies_but_never_signs(self):
        """Key rotation must not log every user out."""
        old, new = "old-secret", "new-secret"
        old_token = Signer(old).sign({"user": 1})

        rotated = Signer(new, fallbacks=[old])

        assert rotated.unsign(old_token) == {"user": 1}
        # New tokens are signed with the new key only.
        assert Signer(old).safe_unsign(rotated.sign({"user": 2})) is None

    def test_an_empty_secret_is_refused(self):
        with pytest.raises(SlowAPIError, match="non-empty secret"):
            Signer("")

    def test_safe_unsign_returns_the_default_rather_than_raising(self):
        signer = Signer(SECRET)
        assert signer.safe_unsign(None, default={}) == {}
        assert signer.safe_unsign("garbage", default={}) == {}


class TestSession:
    def test_it_tracks_modification(self):
        session = Session({"a": 1})
        assert not session.modified

        session["b"] = 2
        assert session.modified

    def test_every_mutating_operation_marks_it(self):
        for mutate in (
            lambda s: s.__setitem__("x", 1),
            lambda s: s.update({"x": 1}),
            lambda s: s.pop("a", None),
            lambda s: s.__delitem__("a"),
            lambda s: s.clear(),
        ):
            session = Session({"a": 1})
            mutate(session)
            assert session.modified

    def test_flashes_are_consumed_once(self):
        session = Session()
        session.flash("saved")
        session.flash("careful", "warning")

        assert session.get_flashes() == [["info", "saved"], ["warning", "careful"]]
        assert session.get_flashes() == []


class TestSessionMiddleware:
    def _app(self, **kwargs):
        app = SlowAPI()
        app.use(SessionMiddleware(SECRET, secure=False, **kwargs))

        @app.get("/login")
        def login(req, res: Response):
            req.state.session["user"] = "ada"
            return res.json({"ok": True})

        @app.get("/me")
        def me(req, res: Response):
            return res.json({"user": req.state.session.get("user")})

        @app.get("/logout")
        def logout(req, res: Response):
            req.state.session.clear()
            return res.json({"ok": True})

        return app

    def test_a_session_survives_between_requests(self, make_client):
        client = make_client(self._app())

        cookie = client.get("/login").headers["set-cookie"].split(";")[0]
        response = client.get("/me", headers={"cookie": cookie})

        assert response.json() == {"user": "ada"}

    def test_an_unread_session_sets_no_cookie(self, make_client):
        """Otherwise every anonymous response carries a pointless Set-Cookie."""
        assert "set-cookie" not in make_client(self._app()).get("/me").headers

    def test_a_forged_cookie_is_ignored_rather_than_trusted(self, make_client):
        client = make_client(self._app())
        forged = Signer("attacker-secret").sign({"user": "root"})

        response = client.get("/me", headers={"cookie": f"session={forged}"})

        assert response.json() == {"user": None}

    def test_logout_clears_the_cookie(self, make_client):
        client = make_client(self._app())
        cookie = client.get("/login").headers["set-cookie"].split(";")[0]

        response = client.get("/logout", headers={"cookie": cookie})

        header = response.headers["set-cookie"]
        assert "session=" in header and ("Max-Age=0" in header or "expires" in header.lower())

    def test_the_cookie_is_hardened_by_default(self, make_client):
        app = SlowAPI()
        app.use(SessionMiddleware(SECRET))

        @app.get("/login")
        def login(req, res: Response):
            req.state.session["user"] = "ada"
            return res.json({})

        header = make_client(app).get("/login").headers["set-cookie"].lower()

        assert "httponly" in header  # not readable from JavaScript
        assert "secure" in header  # not sent over plain HTTP
        assert "samesite=lax" in header  # not sent on cross-site POSTs

    def test_an_oversized_session_fails_loudly_instead_of_truncating(self, make_client):
        """A silently truncated session is a logout that looks like a bug."""
        app = SlowAPI()
        app.use(SessionMiddleware(SECRET, secure=False))

        @app.get("/big")
        def big(req, res: Response):
            req.state.session["blob"] = "x" * (COOKIE_LIMIT * 2)
            return res.json({})

        response = make_client(app).get("/big")
        assert response.status_code == 500

    def test_rotation_keeps_existing_sessions_valid(self, make_client):
        client = make_client(self._app())
        cookie = client.get("/login").headers["set-cookie"].split(";")[0]

        rotated = make_client(self._app(fallback_secrets=[SECRET]))
        # The rotated app signs with a new key but still accepts the old one.
        assert rotated.get("/me", headers={"cookie": cookie}).json() == {"user": "ada"}
