# Dual-protocol dispatch

This is the part of SlowAPI that does not exist elsewhere, so it is worth
explaining properly rather than asserting.

## The problem

Python web frameworks pick a side.

WSGI (PEP 3333) is synchronous: the server calls `app(environ, start_response)`
and expects an iterable of bytes. ASGI is asynchronous: the server awaits
`app(scope, receive, send)`. A framework built for one cannot serve the other
without an adapter that gives up most of the benefit — `a2wsgi` and friends
work, but they pay a thread hop or a loop per request and they are a translation
layer, not a native path.

The consequence is that "sync or async" stops being a per-handler decision and
becomes an architectural one, made early, and expensive to revisit. Teams pick
FastAPI and then discover their database driver blocks. Teams pick Flask and
then need server-sent events.

## The claim

In SlowAPI, the same application object serves both protocols natively, and the
same handler runs under either, whether it is `def` or `async def`.

```python
app = SlowAPI()

@app.get("/x")
def sync_handler(res): ...

@app.get("/y")
async def async_handler() -> dict: ...

# gunicorn app:app     -> both work
# uvicorn  app:app     -> both work
```

## How the object is both

```python
def __call__(self, *args):
    if len(args) == 3:
        return ASGIAdapter(self)(*args)     # returns a coroutine
    if len(args) == 2:
        return WSGIAdapter(self)(*args)     # returns an iterable
```

Dispatching on argument count is unambiguous: no protocol uses the other's
arity. ASGI servers `await` the return value and get a coroutine; WSGI servers
iterate it and get bytes. `app.wsgi_app` and `app.asgi_app` are available for
servers that introspect signatures.

## The four cases

| Handler | Server | Strategy | Cost |
| --- | --- | --- | --- |
| `def` | WSGI | Called directly, no loop exists | none |
| `async def` | ASGI | Awaited on the server's loop | none |
| `def` | ASGI | Offloaded to a worker thread | one thread hop |
| `async def` | WSGI | Driven on a shared background loop | one thread hop |

The first two are the fast paths and cost nothing. The other two are the
interesting ones.

### `def` on ASGI

The handler is pushed to a thread via `asyncio.to_thread`, so a blocking call
slows itself down and nothing else. This is what Starlette and FastAPI do too.

The wrinkle is that a sync handler may still need the body, which only the
serving loop can produce. `AsyncBodyReader.iter_sync` hops back:

```python
chunk = asyncio.run_coroutine_threadsafe(read_one(), serving_loop).result()
```

The worker thread blocks; the loop stays free.

### `async def` on WSGI

This is the case most frameworks refuse. SlowAPI runs one long-lived daemon
thread per process hosting an event loop, created lazily the first time
something actually needs it:

```python
result = asyncio.run_coroutine_threadsafe(handler(...), loop_thread.loop).result()
```

A per-request `asyncio.run()` would also work and would be simpler, but it tears
the loop down every time — which destroys async connection pools, `asyncio.Lock`
objects, and background tasks created by user code. A shared loop keeps them
alive for the process lifetime, which is what makes an async database driver
usable from a gunicorn worker.

## The loop-free fast path

The interesting engineering is in making case 1 genuinely free.

The dispatch pipeline — middleware, guards, injection, interceptors, the
handler — is written once, as `async def`. Sharing one implementation is what
keeps the two protocols behaving identically. But naively, running that
pipeline on WSGI would need a loop even when nothing in it is async.

It does not, because of one property of coroutines:

> A coroutine only suspends when it awaits something that is not already done —
> a future, a sleep, socket I/O. A chain of coroutines that only ever `await`
> other coroutines never yields to a scheduler.

So a fully synchronous chain can be driven by hand:

```python
def drive(coro):
    try:
        coro.send(None)              # one step
    except StopIteration as stop:
        return stop.value            # finished without ever suspending
    coro.close()
    raise RuntimeError("a chain marked fully synchronous suspended")
```

