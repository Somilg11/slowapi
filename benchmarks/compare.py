#!/usr/bin/env python3
"""Compare SlowAPI against FastAPI on identical handlers.

Cross-framework benchmarks are usually unfair by accident: different handlers,
different serialisers, different middleware stacks, one framework's test client
against another's raw protocol.  This one tries not to be.

    pip install fastapi
    python benchmarks/compare.py
    python benchmarks/compare.py --iterations 40000 --json

Methodology
-----------
* Both applications are driven through the **raw ASGI protocol** with
  byte-identical scopes.  Neither test client is involved, so neither
  framework's client overhead is counted.
* The handlers are the same code, and the responses are verified
  byte-for-byte identical before timing starts.
* SlowAPI is additionally driven through **raw WSGI**, which is the point of
  the project: a synchronous handler never touches an event loop.
* The default middleware stack of each framework is left alone.  SlowAPI's
  includes a per-request UUID request ID that FastAPI's does not, so SlowAPI is
  doing slightly *more* work on every row.

What this does not measure: throughput, concurrency, or anything a real
application spends its time on.  Framework overhead stops mattering the moment
a handler talks to a database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from fastapi import FastAPI
    from fastapi import Query as FastAPIQuery
    from fastapi.responses import PlainTextResponse
except ModuleNotFoundError:  # pragma: no cover - depends on the environment
    sys.exit("FastAPI is not installed. Run: pip install fastapi")

from slowapi import Query, SlowAPI
from slowapi.logging import configure_logging

configure_logging("CRITICAL")

BATCH = 50


# --------------------------------------------------------------- applications


def build_slowapi() -> SlowAPI:
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

    @app.get("/async")
    async def async_route() -> dict:
        return {"ok": True}

    return app


def build_fastapi() -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/plain", response_class=PlainTextResponse)
    def plain() -> str:
        return "ok"

    @app.get("/json")
    def json_route() -> dict:
        return {"ok": True, "n": 1}

    @app.get("/params/{id}")
    def params(id: int, limit: int = FastAPIQuery(10, ge=1, le=100)) -> dict:
        return {"id": id, "limit": limit}

    @app.get("/async")
    async def async_route() -> dict:
        return {"ok": True}

    return app


# -------------------------------------------------------------------- drivers


def make_scope(path: str, query: bytes) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"accept", b"*/*")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


async def _receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


async def asgi_call(app, scope: dict) -> tuple[int, bytes]:
    status = 0
    chunks: list[bytes] = []

    async def send(message: dict) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        else:
            chunks.append(message.get("body", b""))

    await app(dict(scope), _receive, send)
    return status, b"".join(chunks)


def make_environ(path: str, query: str) -> dict:
    return {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "testserver",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(b""),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": True,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }


def wsgi_call(app, environ: dict) -> tuple[int, bytes]:
    captured: list[str] = []

    def start_response(status, headers, exc_info=None):
        captured.append(status)
        return lambda chunk: None

    body = app(dict(environ), start_response)
    try:
        payload = b"".join(body)
    finally:
        close = getattr(body, "close", None)
        if close is not None:
            close()
    return int(captured[0].split()[0]), payload


# -------------------------------------------------------------------- measure


def summarise(samples: list[float]) -> dict[str, float]:
    samples.sort()
    return {
        "mean_us": statistics.fmean(samples),
        "p50_us": samples[len(samples) // 2],
        "p99_us": samples[min(len(samples) - 1, int(len(samples) * 0.99))],
    }


async def time_asgi(app, path: str, query: bytes, iterations: int) -> dict[str, float]:
    scope = make_scope(path, query)
    for _ in range(500):
        await asgi_call(app, scope)
    samples = []
    for _ in range(max(1, iterations // BATCH)):
        started = time.perf_counter()
        for _ in range(BATCH):
            await asgi_call(app, scope)
        samples.append((time.perf_counter() - started) / BATCH * 1e6)
    return summarise(samples)


def time_wsgi(app, path: str, query: str, iterations: int) -> dict[str, float]:
    environ = make_environ(path, query)
    for _ in range(500):
        wsgi_call(app, environ)
    samples = []
    for _ in range(max(1, iterations // BATCH)):
        started = time.perf_counter()
        for _ in range(BATCH):
            wsgi_call(app, environ)
        samples.append((time.perf_counter() - started) / BATCH * 1e6)
    return summarise(samples)


ROUTES = [
    ("plain text", "/plain", "", b"", True),
    ("json dict", "/json", "", b"", True),
    ("path + query validated", "/params/42", "limit=20", b"limit=20", True),
    ("async json", "/async", "", b"", False),
]


async def assert_identical(slow, fast) -> None:
    """Refuse to publish numbers for handlers that do not agree."""
    for name, path, query_text, query_bytes, has_wsgi in ROUTES:
        fast_status, fast_body = await asgi_call(fast, make_scope(path, query_bytes))
        slow_status, slow_body = await asgi_call(slow, make_scope(path, query_bytes))
        if (fast_status, fast_body) != (slow_status, slow_body):
            sys.exit(
                f"{name}: responses differ, benchmark would be meaningless\n"
                f"  fastapi -> {fast_status} {fast_body!r}\n"
                f"  slowapi -> {slow_status} {slow_body!r}"
            )
        if has_wsgi:
            wsgi_status, wsgi_body = wsgi_call(slow, make_environ(path, query_text))
            if (wsgi_status, wsgi_body) != (slow_status, slow_body):
                sys.exit(f"{name}: SlowAPI's own protocols disagree, which is a bug")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20000)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()

    slow = build_slowapi()
    fast = build_fastapi()
    await slow.startup()

    async with fast.router.lifespan_context(fast):
        await assert_identical(slow, fast)

        rows = []
        for name, path, query_text, query_bytes, has_wsgi in ROUTES:
            rows.append(
                {
                    "route": name,
                    "fastapi_asgi": await time_asgi(fast, path, query_bytes, args.iterations),
                    "slowapi_asgi": await time_asgi(slow, path, query_bytes, args.iterations),
                    "slowapi_wsgi": (
                        time_wsgi(slow, path, query_text, args.iterations) if has_wsgi else None
                    ),
                }
            )

    await slow.shutdown()

    if args.json:
        print(json.dumps({"iterations": args.iterations, "rows": rows}, indent=2))
        return 0

    header = f"{'route':<24}{'FastAPI ASGI':>15}{'SlowAPI ASGI':>15}{'SlowAPI WSGI':>15}"
    print(header)
    print("-" * len(header))
    for row in rows:
        wsgi = row["slowapi_wsgi"]
        wsgi_text = f"{wsgi['p50_us']:.1f}us" if wsgi else "n/a"
        print(
            f"{row['route']:<24}"
            f"{row['fastapi_asgi']['p50_us']:>13.1f}us"
            f"{row['slowapi_asgi']['p50_us']:>13.1f}us"
            f"{wsgi_text:>15}"
        )

    print("\nmedian per-request overhead; lower is better\n")
    for row in rows:
        wsgi = row["slowapi_wsgi"]
        best = (wsgi or row["slowapi_asgi"])["p50_us"]
        protocol = "WSGI" if wsgi else "ASGI"
        ratio = row["fastapi_asgi"]["p50_us"] / best
        verb = "faster" if ratio >= 1 else "slower"
        factor = ratio if ratio >= 1 else 1 / ratio
        print(f"  {row['route']:<24} SlowAPI on {protocol} is {factor:.2f}x {verb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
