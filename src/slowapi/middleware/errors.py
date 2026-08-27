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

import inspect
import traceback
import typing as t

from ..exceptions import HTTPException
from ..request import Request
from ..response import Response

__all__ = ["ErrorMiddleware", "ExceptionHandlers"]

Handler = t.Callable[[Request, BaseException, Response], t.Any]


#: Parameter names that identify each role when the annotation does not.
_REQUEST_NAMES = frozenset({"request", "req", "r"})
_RESPONSE_NAMES = frozenset({"response", "res"})
_EXCEPTION_NAMES = frozenset({"exception", "exc", "error", "err", "e"})

#: Cache keyed by the handler function, since the answer never changes.
_ARGUMENT_ORDER: dict[t.Any, tuple[str, ...]] = {}


def _annotation_name(annotation: t.Any) -> str:
    """The annotation's bare name.

    ``from __future__ import annotations`` turns every annotation into a
    string, so comparing against the class alone would work in some modules and
    not others -- a difference nobody should have to debug.
    """
    if isinstance(annotation, str):
        return annotation.rsplit(".", 1)[-1].strip("\"'")
    return getattr(annotation, "__name__", "")


def _is_request(annotation: t.Any) -> bool:
    return annotation is Request or _annotation_name(annotation) == "Request"


def _is_response(annotation: t.Any) -> bool:
    return annotation is Response or _annotation_name(annotation) == "Response"


def _is_exception(annotation: t.Any) -> bool:
    if isinstance(annotation, type):
        return issubclass(annotation, BaseException)
    name = _annotation_name(annotation)
    return name.endswith(("Error", "Exception")) or name in ("HTTPException", "BaseException")


def _argument_order(handler: t.Any) -> tuple[str, ...]:
    """Work out which of ``request``, ``exception`` and ``response`` go where.

    Handlers used to be positional -- ``(req, exc, res)`` -- which reads as an
    arbitrary rule next to the ``(req, res)`` order used by every handler and
    middleware in the framework, and getting it backwards produced a confusing
    ``AttributeError`` deep inside error handling.  Roles are matched by
    annotation first, then by parameter name, so both orders work and neither
    has to be memorised.  Anything unrecognised keeps its position, which is
    what makes the historical order still correct.
    """
    cached = _ARGUMENT_ORDER.get(handler)
    if cached is not None:
        return cached

    try:
        parameters = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):  # pragma: no cover - builtins
        return ("request", "exception", "response")
    if parameters and parameters[0].name in ("self", "cls"):
        parameters = parameters[1:]

    positional = ["request", "exception", "response"]
    roles: list[str | None] = [None] * len(parameters)
    for index, parameter in enumerate(parameters):
        annotation = parameter.annotation
        name = parameter.name.lower()
        if _is_request(annotation) or name in _REQUEST_NAMES:
            roles[index] = "request"
        elif _is_response(annotation) or name in _RESPONSE_NAMES:
            roles[index] = "response"
        elif name in _EXCEPTION_NAMES or _is_exception(annotation):
            roles[index] = "exception"

    # Fill anything unrecognised from the historical order, skipping roles
    # already claimed by name so a partial match cannot duplicate one.
    remaining = [role for role in positional if role not in roles]
    for index, role in enumerate(roles):
        if role is None and remaining:
            roles[index] = remaining.pop(0)

    order = tuple(role or "request" for role in roles)
    _ARGUMENT_ORDER[handler] = order
    return order


def call_handler(handler: t.Any, request: Request, exc: BaseException, response: Response) -> t.Any:
    """Invoke a user exception handler with its arguments in its own order."""
    values = {"request": request, "exception": exc, "response": response}
    return handler(*(values[role] for role in _argument_order(handler)))


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

            result = await maybe_await(call_handler(custom, request, exc, response))
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

            result = await maybe_await(call_handler(custom, request, exc, response))
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
