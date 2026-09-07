# Middleware

Middleware wraps the whole request: it sees the request on the way in and the
response on the way out. SlowAPI uses Express's contract, because a single
function that can do both halves is easier to reason about than a pair of
before/after hooks.

## The contract

```python
def timing(req, res, next):
    started = time.perf_counter()
    next()                                     # everything downstream runs here
    res.set("X-Elapsed", f"{(time.perf_counter() - started) * 1000:.1f}ms")


app.use(timing)
```

Four spellings are accepted and normalised into one:

```python
def full(req, res, next): ...                  # sync, both halves
async def full_async(req, res, next): ...      # async, both halves (await next())
def pre_only(req, res): ...                    # runs before, continues automatically

class Timing:                                  # class-based
    async def dispatch(self, req, res, next): ...
```

## Order

`app.use(a, b, c)` produces `a(b(c(handler)))`. The first registered is the
outermost — it sees the request first and the response last.

Two framework middlewares are always outermost, before anything you register:

1. `ErrorMiddleware` — so nothing can escape unhandled.
2. `RequestIDMiddleware` — so every log line and error body has a correlation
   id, including ones produced by your own middleware.

## Short-circuiting

Skip `next()` and nothing downstream runs:

```python
def require_api_key(req, res, next):
    if req.get("x-api-key") != EXPECTED:
        res.status(401).json({"error": "unauthorized"})
        return                                 # the handler never runs
    next()
```

For authorisation specifically, prefer a [guard](guards-interceptors-pipes.md):
guards run after routing, so they know which handler was matched and can read
its metadata.

## Route-scoped middleware

```python
@app.get("/admin", middlewares=[audit])
def admin(res): ...
```

Route middleware runs inside all application middleware, immediately around the
handler.

## Async and sync, mixed

You can register both in one chain. SlowAPI figures out the rest:

- If **every** participant is sync, the whole chain runs with no event loop at
  all on the WSGI path.
- If any participant is async, the chain runs on a loop, and sync middleware is
  offloaded to a worker thread whose `next()` hops back onto the loop.

You are never asked to declare which mode you are in. See
[Dual-protocol dispatch](../internals/dual-protocol.md).

## Keeping the fast path

That first rule is stricter than it needs to be. Being `async def` is not the
same as being able to *suspend*: middleware that awaits nothing except
`call_next()` cannot suspend on its own, so it is transparent to the loop-free
path. SlowAPI assumes the worst by default, because assuming the worst is safe.

`@never_suspends` lets you say otherwise:

```python
from slowfw import never_suspends

@never_suspends
class Timing:
    async def dispatch(self, req, res, call_next):
        started = time.perf_counter()
        result = await call_next()          # the only await -- fine
        res.set("x-elapsed", f"{time.perf_counter() - started:.4f}")
        return result
```

Without the marker, adding that to an application moves **every** synchronous
route onto the event loop — a strange price for a timing header.

**The obligation is real and it is yours.** Inside a marked participant, do not
await anything but `call_next()`: no `asyncio.sleep`, no async HTTP client, no
lock, no `wait_for`. Break the promise and a synchronous request raises a
`RuntimeError` from `drive()` naming this as the cause — a clear failure rather
than a hang, but still your bug.

The built-in interceptors (`TimingInterceptor`, `EnvelopeInterceptor`,
`CacheInterceptor`) and `TimeoutMiddleware` carry the marker for exactly this
reason.

## Built-in middleware

```python
from slowfw.middleware import (
    AccessLogMiddleware,
    CORSMiddleware,
    GZipMiddleware,
    ProxyHeadersMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    SessionMiddleware,
    TrustedHostMiddleware,
)
```

A production stack, in a sensible order:

```python
app.use(
    ProxyHeadersMiddleware(trusted_hosts=["10.0.0.0/8"]),   # fix the client IP first
    TrustedHostMiddleware(["api.example.com"]),             # then validate Host
    SecurityHeadersMiddleware(hsts_seconds=31_536_000),
    AccessLogMiddleware(),
    CORSMiddleware(allow_origins=["https://app.example.com"], allow_credentials=True),
    RateLimitMiddleware(limit=600, window=60),
    GZipMiddleware(minimum_size=500),
    SessionMiddleware(settings.require_secret(), secure=True),
)
```

The order matters: `ProxyHeaders` must come before anything that reads
`req.ip`, and `TrustedHost` before anything that trusts `Host`.

### CORS

