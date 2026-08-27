"""Liveness and readiness endpoints.

Kubernetes, ECS and every load balancer want two different answers, and giving
them the same one is a common way to make an outage worse:

``/healthz`` (liveness)
    Is this process still working?  Answers from the process alone and never
    touches a dependency.  A liveness probe that checks the database restarts
    every pod in the fleet the moment the database hiccups.

``/readyz`` (readiness)
    Should this process receive traffic *right now*?  Runs the registered
    checks, so a pod whose database connection is gone is taken out of
    rotation and left running -- which is what you want, because it can come
    back without a restart.

Usage::

    health = HealthCheck()
    health.add("database", lambda: db.execute("SELECT 1"))
    health.add("cache", cache.ping, critical=False)
    health.install(app)

A check passes by returning without raising; returning ``False`` also fails it,
so one-liners like ``lambda: pool.is_open`` read naturally.  Non-critical checks
are reported but never make the endpoint fail -- degraded is not down.
"""

from __future__ import annotations

import time
import typing as t
from dataclasses import dataclass, field

from .concurrency import call_maybe_sync, maybe_await
from .response import Response

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import SlowAPI

__all__ = ["Check", "HealthCheck"]


@dataclass(slots=True)
class Check:
    """One registered dependency probe."""

    name: str
    probe: t.Callable[..., t.Any]
    critical: bool = True
    #: Failures below this many consecutive strikes are still reported "ok".
    #: Stops a single blip from flapping a pod out of rotation.
    tolerate: int = 0
    _strikes: int = field(default=0, repr=False)

    def record(self, ok: bool) -> bool:
        """Update the strike count and return the *reported* health."""
        self._strikes = 0 if ok else self._strikes + 1
        return ok or self._strikes <= self.tolerate


class HealthCheck:
    """A registry of probes plus the two endpoints that report them.

    :param liveness_path: Path for the process-only check.
    :param readiness_path: Path for the dependency check.
    :param include_details:
        Whether the readiness body names each check and its duration.  On by
        default because that is what makes a failing probe diagnosable from the
        outside; turn it off if the endpoint is reachable from the internet and
        you would rather not publish your dependency list.
    """

    def __init__(
        self,
        *,
        liveness_path: str = "/healthz",
        readiness_path: str = "/readyz",
        include_details: bool = True,
    ) -> None:
        self.liveness_path = liveness_path
        self.readiness_path = readiness_path
        self.include_details = include_details
        self.checks: list[Check] = []

    def add(
        self,
        name: str,
        probe: t.Callable[..., t.Any],
        *,
        critical: bool = True,
        tolerate: int = 0,
    ) -> HealthCheck:
        """Register a probe.  Sync or ``async def``, either works."""
        if any(check.name == name for check in self.checks):
            raise ValueError(f"A health check named {name!r} is already registered")
        self.checks.append(Check(name, probe, critical=critical, tolerate=tolerate))
        return self

    def check(self, name: str, **kwargs: t.Any) -> t.Callable[[t.Callable[..., t.Any]], t.Any]:
        """Decorator form of :meth:`add`."""

        def decorator(probe: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
            self.add(name, probe, **kwargs)
            return probe

        return decorator

    # ------------------------------------------------------------- running

    async def run(self) -> tuple[bool, dict[str, t.Any]]:
        """Run every probe and return ``(ready, report)``."""
        results: dict[str, t.Any] = {}
        ready = True
        for check in self.checks:
            started = time.perf_counter()
            error: str | None = None
            try:
                outcome = await maybe_await(check.probe())
                ok = outcome is not False
            except Exception as exc:
                ok = False
                # The class name alone: a probe's exception text can carry
                # connection strings, and this body may be world-readable.
                error = type(exc).__name__
            reported = check.record(ok)
            if not reported and check.critical:
                ready = False
            results[check.name] = {
                "status": "ok" if reported else "fail",
                "critical": check.critical,
                "took_ms": round((time.perf_counter() - started) * 1000, 2),
                **({"error": error} if error else {}),
            }
        return ready, results

    def run_sync(self) -> tuple[bool, dict[str, t.Any]]:
        """Synchronous entry point, so sync probes keep the loop-free path."""
        results: dict[str, t.Any] = {}
        ready = True
        for check in self.checks:
            started = time.perf_counter()
            error: str | None = None
            try:
                ok = call_maybe_sync(check.probe) is not False
            except Exception as exc:
                ok = False
                error = type(exc).__name__
            reported = check.record(ok)
            if not reported and check.critical:
                ready = False
            results[check.name] = {
                "status": "ok" if reported else "fail",
                "critical": check.critical,
                "took_ms": round((time.perf_counter() - started) * 1000, 2),
                **({"error": error} if error else {}),
            }
        return ready, results

    # ------------------------------------------------------------ wiring

    def install(self, app: SlowAPI) -> HealthCheck:
        """Add both endpoints to ``app``.

        Neither is listed in the OpenAPI schema: they are infrastructure, not
        API surface, and a probe endpoint in the published docs invites clients
        to poll it.
        """
        registry = self

        @app.get(self.liveness_path, include_in_schema=False)
        def liveness(res: Response) -> Response:
            # Deliberately answers without consulting a single dependency.
            return res.json({"status": "ok"})

        @app.get(self.readiness_path, include_in_schema=False)
        def readiness(res: Response) -> Response:
            ready, results = registry.run_sync()
            body: dict[str, t.Any] = {"status": "ok" if ready else "fail"}
            if registry.include_details:
                body["checks"] = results
            # 503 rather than 500: the load balancer reads it as "not now",
            # keeps the pod alive and retries.
            return res.status(200 if ready else 503).no_cache().json(body)

        return self
