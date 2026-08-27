#!/usr/bin/env python3
"""Measure SlowAPI's per-request overhead on both protocols.

This measures *framework* cost, not throughput: requests go through the real
adapters in-process, so there is no socket, no server, and no network. Real
applications are dominated by their database, not by this number — but the
number should still be published rather than implied.

    python benchmarks/run.py
    python benchmarks/run.py --iterations 20000 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slowapi import Depends, Query, SlowAPI
from slowapi.concurrency import _LoopThread, shutdown_loop_thread
from slowapi.logging import configure_logging
from slowapi.testing import TestClient

configure_logging("CRITICAL")


@dataclass
class Result:
    name: str
    protocol: str
    per_request_us: float
    p50_us: float
    p99_us: float
    requests_per_second: float


def build_app() -> SlowAPI:
    app = SlowAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/plain")
    def plain(res):
        res.text("ok")

    @app.get("/json")
    def json_route() -> dict:
        return {"ok": True, "n": 1}

    @app.get("/params/{id:int}")
    def params(id: int, limit: int = Query(10, ge=1, le=100)) -> dict:
        return {"id": id, "limit": limit}

    def dependency(offset: int = Query(0, ge=0)) -> dict:
        return {"offset": offset}

    @app.get("/deps")
    def deps(paging: dict = Depends(dependency)) -> dict:
        return paging

    @app.get("/async")
    async def async_route() -> dict:
        return {"ok": True}

    return app


def measure(client: TestClient, path: str, iterations: int) -> tuple[float, float, float]:
    """Return (mean, p50, p99) microseconds per request."""
    for _ in range(min(200, iterations // 10 + 1)):  # warm up
        client.get(path)

    samples: list[float] = []
    batch = 50
    for _ in range(max(1, iterations // batch)):
        started = time.perf_counter()
        for _ in range(batch):
            client.get(path)
        samples.append((time.perf_counter() - started) / batch * 1e6)

    samples.sort()
    return (
        statistics.fmean(samples),
        samples[len(samples) // 2],
        samples[min(len(samples) - 1, int(len(samples) * 0.99))],
    )


ROUTES = [
    ("plain text", "/plain"),
    ("json", "/json"),
    ("typed params", "/params/42?limit=5"),
    ("dependency", "/deps?offset=3"),
    ("async handler", "/async"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    app = build_app()
    results: list[Result] = []

    for protocol in ("wsgi", "asgi"):
        client = TestClient(app, protocol=protocol)
        for name, path in ROUTES:
            mean, p50, p99 = measure(client, path, args.iterations)
            results.append(Result(name, protocol, mean, p50, p99, 1e6 / mean))

    # The claim in the README, asserted rather than asserted-about.
    shutdown_loop_thread()
    TestClient(app, protocol="wsgi").get("/plain")
    loop_free = _LoopThread._instance is None

    if args.as_json:
        print(
            json.dumps(
                {
                    "iterations": args.iterations,
                    "loop_free_fast_path": loop_free,
                    "results": [r.__dict__ for r in results],
                },
                indent=2,
            )
        )
        return 0

    print(f"\nSlowAPI overhead — {args.iterations} iterations per route, in-process\n")
    print(f"{'route':<16} {'protocol':<9} {'mean':>9} {'p50':>9} {'p99':>9} {'req/s':>10}")
    print("-" * 66)
    for r in results:
        print(
            f"{r.name:<16} {r.protocol:<9} "
            f"{r.per_request_us:>8.1f}µ {r.p50_us:>8.1f}µ {r.p99_us:>8.1f}µ "
            f"{r.requests_per_second:>10,.0f}"
        )

    print(
        f"\nLoop-free WSGI fast path: {'confirmed' if loop_free else 'REGRESSED'}\n"
        "\nNotes:\n"
        "  - In-process through the real adapters; includes building the request.\n"
        "  - The ASGI numbers for sync handlers include one thread hop, which is\n"
        "    the cost of not blocking the event loop. Async handlers do not pay it.\n"
        "  - Your database is almost certainly a larger term than any row above.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
