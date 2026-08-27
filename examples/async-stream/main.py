"""Streaming, background work, and mixing sync with async in one app.

Every handler below runs under both protocols. What differs is *how*:

* Under ASGI, ``async def`` handlers run on the server's loop and ``def``
  handlers are offloaded to a worker thread.
* Under WSGI, ``def`` handlers run inline with no loop at all, and ``async
  def`` handlers run on SlowAPI's shared background loop.

You do not choose. You write the handler that fits the work.

    uvicorn main:app                       # recommended for this example
    gunicorn main:app --workers 2          # also works, unchanged
"""

from __future__ import annotations

import asyncio
import json
import time
import typing as t
from dataclasses import dataclass

from slowapi import Query, SlowAPI, StreamingResponse
from slowapi.middleware import AccessLogMiddleware

app = SlowAPI(title="Streaming", version="1.0.0")
app.use(AccessLogMiddleware(slow_ms=5000))


@dataclass
class Tick:
    n: int
    at: float


# ---------------------------------------------------------------- streaming


@app.get("/sse", tags=["stream"])
async def server_sent_events(count: int = Query(5, ge=1, le=100)) -> StreamingResponse:
    """Server-sent events: a long-lived response with incremental writes.

    Nothing is buffered. The client sees each event as it is produced, which is
    what makes SSE usable for progress bars and log tails.
    """

    async def events() -> t.AsyncIterator[bytes]:
        for n in range(count):
            payload = json.dumps({"n": n, "at": time.time()})
            yield f"event: tick\ndata: {payload}\n\n".encode()
            await asyncio.sleep(0.2)
        yield b"event: done\ndata: {}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )


@app.get("/ndjson", tags=["stream"])
def newline_delimited_json(rows: int = Query(1000, ge=1, le=100_000)) -> StreamingResponse:
    """A *synchronous* generator, streamed.

    This is the shape most reporting endpoints want: pull rows from a cursor,
    serialise one at a time, never hold the full result set in memory.
    """

    def rows_iter() -> t.Iterator[bytes]:
        for n in range(rows):
            yield (json.dumps({"row": n, "squared": n * n}) + "\n").encode()

    return StreamingResponse(rows_iter(), media_type="application/x-ndjson")


@app.get("/download", tags=["stream"])
def download(size_kb: int = Query(64, ge=1, le=8192)) -> StreamingResponse:
    """A large body produced lazily, with a filename the browser will use."""

    def chunks() -> t.Iterator[bytes]:
        block = b"x" * 1024
        for _ in range(size_kb):
            yield block

    response = StreamingResponse(chunks(), media_type="application/octet-stream")
    return response.attachment(f"{size_kb}kb.bin")


# ------------------------------------------------------- mixing sync + async


@app.get("/blocking", tags=["mixed"])
def blocking(ms: int = Query(50, ge=0, le=2000)) -> dict:
    """Deliberately blocking.

    Under ASGI this is pushed to a worker thread, so it slows itself down and
    nothing else. Under WSGI it runs inline, which is what a sync worker is
    for. Same code either way.
    """
    time.sleep(ms / 1000)
    return {"blocked_ms": ms}


@app.get("/concurrent", tags=["mixed"])
async def concurrent(n: int = Query(5, ge=1, le=50)) -> dict:
    """Fan out and gather, the reason to reach for ``async def`` at all."""

    async def unit(i: int) -> int:
        await asyncio.sleep(0.05)
        return i * i

    started = time.perf_counter()
    results = await asyncio.gather(*(unit(i) for i in range(n)))
    return {
        "results": results,
        # ~50ms regardless of n, because they overlapped.
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


# ------------------------------------------------------------- background


@app.post("/notify", tags=["background"])
def notify(res, email: str = Query(...)) -> None:
    """Answer immediately, do the slow part after the response is flushed."""

    def send_later() -> None:
        time.sleep(0.5)
        app.logger.info("notification_sent", extra={"email": email})

    res.background = send_later
    res.status(202).json({"queued": email})


if __name__ == "__main__":
    app.run(port=8000)
