# Deployment

SlowAPI's deployment story is short because the application object is already
both a WSGI app and an ASGI app. There is no `wsgi.py` shim and no `asgi.py`
shim; you point a server at `main:app`.

```bash
gunicorn main:app --workers 4                 # WSGI
uvicorn  main:app --host 0.0.0.0 --port 8000  # ASGI
```

## Which protocol should you actually use?

This is the one deployment decision worth thinking about, and SlowAPI is the
rare framework where you can change your mind later for free.

| Choose **WSGI** (gunicorn sync workers) when | Choose **ASGI** (uvicorn) when |
| --- | --- |
| Handlers are mostly `def` | Handlers are mostly `async def` |
| Work is CPU-bound or uses blocking drivers | Work is I/O-bound with async drivers |
| You need long-lived streaming or SSE | You need long-lived streaming or SSE |
| You want the simplest possible failure mode | You need thousands of concurrent connections |
| An existing stack already runs gunicorn | You want WebSockets (roadmap) |

Two facts to weigh:

- On the WSGI path, a fully synchronous request **never creates an event loop**.
  Dispatch is a direct function call chain, which is measurably cheaper than
  scheduling a task.
- On the ASGI path, a `def` handler is offloaded to a worker thread so it
  cannot stall the loop. That is correct, and it costs a thread hop per
  request — measured at about 100µs. A synchronous handler is roughly 3×
  cheaper on WSGI (48µs vs 147µs for a trivial route); an `async def` handler
  is cheapest on ASGI. See `make bench`.

Start where your code already is. Migrate when the workload changes, not
before, and let the test suite (which runs both) tell you it still works.

## gunicorn (WSGI)

```bash
gunicorn main:app \
  --workers $((2 * $(nproc) + 1)) \
  --bind 0.0.0.0:8000 \
  --timeout 30 \
  --graceful-timeout 30 \
  --max-requests 10000 --max-requests-jitter 1000 \
  --access-logfile - --error-logfile -
```

- **`--workers`**: `2 × cores + 1` is the usual starting point for sync
  workers. Measure before tuning.
- **`--max-requests`** recycles workers periodically, which papers over slow
  leaks. The jitter stops every worker restarting at once.
- **`--timeout`** must exceed your slowest handler, or gunicorn will kill it
  mid-request.

Do **not** use `--worker-class gevent` with SlowAPI unless you have verified
your whole stack is monkey-patch safe; the background loop thread and gevent's
patched threading are not a combination the project tests.

## uvicorn (ASGI)

```bash
uvicorn main:app \
  --host 0.0.0.0 --port 8000 \
  --workers 4 \
  --proxy-headers --forwarded-allow-ips='*' \
  --timeout-keep-alive 5
```

For process management, gunicorn with uvicorn workers is a common production
choice:

```bash
gunicorn main:app -k uvicorn.workers.UvicornWorker --workers 4 --bind 0.0.0.0:8000
```

## Behind a reverse proxy

Two middlewares matter, in this order:

```python
app.use(
    ProxyHeadersMiddleware(trusted_hosts=["10.0.0.0/8"]),
    TrustedHostMiddleware(["api.example.com"]),
)
```

Without `ProxyHeadersMiddleware`, `req.ip` is the proxy's address and rate
limiting buckets every user together. With it, but with `trusted_hosts="*"`,
any client can forge its own IP. Name the network your proxy actually sits on.

An nginx front end that does not break streaming:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_http_version 1.1;
    proxy_buffering off;          # required for SSE and streamed responses
    proxy_read_timeout 300s;
}
```

## Docker

The shipped `Dockerfile` is multi-stage, runs as uid 10001, and health-checks
the application's own endpoint rather than just the process:

```bash
docker build -t myapp .
docker run -p 8000:8000 -e SECRET_KEY=... -e ENVIRONMENT=production myapp
```

```bash
docker compose up                  # app + postgres + redis
docker compose --profile wsgi up   # the same app served over WSGI
```

## Health checks

```python
@app.get("/health", include_in_schema=False)
def health() -> dict:
    """Liveness: is the process able to answer? No dependencies checked."""
    return {"status": "ok", "version": app.version}


@app.get("/ready", include_in_schema=False)
async def ready(db: Database) -> dict:
    """Readiness: can this instance serve traffic right now?"""
    await db.ping()
    return {"status": "ready"}
```

Keep them separate. A liveness probe that checks the database restarts every
pod when the database blips.

## Production checklist

- [ ] `SECRET_KEY` set from a secret manager, not a file
- [ ] `ENVIRONMENT=production` (turns on JSON logs, turns off `/docs`)
- [ ] `debug=False`
- [ ] `TrustedHostMiddleware` with the real host list
- [ ] `ProxyHeadersMiddleware` with the real proxy network
- [ ] `SecurityHeadersMiddleware(hsts_seconds=31_536_000)` behind TLS
- [ ] `SessionMiddleware(secure=True)` if you use sessions
- [ ] Rate limiting with a shared store, if more than one worker
- [ ] Structured logs shipped somewhere, with the request id indexed
- [ ] `/health` and `/ready` wired to the orchestrator
- [ ] Worker count set from measurement, not from a blog post

## Graceful shutdown

Under ASGI, the lifespan protocol runs `app.shutdown()`, which closes
generator-based providers and calls every `on_module_destroy`. Under WSGI there
is no lifespan protocol; startup happens on the first request, and shutdown
runs at process exit. If you need deterministic cleanup on a WSGI deployment,
call `app.shutdown()` from a signal handler.

## Zero-downtime deploys

`gunicorn --reload` is for development only. For production, either use
gunicorn's `SIGHUP` graceful reload, or run two sets of containers behind a
load balancer and drain the old one. `--max-requests` helps with slow leaks but
is not a deployment strategy.
