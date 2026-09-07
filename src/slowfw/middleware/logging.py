"""Request correlation ids and structured access logs.

Every request gets an id -- reused from the inbound header when a gateway
already assigned one -- which is attached to the log line, echoed in the
response header, and embedded in any error body.  That single thread is what
makes a production incident debuggable.
"""

from __future__ import annotations

import time
import typing as t
import uuid

from ..exceptions import HTTPException
from ..request import Request
from ..response import Response

__all__ = ["AccessLogMiddleware", "RequestIDMiddleware"]


class RequestIDMiddleware:
    """Assign or propagate a request id."""

    def __init__(
        self,
        header: str = "X-Request-ID",
        *,
        trust_inbound: bool = True,
        max_length: int = 128,
    ) -> None:
        self.header = header
        #: Reuse a client-supplied id.  Safe because the id is only ever logged
        #: and echoed, never used for authorisation.
        self.trust_inbound = trust_inbound
        self.max_length = max_length

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        incoming = request.get(self.header) if self.trust_inbound else None
        if incoming and len(incoming) <= self.max_length and incoming.isprintable():
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex
        request.scope["request_id"] = request_id
        request.state.request_id = request_id
        result = await call_next()
        target = result if isinstance(result, Response) else response
        target.set(self.header, request_id)
        return result


class AccessLogMiddleware:
    """Emit one structured line per request, after the response is built."""

    def __init__(
        self,
        logger: t.Any = None,
        *,
        skip_paths: t.Sequence[str] = ("/health", "/metrics", "/favicon.ico"),
        slow_ms: float = 1000.0,
    ) -> None:
        if logger is None:
            from ..logging import get_logger

            logger = get_logger("slowfw.access")
        self.logger = logger
        self.skip_paths = set(skip_paths)
        #: Requests slower than this are logged at WARNING.
        self.slow_ms = slow_ms

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        if request.path in self.skip_paths:
            return await call_next()

        started = time.perf_counter()
        status = 500
        try:
            result = await call_next()
            target = result if isinstance(result, Response) else response
            status = target.status_code
            return result
        except HTTPException as exc:
            # The error middleware sits outside this one, so a deliberate 404
            # would otherwise be logged as a 500. Record the real status.
            status = exc.status_code
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            payload = {
                "method": request.method,
                "path": request.path,
                "status": status,
                "durationMs": round(duration_ms, 2),
                "requestId": request.request_id,
                "ip": request.ip,
            }
            if status >= 500:
                self.logger.error("request", extra=payload)
            elif status >= 400 or duration_ms >= self.slow_ms:
                self.logger.warning("request", extra=payload)
            else:
                self.logger.info("request", extra=payload)
