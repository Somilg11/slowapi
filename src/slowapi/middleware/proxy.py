"""Trust ``X-Forwarded-*`` headers, but only from hops you actually trust.

Reverse proxies rewrite the client address, so an app behind one sees the
proxy's IP unless it reads the forwarded headers.  Reading them unconditionally
lets any client spoof its own IP -- which silently breaks rate limiting, audit
logs, and IP allow-lists.  This middleware only honours the headers when the
immediate peer is in ``trusted_hosts``.
"""

from __future__ import annotations

import ipaddress
import typing as t

from ..request import Request
from ..response import Response

__all__ = ["ProxyHeadersMiddleware"]


class ProxyHeadersMiddleware:
    """Rewrite scheme, host and client from forwarded headers."""

    def __init__(self, trusted_hosts: t.Sequence[str] = ("127.0.0.1", "::1")) -> None:
        self.trust_any = "*" in trusted_hosts
        self.literal: set[str] = set()
        self.networks: list[t.Any] = []
        for entry in trusted_hosts:
            if entry == "*":
                continue
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self.literal.add(entry)

    def _trusted(self, host: str | None) -> bool:
        if self.trust_any:
            return True
        if host is None:
            return False
        if host in self.literal:
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(address in network for network in self.networks)

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        client = request.client
        if not self._trusted(client.host if client else None):
            return await call_next()

        request.scope["_trusted_proxy"] = True
        forwarded_proto = request.get("x-forwarded-proto")
        if forwarded_proto:
            request.scope["scheme"] = forwarded_proto.split(",")[0].strip()
        forwarded_for = request.get("x-forwarded-for")
        if forwarded_for:
            peer = forwarded_for.split(",")[0].strip()
            if peer:
                request.scope["client"] = (peer, None)
        forwarded_port = request.get("x-forwarded-port")
        if forwarded_port and forwarded_port.isdigit():
            request.scope["_forwarded_port"] = int(forwarded_port)
        return await call_next()
