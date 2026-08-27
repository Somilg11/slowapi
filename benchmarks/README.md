# Benchmarks

```bash
python benchmarks/run.py
python benchmarks/run.py --iterations 20000 --json
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

Not included here, deliberately. A fair cross-framework benchmark needs
identical handlers, identical serialisation, identical middleware stacks, and a
real server — and most published comparisons have none of those. If you build
one, please share the methodology alongside the numbers.
