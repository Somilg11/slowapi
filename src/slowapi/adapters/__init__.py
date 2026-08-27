"""Protocol adapters.

Each adapter does exactly three things: normalise the server's native request
representation into a scope, choose an execution strategy, and write the
resulting :class:`~slowapi.response.Response` back out.  All routing,
injection, and middleware logic lives above them and is shared.
"""

from __future__ import annotations

from .asgi import ASGIAdapter
from .wsgi import WSGIAdapter

__all__ = ["ASGIAdapter", "WSGIAdapter"]