```python
CORSMiddleware(
    allow_origins=["https://app.example.com"],
    allow_origin_regex=r"https://.*\.example\.com",
    allow_methods=["GET", "POST"],
    allow_headers=["authorization"],
    expose_headers=["x-request-id"],
    allow_credentials=True,
    max_age=600,
)
```

Preflights are answered without reaching your handler. `Vary: Origin` is always
set, so a shared cache cannot serve one origin's response to another.
`allow_origins=["*"]` with `allow_credentials=True` raises at construction,
because browsers reject that combination — better to fail at import than to
debug it in devtools.

### Security headers

```python
SecurityHeadersMiddleware(
    content_security_policy="default-src 'self'",
    frame_options="DENY",
    referrer_policy="strict-origin-when-cross-origin",
    hsts_seconds=31_536_000,
)
```

Defaults are API-grade. A server-rendered app loading a CDN will need to relax
the CSP — do that explicitly rather than by turning it off.

HSTS is only emitted over HTTPS; sending it over plain HTTP does nothing except
mislead an auditor.

### Compression

```python
GZipMiddleware(minimum_size=500, compress_level=6)
```

Skipped for small bodies (compressing a 40-byte error makes it larger), for
already-compressed types, and for streams. A strong `ETag` is downgraded to a
weak one, because the compressed bytes are no longer the same entity.

### Sessions

```python
SessionMiddleware(
    secret=settings.require_secret(),
    max_age=14 * 24 * 3600,
    secure=True,
    samesite="lax",
    fallback_secrets=[OLD_SECRET],     # rotate without logging everyone out
)
```

```python
@app.post("/login")
def login(req, res, email: str):
    req.state.session["user"] = email
    req.state.session.flash("Welcome back.", "success")
    res.redirect("/")
```

The session is a signed cookie: HMAC-SHA256, constant-time comparison, salted
per purpose. It is **signed, not encrypted** — the contents are readable by the
client. Store an identifier, not a secret. Exceeding the 4093-byte browser
limit raises rather than silently truncating.

### Rate limiting

```python
RateLimitMiddleware(
    limit=100,
    window=60,
    key=lambda req: req.get("x-api-key") or req.ip,
    skip=lambda req: req.path == "/health",
)
```

The default store is an in-process sliding window: exact for one worker,
approximate across several. For multi-process deployments, implement the
`RateLimitStore` protocol against Redis — the shipped store is a working
reference for what that needs to do.

### Access logs

```python
AccessLogMiddleware(skip_paths=("/health", "/metrics"), slow_ms=1000)
```

One structured line per request with method, path, status, duration, request
id and client IP. `4xx` and slow requests log at `WARNING`, `5xx` at `ERROR`.

The status recorded is the status actually sent. That sounds obvious, but the
error middleware sits *outside* this one, so a deliberate `404` is still an
exception in flight when the log line is written — logging it as `500` would
send people hunting a bug that is not there.

### Deadlines

```python
TimeoutMiddleware(seconds=10, per_path={"/export": None})
```

Fails a request with `504` once it passes its deadline. What it can enforce
differs between the async and sync paths in a way worth understanding before
choosing a value — see
[Background tasks, health checks, and deadlines](background-and-health.md#deadlines).

## Writing your own

A class-based middleware, with dependencies:

```python
class Tenant:
    def __init__(self, tenants: TenantService) -> None:
        self.tenants = tenants

    async def dispatch(self, req, res, next):
        host = (req.get("host") or "").split(":")[0]
        tenant = await self.tenants.by_host(host)
        if tenant is None:
            res.status(404).json({"error": "unknown tenant"})
            return
        req.state.tenant = tenant
        result = await next()
        res.set("X-Tenant", tenant.id)
        return result


app.use(Tenant(tenant_service))
```

Rules worth knowing:

- **Pass instances, not classes.** `app.use(Tenant)` raises with a message
  saying so; the framework will not guess at constructor arguments.
- **Return the result of `next()`** when a downstream layer may have returned a
  different `Response` object.
- **Arity is checked at registration**, so a wrong signature fails at import.

## Middleware or interceptor?

| Use middleware when | Use an [interceptor](guards-interceptors-pipes.md) when |
| --- | --- |
| You work with headers, status, bytes | You work with the object the handler returned |
| It applies to every route, including 404s | It applies to specific routes or controllers |
| You need to run before routing | You need to know which route matched |

Compression is middleware. A response envelope is an interceptor.
