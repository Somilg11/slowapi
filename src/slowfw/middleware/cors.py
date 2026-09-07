"""Cross-Origin Resource Sharing.

Correct CORS is mostly about the boring parts: answering preflights without
reaching the handler, never echoing an arbitrary origin when credentials are
allowed, and setting ``Vary: Origin`` so a shared cache cannot serve one
origin's response to another.  All three are handled here.
"""

from __future__ import annotations

import re
import typing as t

from ..exceptions import ConfigurationError
from ..request import Request
from ..response import Response

__all__ = ["CORSMiddleware"]

SAFE_HEADERS = ("Accept", "Accept-Language", "Content-Language", "Content-Type")


class CORSMiddleware:
    """Answer preflights and decorate responses with CORS headers."""

    def __init__(
        self,
        *,
        allow_origins: t.Sequence[str] = (),
        allow_origin_regex: str | None = None,
        allow_methods: t.Sequence[str] = (
            "GET",
            "HEAD",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ),
        allow_headers: t.Sequence[str] = (),
        expose_headers: t.Sequence[str] = (),
        allow_credentials: bool = False,
        max_age: int = 600,
    ) -> None:
        if allow_credentials and "*" in allow_origins:
            raise ConfigurationError(
                "allow_origins=['*'] cannot be combined with allow_credentials=True; "
                "browsers reject it. List the origins explicitly."
            )
        self.allow_origins = set(allow_origins)
        self.allow_all_origins = "*" in self.allow_origins
        self.origin_regex = re.compile(allow_origin_regex) if allow_origin_regex else None
        self.allow_methods = {m.upper() for m in allow_methods}
        self.allow_headers = {h.lower() for h in (*SAFE_HEADERS, *allow_headers)}
        self.allow_all_headers = "*" in allow_headers
        self.expose_headers = list(expose_headers)
        self.allow_credentials = allow_credentials
        self.max_age = max_age

    def _origin_allowed(self, origin: str) -> bool:
        if self.allow_all_origins:
            return True
        if origin in self.allow_origins:
            return True
        return self.origin_regex is not None and self.origin_regex.fullmatch(origin) is not None

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        origin = request.get("origin")
        if origin is None:
            return await call_next()

        response.vary("Origin")
        if not self._origin_allowed(origin):
            # Omit the headers entirely; the browser enforces the block.
            return await call_next()

        allowed_origin = "*" if self.allow_all_origins and not self.allow_credentials else origin
        response.set("Access-Control-Allow-Origin", allowed_origin)
        if self.allow_credentials:
            response.set("Access-Control-Allow-Credentials", "true")
        if self.expose_headers:
            response.set("Access-Control-Expose-Headers", ", ".join(self.expose_headers))

        is_preflight = (
            request.method == "OPTIONS" and request.get("access-control-request-method") is not None
        )
        if not is_preflight:
            return await call_next()

        requested_method = (request.get("access-control-request-method") or "").upper()
        requested_headers = [
            h.strip().lower()
            for h in (request.get("access-control-request-headers") or "").split(",")
            if h.strip()
        ]

        if requested_method not in self.allow_methods:
            response.status(400).text(f"Method {requested_method} is not allowed by CORS")
            return response
        if not self.allow_all_headers:
            rejected = [h for h in requested_headers if h not in self.allow_headers]
            if rejected:
                response.status(400).text(f"Headers not allowed by CORS: {', '.join(rejected)}")
                return response

        response.set("Access-Control-Allow-Methods", ", ".join(sorted(self.allow_methods)))
        response.set(
            "Access-Control-Allow-Headers",
            ", ".join(requested_headers)
            if self.allow_all_headers
            else ", ".join(sorted(self.allow_headers)),
        )
        response.set("Access-Control-Max-Age", str(self.max_age))
        response.status(204).end()
        return response
