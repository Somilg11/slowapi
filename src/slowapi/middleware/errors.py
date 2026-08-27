"""Turn exceptions into responses, and decide what a client is told.

Two rules drive everything here:

* An :class:`~slowapi.exceptions.HTTPException` is a *deliberate* answer, so
  its detail is safe to show.
* Anything else is a bug.  The traceback goes to the log with a request id;
  the client gets that id and nothing more, because stack traces leak file
  paths, library versions and sometimes credentials.

In debug mode the second rule is relaxed so you can see what broke.
"""

from __future__ import annotations

import traceback
import typing as t

from ..exceptions import HTTPException
from ..request import Request
from ..response import Response

__all__ = ["ErrorMiddleware", "ExceptionHandlers"]

Handler = t.Callable[[Request, BaseException, Response], t.Any]


class ExceptionHandlers:
    """A registry mapping exception types (or status codes) to handlers."""

    def __init__(self) -> None:
        self._by_type: dict[type, Handler] = {}
        self._by_status: dict[int, Handler] = {}

    def add(self, key: type | int, handler: Handler) -> None:
        if isinstance(key, int):
            self._by_status[key] = handler
        else:
            self._by_type[key] = handler

    def find(self, exc: BaseException) -> Handler | None:
        for klass in type(exc).__mro__:
            if klass in self._by_type:
                return self._by_type[klass]
        if isinstance(exc, HTTPException) and exc.status_code in self._by_status:
            return self._by_status[exc.status_code]
        return None

    def __len__(self) -> int:
        return len(self._by_type) + len(self._by_status)


class ErrorMiddleware:
    """Outermost middleware; nothing above it should ever raise."""

    def __init__(
        self,
        *,
        debug: bool = False,
        handlers: ExceptionHandlers | None = None,
        logger: t.Any = None,
    ) -> None:
        self.debug = debug
        # `or` would discard an empty-but-shared registry the app fills in later.
        self.handlers = handlers if handlers is not None else ExceptionHandlers()
        self.logger = logger

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        try:
            return await call_next()
        except HTTPException as exc:
            return await self._handle_http(request, exc, response)
        except Exception as exc:
            return await self._handle_unexpected(request, exc, response)

    async def _handle_http(
        self, request: Request, exc: HTTPException, response: Response
    ) -> Response:
        custom = self.handlers.find(exc)
        if custom is not None:
            from ..concurrency import maybe_await

            result = await maybe_await(custom(request, exc, response))
            if isinstance(result, Response):
                return result
            if result is not None:
                return response.json(result, exc.status_code)
            return response

        if self.logger is not None and exc.status_code >= 500:
            self.logger.error(
                "http_exception", extra={"status": exc.status_code, "detail": exc.detail}
            )
        payload = exc.to_dict()
        if request.request_id:
            payload["error"]["requestId"] = request.request_id
        response.json(payload, exc.status_code)
        for name, value in exc.headers.items():
            response.set(name, value)
        return response

    async def _handle_unexpected(
        self, request: Request, exc: Exception, response: Response
    ) -> Response:
        custom = self.handlers.find(exc)
        if custom is not None:
            from ..concurrency import maybe_await

            result = await maybe_await(custom(request, exc, response))
            if isinstance(result, Response):
                return result
            if result is not None:
                return response.json(result, 500)
            return response

        if self.logger is not None:
            self.logger.exception(
                "unhandled_exception",
                extra={
                    "path": request.path,
                    "method": request.method,
                    "requestId": request.request_id,
                },
            )
        else:  # pragma: no cover - only when logging is disabled entirely
            traceback.print_exc()

        body: dict[str, t.Any] = {
            "error": {
                "code": "internal_server_error",
                "status": 500,
                "message": "Internal Server Error",
            }
        }
        if request.request_id:
            body["error"]["requestId"] = request.request_id
        if self.debug:
            body["error"]["message"] = f"{type(exc).__name__}: {exc}"
            body["error"]["traceback"] = traceback.format_exc().splitlines()
        return response.json(body, 500)