No loop, no task, no thread, no scheduler. Just a function call chain wearing
coroutine syntax.

### Deciding which requests qualify

At route registration, SlowAPI inspects every participant in that route's
chain — the handler, application middleware, route middleware, guards,
interceptors, pipes, and every transitive dependency — and records whether any
of them is async:

```python
plan.fully_sync = not any(_maybe_async(p) for p in participants)
```

The result is cached per route. The WSGI adapter reads it before dispatch:

```python
if fully_sync:
    result = drive(app.dispatch(request, response, SyncExecutor()))
else:
    result = run_coroutine_sync(app.dispatch(request, response, AsyncExecutor()))
```

`SyncExecutor` calls user callables inline. `AsyncExecutor` awaits async ones
and offloads sync ones to a thread. Both are `async def`; only one of them can
ever suspend.

### The remaining leaks

Two internals would have needed a loop even on the fast path, and both check:

- `iterate_in_threadpool` falls back to inline iteration when no loop is
  running — correct, because with no loop there is nothing to block.
- `SyncBodyReader.aiter` does the same for request bodies.

### Why this is safe

If a coroutine driven by `drive()` ever *did* suspend, it would raise a clear
`RuntimeError` rather than deadlocking or corrupting state. The classification
is conservative: anything it cannot prove synchronous goes down the loop path.
A false negative costs a thread hop; a false positive raises loudly in the
first test that touches the route.

The test suite asserts the property directly:

```python
def test_a_fully_sync_wsgi_request_creates_no_event_loop():
    shutdown_loop_thread()
    TestClient(app, protocol="wsgi").get("/plain")
    assert _LoopThread._instance is None
```

## Measured cost

`python benchmarks/run.py`, M-series laptop, 3000 iterations per route,
in-process through the real adapters (so this includes building the request,
not only dispatch):

| Route | WSGI | ASGI |
| --- | --- | --- |
| plain text, `def` | **48µs** | 147µs |
| JSON, `def` | **56µs** | 152µs |
| typed params + validation, `def` | **67µs** | 163µs |
| `Depends`, `def` | **62µs** | 159µs |
| `async def` | 107µs | **115µs** |

Three things to read out of it.

**A `def` handler is about 3× cheaper on WSGI.** That difference is the
loop-free fast path. There is no task, no scheduler, and no thread.

**A `def` handler on ASGI costs roughly 100µs more.** That is
`asyncio.to_thread`, and it is the honest price of running blocking code
without stalling the loop. Every ASGI framework pays it, including Starlette
and FastAPI.

**An `async def` handler is cheapest on ASGI**, and costs about 107µs on WSGI —
the hop to the shared background loop and back. It *works* on WSGI, which is
the point, but it is not free.

The diagonal is the argument: the cheapest configuration depends on what your
handlers actually do. Every other framework asks you to guess that before
writing the code. SlowAPI lets you measure and then change a deployment
command.

Framework overhead is rarely the dominant term in a real application — a single
database round trip is an order of magnitude larger. These numbers are
published because the design makes a performance claim, and a claim without a
measurement is marketing.

## What this buys you

- **Deployment stops being a framework decision.** Run on the stack you already
  have; change later without a rewrite.
- **Incremental migration.** Turn one handler `async def` at a time, on the
  server you are already running.
- **Blocking drivers stay usable.** A sync database driver is fine — put it on
  WSGI, or let ASGI offload it, and either way the code is the same.
- **Tests cover both.** `TestClient(app, protocol=...)` drives real adapters,
  so parity is verified rather than assumed.

## Limitations, stated plainly

- **WebSockets are ASGI-only** and are not implemented yet; the ASGI adapter
  closes the connection cleanly rather than hanging.
- **Very high concurrency wants ASGI.** Sync workers are processes; there is a
  ceiling.
- **Gevent-style monkey patching is untested** and the loop thread is a real
  thread. Do not combine them without verifying.
- **The fast path is a WSGI property.** ASGI always has a loop, by definition.
