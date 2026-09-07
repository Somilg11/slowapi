"""Signed-cookie sessions.

The whole session lives in the cookie, signed but not encrypted, so there is no
server-side store to run, scale, or invalidate.  That trade-off is right for
preferences, flash messages, and a user id; it is wrong for anything secret or
anything larger than a couple of kilobytes, and the middleware says so loudly
rather than silently truncating.
"""

from __future__ import annotations

import typing as t

from ..request import Request
from ..response import Response
from ..signing import Signer

__all__ = ["Session", "SessionMiddleware"]

#: Browsers are only required to store 4096 bytes per cookie.
COOKIE_LIMIT = 4093


class Session(dict):
    """A dict that remembers whether it changed."""

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        self.modified = False
        self.cleared = False

    def __setitem__(self, key: t.Any, value: t.Any) -> None:
        super().__setitem__(key, value)
        self.modified = True

    def __delitem__(self, key: t.Any) -> None:
        super().__delitem__(key)
        self.modified = True

    def pop(self, key: t.Any, *default: t.Any) -> t.Any:
        self.modified = True
        return super().pop(key, *default)

    def update(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().update(*args, **kwargs)
        self.modified = True

    def clear(self) -> None:
        super().clear()
        self.modified = True
        self.cleared = True

    def flash(self, message: str, category: str = "info") -> None:
        """Queue a one-shot message, consumed by :meth:`get_flashes`."""
        queue = list(self.get("_flashes", []))
        queue.append([category, message])
        self["_flashes"] = queue

    def get_flashes(self) -> list[list[str]]:
        queue = self.pop("_flashes", [])
        return list(queue)


class SessionMiddleware:
    """Load the session before the handler and persist it afterwards."""

    def __init__(
        self,
        secret: str,
        *,
        cookie_name: str = "session",
        max_age: int = 14 * 24 * 3600,
        path: str = "/",
        domain: str | None = None,
        secure: bool = True,
        httponly: bool = True,
        samesite: str = "lax",
        salt: str = "slowfw.session",
        fallback_secrets: t.Sequence[str] = (),
    ) -> None:
        self.signer = Signer(secret, salt=salt, fallbacks=fallback_secrets)
        self.cookie_name = cookie_name
        self.max_age = max_age
        self.cookie_options = {
            "path": path,
            "domain": domain,
            "secure": secure,
            "httponly": httponly,
            "samesite": samesite,
        }

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        raw = request.cookies.get(self.cookie_name)
        data = self.signer.safe_unsign(raw, max_age=self.max_age, default={}) or {}
        session = Session(data if isinstance(data, dict) else {})
        request.state.session = session

        result = await call_next()
        target = result if isinstance(result, Response) else response

        if session.cleared and not session:
            target.clear_cookie(self.cookie_name, path=str(self.cookie_options["path"]))
            return result
        if not session.modified:
            return result

        token = self.signer.sign(dict(session))
        if len(token) > COOKIE_LIMIT:
            raise ValueError(
                f"Session cookie would be {len(token)} bytes, over the {COOKIE_LIMIT} byte "
                "browser limit. Store an identifier in the session and keep the payload "
                "server-side."
            )
        target.cookie(self.cookie_name, token, max_age=self.max_age, **self.cookie_options)  # type: ignore[arg-type]
        return result
