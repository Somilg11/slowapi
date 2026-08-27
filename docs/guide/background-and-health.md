# Background tasks, health checks, and deadlines

Three things every service needs before it is safe to deploy, and none of them
should cost the loop-free fast path.

## Background tasks

Work that must happen, but not before the client gets its response. Declare a
`BackgroundTasks` parameter and the framework hands you one:

```python
from slowapi import BackgroundTasks, Response, SlowAPI

app = SlowAPI()

@app.post("/signup")
def signup(body: SignupDTO, tasks: BackgroundTasks, res: Response):
    user = users.create(body)
    tasks.add(send_welcome_email, user.email)
    tasks.add(analytics.record, "signup", user_id=user.id)
    return res.status(201).json(user)
```

The 201 goes out immediately; the email and the analytics call run afterwards.

Tasks run in the order they were added, on both protocols, at the same point in
the lifecycle — ASGI runs them once the final body message is sent, WSGI once
the server closes the response iterable. Either way the client already has the
response.

A dependency can queue work the handler never sees, which is what makes audit
trails tidy:

```python
def audited(request: Request, tasks: BackgroundTasks) -> None:
    tasks.add(audit_log.write, request.path, request.ip)

@app.delete("/items/{id:int}")
def delete(id: int, res: Response, _: None = Depends(audited)):
    items.delete(id)
    return res.status(204).end()
```

### What happens when a task fails

The response is already sent, so there is nobody left to return an error to.
A failing task is logged to `slowapi.background` and the queue continues —
one bad task cannot take the others with it.

That is a deliberate trade. If a task must not be lost, it does not belong in a
background task; it belongs in a durable queue. See
[growth.md](../growth.md#what-is-deliberately-absent) for why the framework
does not ship one.

### Cost

A synchronous task on a synchronous route runs inline, in the same thread, with
no event loop — adding one does not opt the route out of the fast path. An
`async def` task pays for a hop onto the shared loop, and only then.

## Health checks

Liveness and readiness answer different questions, and giving them the same
answer is a common way to turn a blip into an outage.

```python
from slowapi import HealthCheck, SlowAPI

app = SlowAPI()

health = HealthCheck()
health.add("database", lambda: db.execute("SELECT 1"))
health.add("cache", cache.ping, critical=False)
health.add("upstream", probe_upstream, tolerate=2)
health.install(app)
```

That gives you two endpoints:

| Path | Question | Consults dependencies |
| --- | --- | --- |
| `/healthz` | Is this process alive? | No |
| `/readyz` | Should it get traffic right now? | Yes |

**Liveness never touches a dependency.** A liveness probe that checks the
database restarts every pod in the fleet the moment the database hiccups —
turning a recoverable incident into a cold start for the whole service.

**Readiness runs the checks.** A pod whose database connection is gone is taken
out of rotation and left running, so it can come back without a restart.

A check passes by returning without raising. Returning `False` also fails it,
so `lambda: pool.is_open` reads naturally. `async def` probes work too.

| Option | Effect |
| --- | --- |
| `critical=False` | Reported, but never fails the endpoint. Degraded is not down. |
| `tolerate=N` | Absorbs `N` consecutive failures before reporting one. Stops a blip from flapping a pod out of rotation. |
| `include_details=False` | Reports only `{"status": "ok"}`, publishing no dependency list. |

`/readyz` answers `200` when ready and `503` when not — never `500`. A load
balancer reads `503` as "not now" and retries; `500` looks like a bug in the
probe itself.

Probe exceptions are reported by class name only. A connection error's message
routinely contains a connection string, and this body may be world-readable.

Neither endpoint appears in the OpenAPI document. They are infrastructure, not
API surface.

### In Kubernetes

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8000 }
  periodSeconds: 10
readinessProbe:
  httpGet: { path: /readyz, port: 8000 }
  periodSeconds: 5
```

## Deadlines

A handler that never returns is the quietest kind of outage: connections pile
up, the worker pool drains, and liveness keeps passing because the process is
alive. A deadline turns that into a 504 you can alert on.

```python
from slowapi.middleware import TimeoutMiddleware

app.use(TimeoutMiddleware(seconds=10, per_path={"/reports/{id}": 60.0, "/export": None}))
```

`per_path` is keyed by the registered path template, not the request path, so
one entry covers every id. `None` exempts a route entirely — that is how a
deliberately long-running export avoids being cut off.

### What a deadline can and cannot do

This difference matters when you are choosing a value, so it is stated plainly
rather than buried:

- **On the async path the deadline is real.** `asyncio.wait_for` cancels the
  handler's task, so an `await` waiting on a slow query gets a `CancelledError`
  and the resources behind it unwind.
- **On the loop-free sync path there is no scheduler to interrupt**, and Python
  cannot pre-empt a running function. A `def` handler stuck in a blocking call
  runs to completion; the middleware notices afterwards and returns 504 instead
  of the late response.

The observable contract is the same either way — over the deadline means 504,
never the handler's own response — because protocol parity is the first
invariant. But a synchronous deployment should put a server-level timeout
underneath this one (`gunicorn --timeout`) to bound what the *socket* waits for.

`TimeoutMiddleware` carries `@never_suspends`, so adding it does not cost the
fast path. See [Middleware](middleware.md#keeping-the-fast-path) for what that
marker promises.

## See also

- [Deployment](deployment.md) — wiring probes into a scheduler
- [Middleware](middleware.md) — the onion these three sit in
- [Testing](testing.md) — asserting on tasks and probes
