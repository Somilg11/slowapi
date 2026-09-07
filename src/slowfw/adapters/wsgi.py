"""WSGI adapter -- gunicorn sync workers, uWSGI, mod_wsgi, ``wsgiref``.

The interesting part is :meth:`WSGIAdapter._choose_executor`.  When every
participant in a route's chain is synchronous, the whole dispatch coroutine is
stepped to completion in the calling thread with no event loop involved at all
(see :func:`slowfw.concurrency.drive`).  Only when user code is actually
``async def`` does the request hop onto the shared background loop.
"""

from __future__ import annotations

import atexit
import os
import signal
import typing as t

from ..concurrency import call_maybe_sync, drive, run_coroutine_sync
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
    """Callable WSGI application wrapping a :class:`~slowfw.app.SlowAPI`."""

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
        # ...and no shutdown message either.  Without this, a gunicorn worker
        # exiting would skip every ``on_event("shutdown")`` hook and leave the
        # container's singletons undisposed -- pools left open, temp files left
        # on disk.  ASGI gets this from ``lifespan.shutdown``; atexit is the
        # closest equivalent WSGI offers.
        self._register_atexit()

    def _register_atexit(self) -> None:
        """Arrange for shutdown to run however this process is stopped.

        Two mechanisms, because neither covers the other's case:

        ``atexit``
            Fires on a clean interpreter exit.  This is the usual path -- a
            scheduler signals the gunicorn arbiter, the arbiter stops its
            workers with ``sys.exit()``, and ``atexit`` runs.

        A ``SIGTERM``/``SIGINT`` handler
            Python's default ``SIGTERM`` handler terminates the process without
            running ``atexit`` at all.  Anything that signals a *worker*
            directly -- a supervisor reaping a stuck one, a stray ``pkill`` --
            would otherwise skip every shutdown hook.

        The signal handler runs after the previous one, so a server's own
        graceful drain still happens first and the container is not disposed
        out from under an in-flight request.  ``shutdown()`` is idempotent, so
        both mechanisms firing is harmless.
        """
        if self.app._atexit_registered:
            return
        self.app._atexit_registered = True
        atexit.register(self._run_shutdown)
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, self._make_handler(signum, previous))
            except (ValueError, OSError, RuntimeError):
                # Not the main thread, or a platform without this signal.  The
                # atexit hook still covers the clean-exit path.
                return

    def _make_handler(self, signum: int, previous: t.Any) -> t.Callable[..., None]:
        def handler(received: int, frame: t.Any) -> None:
            # Delegate first: the server drains in-flight requests here, and
            # may not return at all if it exits from inside its own handler --
            # in which case atexit picks the shutdown up.
            try:
                if callable(previous):
                    previous(received, frame)
            finally:
                self._run_shutdown()
            if previous is signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        return handler

    def _run_shutdown(self) -> None:
        """Run the application's shutdown once.  Must never raise.

        Called from an ``atexit`` hook and from a signal handler, so raising
        here would replace whatever exit was already under way with a confusing
        one.
        """
        if not self.app._started:
            return
        try:
            call_maybe_sync(self.app.shutdown)
        except Exception:  # pragma: no cover - best effort at process exit
            self.app.logger.exception("shutdown hook failed")

    def _choose_executor(self, request: Request) -> bool:
        """Return True when this request can take the loop-free fast path."""
        try:
            route, _ = self.app.match_request(request)
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

        bodyless = request.method == "HEAD" or response.status_code in (204, 304)
        return _ResponseIterable(response, bodyless=bodyless)


class _ResponseIterable:
    """The WSGI return value: yields the body, then runs the background task.

    Both branches go through here so that ``response.background`` fires for
    buffered and streaming responses alike -- ASGI runs it unconditionally, and
    protocol parity is the first invariant.  ``close()`` is the right hook: the
    WSGI spec requires servers to call it once the body has been consumed, so
    the task runs after the client has the response rather than before.
    """

    __slots__ = ("_bodyless", "_closed", "_response")

    def __init__(self, response: Response, *, bodyless: bool = False) -> None:
        self._response = response
        self._bodyless = bodyless
        self._closed = False

    def __iter__(self) -> t.Iterator[bytes]:
        if self._bodyless:
            yield b""
        elif self._response.is_streaming:
            yield from self._response.iter_chunks_sync()
        else:
            yield self._response.body

    def close(self) -> None:
        # Servers are allowed to call close() more than once; a background task
        # that ran twice would be a surprising way to learn that.
        if self._closed:
            return
        self._closed = True
        background = getattr(self._response, "background", None)
        if background is None:
            return
        # ``BackgroundTasks`` offers a synchronous runner so that queued plain
        # ``def`` tasks stay off the event loop -- see slowfw.background.
        # Anything else goes through ``call_maybe_sync``, which hops an
        # ``async def`` onto the shared loop rather than leaving an un-awaited
        # coroutine behind.
        runner = getattr(background, "run_sync", None)
        if runner is not None:
            runner()
        else:
            call_maybe_sync(background)
