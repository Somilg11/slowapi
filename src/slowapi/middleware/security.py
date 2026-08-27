"""Security response headers, on by default with sensible values."""

from __future__ import annotations

import typing as t

from ..request import Request
from ..response import Response

__all__ = ["SecurityHeadersMiddleware", "TrustedHostMiddleware"]

DEFAULT_CSP = (
    "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
    "object-src 'none'; img-src 'self' data:; form-action 'self'"
)


class SecurityHeadersMiddleware:
    """Set the headers every HTML-serving app should have.

    Defaults are chosen to be safe for an API.  A server-rendered app that
    loads a CDN will need to relax ``content_security_policy``; that is a
    deliberate decision the application should make explicitly.
    """

    def __init__(
        self,
        *,
        content_security_policy: str | None = DEFAULT_CSP,
        frame_options: str | None = "DENY",
        content_type_options: bool = True,
        referrer_policy: str | None = "strict-origin-when-cross-origin",
        hsts_seconds: int | None = None,
        hsts_subdomains: bool = True,
        permissions_policy: str | None = "geolocation=(), microphone=(), camera=()",
        cross_origin_opener_policy: str | None = "same-origin",
    ) -> None:
        self.csp = content_security_policy
        self.frame_options = frame_options
        self.content_type_options = content_type_options
        self.referrer_policy = referrer_policy
        self.hsts_seconds = hsts_seconds
        self.hsts_subdomains = hsts_subdomains
        self.permissions_policy = permissions_policy
        self.coop = cross_origin_opener_policy

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        result = await call_next()
        target = result if isinstance(result, Response) else response

        if self.csp:
            target.headers.setdefault("content-security-policy", self.csp)
        if self.frame_options:
            target.headers.setdefault("x-frame-options", self.frame_options)
        if self.content_type_options:
            target.headers.setdefault("x-content-type-options", "nosniff")
        if self.referrer_policy:
            target.headers.setdefault("referrer-policy", self.referrer_policy)
        if self.permissions_policy:
            target.headers.setdefault("permissions-policy", self.permissions_policy)
        if self.coop:
            target.headers.setdefault("cross-origin-opener-policy", self.coop)
        # HSTS over plain HTTP is ignored by browsers and misleads auditors.
        if self.hsts_seconds and request.scheme == "https":
            value = f"max-age={self.hsts_seconds}"
            if self.hsts_subdomains:
                value += "; includeSubDomains"
            target.headers.setdefault("strict-transport-security", value)
        return result


class TrustedHostMiddleware:
    """Reject requests whose ``Host`` header is not in the allow-list.

    Without this, an attacker can poison absolute URLs the app generates
    (password-reset links being the classic case) by sending a forged Host.
    """

    def __init__(self, allowed_hosts: t.Sequence[str], *, www_redirect: bool = True) -> None:
        self.allowed = [h.lower() for h in allowed_hosts]
        self.allow_any = "*" in self.allowed
        self.www_redirect = www_redirect

    def _matches(self, host: str) -> bool:
        for pattern in self.allowed:
            if pattern.startswith("*.") and (host == pattern[2:] or host.endswith(pattern[1:])):
                return True
            if host == pattern:
                return True
        return False

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        if self.allow_any:
            return await call_next()
        host = (request.get("host") or "").split(":")[0].lower()
        if self._matches(host):
            return await call_next()
        if self.www_redirect and self._matches("www." + host):
            return response.redirect(str(request.url.replace(netloc="www." + host)), 307)
        return response.status(400).text("Invalid Host header")
