"""WSGI adapter -- gunicorn sync workers, uWSGI, mod_wsgi, ``wsgiref``.

The interesting part is :meth:`WSGIAdapter._choose_executor`.  When every
participant in a route's chain is synchronous, the whole dispatch coroutine is
stepped to completion in the calling thread with no event loop involved at all
(see :func:`slowapi.concurrency.drive`).  Only when user code is actually
``async def`` does the request hop onto the shared background loop.
"""

from __future__ import annotations

import typing as t

from ..concurrency import drive, run_coroutine_sync
from ..exceptions import HTTPException
from ..middleware.base import AsyncExecutor, SyncExecutor
from ..request import Request, SyncBodyReader
from ..response import Response

if t.TYPE_CHECKING:  # pragma: no cover
    from ..app import SlowAPI

__all__ = ["WSGIAdapter", "build_scope"]

StartResponse = t.Callable[[str, list[tuple[str, str]]], t.Any]

#: ``environ`` keys that are headers without the ``HTTP_`` prefix.
_UNPREFIXED = {"CONTENT_TYPE": "content-type", "CONTENT_LENGTH": "content-length"}


def build_scope(environ: t.Mapping[str, t.Any]) -> dict[str, t.Any]:
    """Translate a WSGI ``environ`` into SlowAPI's protocol-neutral scope."""
    headers: list[tuple[bytes, bytes]] = []
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").lower()
        elif key in _UNPREFIXED:
            name = _UNPREFIXED[key]
        else:
            continue
        if value not in (None, ""):
            headers.append((name.encode("latin-1"), str(value).encode("latin-1")))

    port = environ.get("REMOTE_PORT")
    client = (environ.get("REMOTE_ADDR", ""), int(port) if port else None)

    return {
        "type": "http",
        "protocol": "wsgi",
        "http_version": environ.get("SERVER_PROTOCOL", "HTTP/1.1").split("/")[-1],
        "method": environ.get("REQUEST_METHOD", "GET").upper(),
        "scheme": environ.get("wsgi.url_scheme", "http"),
        "path": environ.get("PATH_INFO", "/") or "/",
        "raw_path": environ.get("RAW_URI", environ.get("PATH_INFO", "/")),
        "root_path": environ.get("SCRIPT_NAME", ""),
        "query_string": environ.get("QUERY_STRING", "").encode("latin-1"),
        "headers": headers,
        "client": client,
        "server": (environ.get("SERVER_NAME", ""), int(environ.get("SERVER_PORT", 0) or 0)),
        "wsgi_environ": environ,
    }


class WSGIAdapter:
    """Callable WSGI application wrapping a :class:`~slowapi.app.SlowAPI`."""

    def __init__(self, app: SlowAPI) -> None:
        self.app = app

    def __call__(
        self, environ: t.MutableMapping[str, t.Any], start_response: StartResponse
    ) -> t.Iterable[bytes]:
        self._ensure_started()

        scope = build_scope(environ)
        raw_length = str(environ.get("CONTENT_LENGTH") or "")
        reader = SyncBodyReader(
            t.cast("t.BinaryIO", environ["wsgi.input"]),
            int(raw_length) if raw_length.isdigit() else None,
        )
        request = Request(scope, reader, app=self.app, max_body_size=self.app.max_body_size)
        response = Response()

        fully_sync = self._choose_executor(request)
        if fully_sync:
            result = drive(self.app.dispatch(request, response, SyncExecutor()))
        else:
            result = run_coroutine_sync(self.app.dispatch(request, response, AsyncExecutor()))

        return self._write(result, request, start_response)

    def _ensure_started(self) -> None:
        if self.app._started:
            return
        # WSGI has no lifespan protocol, so startup happens on first request.
        if self.app.startup_is_sync():
            drive(self.app.startup())
        else:
            run_coroutine_sync(self.app.startup())

    def _choose_executor(self, request: Request) -> bool:
        """Return True when this request can take the loop-free fast path."""
        try:
            route, _ = self.app.router.match(request.method, request.path)
        except HTTPException:
            # No route matched, so only middleware will run.  The framework's
            # own middleware is `async def` but never actually suspends, which
            # is exactly what drive() requires; only user middleware can force
            # the loop.
            from ..app import _maybe_async

            return not any(_maybe_async(m) for m in self.app._user_middlewares)
        return self.app._prepare(route).fully_sync

    def _write(
        self, response: Response, request: Request, start_response: StartResponse
    ) -> t.Iterable[bytes]:
        headers = response.raw_headers()
        start_response(response.status_line, [(k, v) for k, v in headers])

        if request.method == "HEAD" or response.status_code in (204, 304):
            return [b""]
        if not response.is_streaming:
            return [response.body]
        return _StreamingIterable(response)


class _StreamingIterable:
    """Lazily yields chunks and runs the background task once drained."""

    __slots__ = ("_response",)

    def __init__(self, response: Response) -> None:
        self._response = response

    def __iter__(self) -> t.Iterator[bytes]:
        yield from self._response.iter_chunks_sync()

    def close(self) -> None:
        background = getattr(self._response, "background", None)
        if background is not None:
            background()
