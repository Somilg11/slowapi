"""Exception hierarchy used across SlowAPI.

Every exception raised by the framework inherits from :class:`SlowAPIError`, so
applications can install a single catch-all handler.  Exceptions that are meant
to become HTTP responses inherit from :class:`HTTPException`.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any


class SlowAPIError(Exception):
    """Base class for every SlowAPI exception."""


class ConfigurationError(SlowAPIError):
    """Raised when the application is wired up incorrectly.

    These are programmer errors detected at import/registration time rather
    than at request time, so they should never reach a client.
    """


class HTTPException(SlowAPIError):
    """An exception that carries an HTTP status code.

    Raising it anywhere inside a handler, middleware, or dependency aborts the
    request and produces a response via the registered error middleware.
    """

    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        *,
        headers: Mapping[str, str] | None = None,
        code: str | None = None,
    ) -> None:
        self.status_code = int(status_code)
        try:
            phrase = HTTPStatus(self.status_code).phrase
        except ValueError:  # non-standard status codes are still allowed
            phrase = "Error"
        self.detail = phrase if detail is None else detail
        self.headers = dict(headers or {})
        #: Stable machine-readable identifier, e.g. ``"not_found"``.
        self.code = code or phrase.lower().replace(" ", "_")
        super().__init__(f"{self.status_code}: {self.detail}")

    def to_dict(self) -> dict[str, Any]:
        """Render the exception as the framework's canonical error body."""
        return {
            "error": {
                "code": self.code,
                "status": self.status_code,
                "message": self.detail,
            }
        }


class BadRequest(HTTPException):
    def __init__(self, detail: Any = None, **kw: Any) -> None:
        super().__init__(400, detail, **kw)


class Unauthorized(HTTPException):
    def __init__(self, detail: Any = None, **kw: Any) -> None:
        kw.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(401, detail, **kw)


class Forbidden(HTTPException):
    def __init__(self, detail: Any = None, **kw: Any) -> None:
        super().__init__(403, detail, **kw)


class NotFound(HTTPException):
    def __init__(self, detail: Any = None, **kw: Any) -> None:
        super().__init__(404, detail, **kw)


class MethodNotAllowed(HTTPException):
    def __init__(self, allowed: list[str], detail: Any = None, **kw: Any) -> None:
        headers = dict(kw.pop("headers", None) or {})
        headers.setdefault("Allow", ", ".join(sorted(allowed)))
        super().__init__(405, detail, headers=headers, **kw)


class PayloadTooLarge(HTTPException):
    def __init__(self, detail: Any = None, **kw: Any) -> None:
        super().__init__(413, detail, **kw)


class UnsupportedMediaType(HTTPException):
    def __init__(self, detail: Any = None, **kw: Any) -> None:
        super().__init__(415, detail, **kw)


class TooManyRequests(HTTPException):
    def __init__(self, retry_after: int | None = None, detail: Any = None, **kw: Any) -> None:
        headers = dict(kw.pop("headers", None) or {})
        if retry_after is not None:
            headers.setdefault("Retry-After", str(int(retry_after)))
        super().__init__(429, detail, headers=headers, **kw)


class ServiceUnavailable(HTTPException):
    def __init__(self, detail: Any = None, retry_after: int | None = None, **kw: Any) -> None:
        headers = dict(kw.pop("headers", None) or {})
        if retry_after is not None:
            headers.setdefault("Retry-After", str(int(retry_after)))
        super().__init__(503, detail, headers=headers, **kw)


class GatewayTimeout(HTTPException):
    """The handler took longer than the deadline it was given."""

    def __init__(self, detail: Any = None, **kw: Any) -> None:
        super().__init__(504, detail, **kw)


class ValidationError(HTTPException):
    """Raised when request data does not satisfy a handler's declared types."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(422, "Request validation failed", code="validation_error")

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["error"]["details"] = self.errors
        return payload


class WebSocketDisconnect(SlowAPIError):
    """Raised inside a WebSocket handler when the peer goes away."""

    def __init__(self, code: int = 1000, reason: str = "") -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"WebSocket disconnected ({code}) {reason}".strip())
