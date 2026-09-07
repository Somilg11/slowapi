"""ASGI adapter -- uvicorn, hypercorn, daphne, granian.

Implements the ``lifespan`` and ``http`` scopes.  Sync handlers reached through
this adapter are offloaded to a worker thread by
:class:`~slowfw.middleware.base.AsyncExecutor`, so a blocking handler slows
itself down and nothing else.
"""

from __future__ import annotations

import asyncio
import typing as t

from ..datastructures import MutableHeaders
from ..middleware.base import AsyncExecutor
from ..request import AsyncBodyReader, Request
from ..response import Response

if t.TYPE_CHECKING:  # pragma: no cover
    from ..app import SlowAPI

__all__ = ["ASGIAdapter"]

Receive = t.Callable[[], t.Awaitable[t.MutableMapping[str, t.Any]]]
Send = t.Callable[[t.MutableMapping[str, t.Any]], t.Awaitable[None]]


class ASGIAdapter:
    """Callable ASGI application wrapping a :class:`~slowfw.app.SlowAPI`."""

    def __init__(self, app: SlowAPI) -> None:
        self.app = app

    async def __call__(
        self, scope: t.MutableMapping[str, t.Any], receive: Receive, send: Send
    ) -> None:
        scope_type = scope["type"]
        if scope_type == "lifespan":
            await self._lifespan(receive, send)
        elif scope_type == "http":
            await self._http(scope, receive, send)
        elif scope_type == "websocket":
            # WebSockets are on the roadmap; refuse cleanly rather than hanging.
            await send({"type": "websocket.close", "code": 1011})
        else:  # pragma: no cover - future scope types
            raise NotImplementedError(f"Unsupported ASGI scope type: {scope_type!r}")

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self.app.startup()
                except Exception as exc:
                    await send({"type": "lifespan.startup.failed", "message": repr(exc)})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                try:
                    await self.app.shutdown()
                except Exception as exc:
                    await send({"type": "lifespan.shutdown.failed", "message": repr(exc)})
                    return
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _http(
        self, scope: t.MutableMapping[str, t.Any], receive: Receive, send: Send
    ) -> None:
        if not self.app._started:
            # Some servers skip lifespan entirely (and test clients often do).
            await self.app.startup()

        loop = asyncio.get_running_loop()
        scope.setdefault("protocol", "asgi")
        request = Request(
            scope,
            AsyncBodyReader(receive, loop),
            app=self.app,
            max_body_size=self.app.max_body_size,
        )
        response = Response()

        result = await self.app.dispatch(request, response, AsyncExecutor(loop), loop)
        await self._write(result, request, send)

    async def _write(self, response: Response, request: Request, send: Send) -> None:
        headers = MutableHeaders(response.raw_headers()).encode()
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": headers,
            }
        )

        bodyless = request.method == "HEAD" or response.status_code in (204, 304)
        if bodyless:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        elif not response.is_streaming:
            await send({"type": "http.response.body", "body": response.body, "more_body": False})
        else:
            async for chunk in response.iter_chunks():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        if response.background is not None:
            from ..concurrency import call_maybe_async

            await call_maybe_async(response.background)
