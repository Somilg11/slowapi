"""A deadline for request handling.

A handler that never returns is the quietest kind of outage: connections pile
up, the worker pool drains, and health checks keep passing because the process
is alive.  A deadline turns that into a 504 you can alert on.

    app.use(TimeoutMiddleware(seconds=10))

Honesty about what this can and cannot do, because the difference matters when
you are choosing a value:

* On the **async path** the deadline is real.  ``asyncio.wait_for`` cancels the
  handler's task, so an ``await`` that is waiting on a slow database gets a
  ``CancelledError`` and the resources behind it unwind.
* On the **loop-free sync path** there is no scheduler to interrupt, and Python
  cannot pre-empt a running function.  A ``def`` handler stuck in a blocking
  call runs to completion; the middleware notices afterwards and returns 504
  instead of the late response.  That still bounds what the *client* waits for
  only if the server itself is not the thing blocked, so a sync deployment
  wants a server-level timeout (``gunicorn --timeout``) underneath this one.

Keeping a deadline that works differently per protocol would break protocol
parity, so the observable contract is the same either way: a request that
exceeds ``seconds`` produces 504 and never the handler's own response.
"""

from __future__ import annotations

import asyncio
import time
import typing as t

from ..decorators import never_suspends
from ..exceptions import GatewayTimeout
from ..logging import get_logger

if t.TYPE_CHECKING:  # pragma: no cover
    from ..request import Request
    from ..response import Response

__all__ = ["TimeoutMiddleware"]

logger = get_logger("slowapi.timeout")

CallNext = t.Callable[[], t.Any]


@never_suspends
class TimeoutMiddleware:
    """Fail a request with 504 once it passes its deadline.

    :param seconds:
        The default deadline, in seconds.
    :param per_path:
        Overrides keyed by the route's registered path template -- not the
        request path -- so ``{"/reports/{id}": 60.0}`` covers every id.  A
        value of ``None`` exempts the route entirely, which is how you keep a
        deliberately long-running export from being cut off.
    :param detail:
        The message sent to the client.  Kept generic by default: how long a
        handler is allowed to run is not a client's business.
    """

    def __init__(
        self,
        seconds: float = 30.0,
        *,
        per_path: t.Mapping[str, float | None] | None = None,
        detail: str = "The server took too long to produce a response.",
    ) -> None:
        if seconds <= 0:
            raise ValueError("TimeoutMiddleware(seconds=) must be positive")
        self.seconds = seconds
        self.per_path = dict(per_path or {})
        self.detail = detail

    def _deadline(self, request: Request) -> float | None:
        """The deadline for this request, or ``None`` to let it run.

        Middleware runs before routing, so a ``per_path`` override has to match
        the route itself.  Matching is a trie walk over pre-compiled segments
        and the result is cached on the router, so paying for it twice is
        cheaper than threading routing state through the onion.
        """
        if not self.per_path:
            return self.seconds
        app = getattr(request, "app", None)
        router = getattr(app, "router", None)
        if router is None:
            return self.seconds
        try:
            route, _ = router.match(request.method, request.path)
        except Exception:
            # No route, or the wrong method.  The 404/405 is not ours to raise;
            # let the chain reach the handler that does.
            return self.seconds
        if route.path in self.per_path:
            return self.per_path[route.path]
        return self.seconds

    async def dispatch(self, request: Request, response: Response, call_next: CallNext) -> t.Any:
        deadline = self._deadline(request)
        if deadline is None:
            return await call_next()

        try:
            loop_running = asyncio.get_running_loop() is not None
        except RuntimeError:
            loop_running = False

        if loop_running:
            try:
                return await asyncio.wait_for(_await_next(call_next), timeout=deadline)
            except (TimeoutError, asyncio.TimeoutError):
                self._log(request, deadline, deadline)
                raise GatewayTimeout(self.detail) from None

        # Loop-free fast path: nothing exists that could interrupt a running
        # ``def``, so the handler finishes and the deadline is enforced after
        # the fact.  Whatever it produced is discarded -- a response the client
        # gave up waiting for is worse than an honest 504.
        started = time.monotonic()
        result = await _await_next(call_next)
        elapsed = time.monotonic() - started
        if elapsed > deadline:
            self._log(request, deadline, elapsed)
            raise GatewayTimeout(self.detail) from None
        return result

    def _log(self, request: Request, deadline: float, elapsed: float) -> None:
        logger.warning(
            "request exceeded deadline",
            extra={
                "method": request.method,
                "path": request.path,
                "deadline": deadline,
                "elapsed": round(elapsed, 4),
            },
        )


async def _await_next(call_next: CallNext) -> t.Any:
    """Await ``call_next`` whether it returns a coroutine or a plain value."""
    result = call_next()
    if hasattr(result, "__await__"):
        return await result
    return result
