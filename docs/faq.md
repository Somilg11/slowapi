# FAQ

### Is this a serious project or a learning exercise?

It started as a teaching toy — a few hundred lines showing how routing and WSGI
fit together. It is now a real framework with a test suite that runs every
scenario on two protocols. It is version 0.1.0: the API is stable enough to
build on, and honest enough to say it may still change before 1.0.

### Why "SlowAPI"? Is it slow?

The name is a joke about FastAPI, kept from the original teaching project.
Measured on the WSGI fast path, a trivial handler costs about 48µs in-process
and a typed, validated one about 67µs — competitive with any Python framework.
The name is not a benchmark claim in either direction. Run `make bench`.

Note that the PyPI name `slowapi` belongs to an unrelated rate-limiting library
for Starlette. This project publishes as `slowapi-framework` and imports as
`slowfw`, so both can be installed side by side. The command-line tool is still
`slowapi`.

### Why not just use FastAPI?

Use FastAPI if its ecosystem is what you need — it is excellent and far more
mature. Consider SlowAPI when:

- You need to run on WSGI (an existing gunicorn stack, a blocking driver, a
  platform without ASGI support) without giving up typed handlers.
- You want NestJS-style modules and guards without adopting a second framework.
- You want zero required dependencies.
- You want to render HTML as a first-class case, not an afterthought.

### Why not Flask?

Flask has no type-driven validation, no OpenAPI generation, no async story that
does not involve an extension, and no dependency injection. If you are happy
with Flask, stay — but the migration is mostly mechanical if you are not.

### Do I have to use `async`?

No. Write `def` handlers and run on WSGI and no event loop is ever created.
Write `async def` when concurrency actually helps. Mix both in one application.
That choice being per handler rather than per project is the point of the
framework.

### Which protocol should I deploy on?

Match it to your code. Mostly `def` handlers and blocking drivers → WSGI with
gunicorn. Mostly `async def` with async drivers → ASGI with uvicorn. You can
change later without touching application code; see
[Deployment](guide/deployment.md).

### Does `async def` really work on gunicorn?

Yes. SlowAPI runs one long-lived event loop in a background thread per process
and submits async work to it. Because the loop persists, async connection pools
and locks created by your code keep working across requests. There is a thread
hop per request, so it is not free — but it works, unchanged.

### Do I need Pydantic?

No. Dataclasses and `TypedDict` are supported natively. If Pydantic is
installed and your annotations use it, SlowAPI detects the models by duck typing
and delegates to them. Neither is the "real" way.

### Can I use SQLAlchemy / Django ORM / Tortoise / asyncpg?

Yes. SlowAPI has no opinion about persistence. Use a generator dependency or a
container provider for the session or pool. Sync ORMs are a good reason to
deploy on WSGI; async ones are a good reason to deploy on ASGI.

### How do I do authentication?

SlowAPI provides the hooks, not the policy. Use a [guard](guide/guards-interceptors-pipes.md)
for authorisation and a library (`PyJWT`, `argon2-cffi`) for the cryptography.
`@roles(...)` and `@public` give you a metadata vocabulary that your guard
interprets. There is no built-in user model and there will not be one.

### Is there WebSocket support?

Not yet. The ASGI adapter closes WebSocket connections cleanly rather than
hanging. It is on the roadmap, and the design question — what the WSGI side
does — is discussed in [growth.md](growth.md).

### How do I serve the docs in production?

By default `/docs` is available. `Settings.show_docs` is `False` in production
unless `DOCS_ENABLED=true`, and the recommended wiring is:

```python
app = SlowAPI(
    docs_url="/docs" if settings.show_docs else None,
    openapi_url="/openapi.json" if settings.show_docs else None,
)
```

The Swagger UI page loads its assets from a CDN. For air-gapped environments,
serve the JSON and point your own copy of Swagger UI at it.

### Can I use it with an existing WSGI stack?

Yes — it *is* a WSGI application. Mount it under any WSGI dispatcher, put it
behind any WSGI middleware, run it on any WSGI server.

### Why does my POST parameter come from the body?

On `POST`/`PUT`/`PATCH`/`DELETE`, a bare scalar parameter reads from the JSON
body first and falls back to the query string. That differs from FastAPI, and
it is deliberate: a scalar on a POST almost always comes from the body. Use
`Query(...)` explicitly if you want the query string only.

### How do I return a custom status code?

Three ways: `res.status(201).json(...)`, `@app.post("/x", status_code=201)`, or
`@http_code(201)` on a controller method.

### Why is my route 404 when I expect it to match?

Run `slowapi routes main:app` and look at the canonical paths. The usual causes
are a converter that does not match (`/users/abc` against `{id:int}`), a
missing prefix, or a `path` converter shadowing a later route.

### How fast is it?

See the table in [Dual-protocol dispatch](internals/dual-protocol.md), and run
`make bench` yourself. Framework overhead is rarely the bottleneck in a real
application — your database is — but the numbers are published rather than
implied.

### Is it production ready?

The test suite covers both protocols, CI runs four Python versions on three
operating systems, there are no required dependencies, and the security
defaults are documented in [Security](guide/security.md). It is also 0.1.0 and
young. Read [growth.md](growth.md) for what is deliberately not there yet, and
decide with that in front of you.

### How do I contribute?

Read [CONTRIBUTING.md](https://github.com/Somilg11/slowapi/blob/master/CONTRIBUTING.md). The short version: tests must run
on both protocols, and new required runtime dependencies are not accepted.
