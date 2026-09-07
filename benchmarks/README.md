# Benchmarks

```bash
python benchmarks/run.py                       # SlowAPI, both protocols
python benchmarks/run.py --iterations 20000 --json
python benchmarks/compare.py                   # vs FastAPI (pip install fastapi)
make bench
```

## What is measured

Per-request framework overhead, in-process, through the real WSGI and ASGI
adapters. There is no socket and no server, so the number isolates dispatch,
routing, injection, validation, and response building — plus the test client's
own request construction, which is included rather than subtracted.

## What is not measured

Throughput, concurrency, or anything resembling a production workload. For
that, use `wrk` or `k6` against a real server with a real handler. Framework
overhead is rarely the bottleneck in an application that talks to a database.

## Why the ASGI numbers are higher for sync handlers

Under ASGI a `def` handler is offloaded to a worker thread so it cannot stall
the event loop. That thread hop costs roughly 100–200µs. It is correct
behaviour, every ASGI framework does it, and it is precisely why SlowAPI lets
you deploy a synchronous application on WSGI instead of paying for it.

Async handlers under ASGI do not pay it. Sync handlers under WSGI do not pay
it. Choosing the protocol that matches your code is the whole point.

## Comparing against other frameworks

`compare.py` measures SlowAPI against FastAPI. A fair cross-framework benchmark
needs identical handlers, identical output, and one driver rather than two test
clients, so the script enforces all three:

* Both applications are driven through the **raw ASGI protocol** with
  byte-identical scopes. Neither test client is involved.
* Responses are compared **byte for byte before timing starts**, and the script
  exits rather than print numbers for handlers that disagree.
* Each framework keeps its own default middleware stack. SlowAPI's mints a UUID
  request ID per request and FastAPI's does not, so SlowAPI is doing slightly
  more work on every row.

On an M-series laptop, Python 3.14, FastAPI 0.141 / Pydantic 2.13, median
per-request overhead:

| route | FastAPI (ASGI) | SlowAPI (ASGI) | SlowAPI (WSGI) |
| --- | --- | --- | --- |
| plain text | 145.6µs | 60.4µs | **17.4µs** |
| json dict | 148.1µs | 68.8µs | **23.2µs** |
| path + query validated | 172.8µs | 78.7µs | **33.2µs** |
| async json | **16.1µs** | 17.4µs | n/a |

Read it as one result, not four: **the gap is the thread hop, not the
framework.** A `def` handler under ASGI must be offloaded to a worker thread so
it cannot stall the event loop, and that hop costs more than everything else on
the row combined. SlowAPI is 5–8x faster on those routes because on WSGI it
never makes the hop — not because its routing or validation is cleverer.

Where no hop is involved, the two are level: on `async def` handlers FastAPI is
ahead by about 8%, which is roughly what SlowAPI spends generating the request
ID FastAPI does not generate.

So the honest summary is narrow. Synchronous code deployed on WSGI is
substantially cheaper here. Asynchronous code is a wash, and you should choose
on features and ecosystem instead — where FastAPI is far ahead.

Numbers are from one laptop under no concurrency. Run it on your own hardware
before quoting it, and remember that a handler which opens a database
connection has already spent more than every figure in the table.
