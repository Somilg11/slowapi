# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `Annotated[T, Query(...)]` and friends. The marker lives in the type, so a
  marked parameter no longer has to sit after every unmarked one and the
  default slot stays free for an actual default. Works for `Query`, `Path`,
  `Header`, `Cookie`, `Body`, `Form`, `File`, `Depends` and `Inject`.
  Unrecognised metadata passes through, so annotations shared with other tools
  keep working. Declaring a marker in both places is refused at startup.
- `BackgroundTasks`: an injectable queue of work that runs after the response,
  in order, on both protocols. One failing task is logged and the rest still
  run. Synchronous tasks run inline, so queueing work does not opt a route out
  of the loop-free fast path.
- `HealthCheck`: `/healthz` (liveness, consults nothing) and `/readyz`
  (readiness, runs registered probes), with `critical=False` for
  report-but-do-not-fail and `tolerate=N` to absorb a blip. Answers `503`, not
  `500`, and reports probe exceptions by class name only. Neither endpoint
  appears in the OpenAPI document.
- `TimeoutMiddleware`: a per-request deadline producing `504`, with `per_path`
  overrides and `None` to exempt a route. The documentation is explicit about
  what a deadline can enforce on the sync path, where nothing can pre-empt a
  running function.
- `SlowAPI.check()` and `slowapi check module:app`: analyse every route without
  starting a server. Runs automatically during startup, so a broken route now
  fails the process rather than the first request; the CLI form moves the
  discovery to a red build. Every broken route is reported, not just the first.
- `@never_suspends`, marking a participant that awaits nothing but
  `call_next()` so it keeps the loop-free fast path. Applied to the built-in
  interceptors and `TimeoutMiddleware`, which previously moved every
  synchronous route onto the event loop just by being installed.
- `503 ServiceUnavailable` and `504 GatewayTimeout` exceptions.
- `TestClient(files=...)` for `multipart/form-data` uploads, and
  `TestClient(client=("1.2.3.4", 5000))` to control the peer address — needed
  to test anything that depends on who is connecting.
- `TestResponse.body`, which un-gzips; `text` and `json()` now read from it, so
  a test about a payload need not know whether compression is installed.

### Fixed

- **`response.background` never ran on WSGI** for non-streaming responses,
  while ASGI always ran it — a protocol parity break. Both branches now run it
  through the response iterable's `close()`, and an `async def` callable is
  awaited rather than left as an un-awaited coroutine.
- **`shutdown()` never ran under WSGI.** With no lifespan protocol, a gunicorn
  worker exiting skipped every `on_event("shutdown")` hook and left the
  container's singletons undisposed. The adapter now registers an `atexit`
  hook *and* a chained `SIGTERM`/`SIGINT` handler — Python's default signal
  handler terminates the process without running `atexit`, so a worker signalled
  directly would still have skipped teardown. Verified against a live gunicorn
  worker, both through the arbiter and signalled directly.
- **`X | None` was not recognised on Python 3.10–3.13.** The union branch tested
  `hasattr(t, "UnionType")`, but `UnionType` lives in `types`, not `typing`, so
  the check was always false. `types.UnionType is typing.Union` only on 3.14,
  which is why local runs passed. An optional nested model reached the handler
  as a raw dict on every other supported version.
- **File uploads leaked their temporary file on the loop-free path.**
  `UploadFile.close()` went through `run_in_threadpool`, which raises
  `RuntimeError: no running event loop` when there is no loop; cleanup failed
  silently and leaked a descriptor per upload. `run_in_threadpool` now falls
  back to calling inline when no loop is running.
- **Path parameters arrived percent-encoded.** `/items/a%20b` reached the
  handler as `a%20b`. Segments are now split first and decoded second, so a
  `%2F` cannot forge a path separator.
- **A scalar sent alongside a file upload was answered with a JSON parse
  error.** Scalars on write methods are classified as body parameters before
  the content type is known; a form-encoded request now reads them from the
  form. `avatar + caption` is the most common upload shape and it returned 400.
- **`T | None` was not recognised as `T`.** `UploadFile | None`,
  `Request | None`, and an optional DTO all fell through to the query string.
- **An absent optional parameter could receive the `REQUIRED` sentinel** rather
  than its declared default, so a missing optional upload arrived as `Ellipsis`.
- **`res.etag()` never produced a `304`.** Conditional requests are now
  answered on the shared dispatch path, using RFC 9110 weak comparison and
  honouring `If-Modified-Since` when there is no `ETag`. Safe methods only.
- **`res.etag()` before the body hashed an empty body**, giving every such
  response the same tag. A computed tag is now deferred to send time, so chain
  order does not matter.
- **Comma-separated list settings were read as one string.**
  `CORS_ORIGINS=a.com,b.com` produced the single nonsense origin
  `"a.com,b.com"`, rejecting both — quietly, and only in the environment that
  had it set.
- Exception handler arguments are matched by role rather than by position, so
  `(req, exc, res)` and `(req, res, exc)` both work. The old positional rule
  contradicted the `(req, res)` order used everywhere else and getting it
  backwards produced an `AttributeError` from inside error handling.

### Changed

- Coverage is now gated at 85% in `pyproject.toml`, and CI validates every
  example's routes and runs a scaffolded project's own tests.
- `NotEmptyPipe` documents that it is length-based, so `"   "` passes; pair it
  with `TrimPipe` when whitespace should not count as content.

## [0.1.0] — 2026-08-27

The first release. SlowAPI began as a teaching project demonstrating how
routing and WSGI fit together; this release rewrites it as a framework.

### Added — the dual-protocol core

- One application object that is both a WSGI and an ASGI application, selected
  by calling convention (`app(environ, start_response)` vs
  `await app(scope, receive, send)`); `app.wsgi_app` and `app.asgi_app` for
  servers that introspect signatures.
- A loop-free synchronous fast path: `concurrency.drive()` steps the shared
  `async def` dispatch pipeline to completion with no event loop when nothing
  in a route's chain can suspend.
- Per-route synchrony classification at registration, covering the handler,
  middleware, guards, interceptors, pipes, and transitive dependencies.
- A persistent per-process background event loop, so `async def` handlers run
  on WSGI servers with async pools and locks surviving across requests.
- `SyncExecutor` / `AsyncExecutor`, choosing how user callables are invoked.

### Added — routing

- Trie-based matching with backtracking; cost proportional to path segments,
  not route count.
- Both `{id}` and `:id` syntaxes, normalised to one canonical form.
- Converters: `str`, `int`, `float`, `uuid`, `slug`, `path`, and `re:<pattern>`.
- Partial-segment parameters (`/report.{fmt}`).
- Automatic `HEAD` fallback to `GET`, correct `405` with `Allow`.
- `Router` with prefixes, tags, middleware, decorators, and nesting.
- Reverse routing via `app.url_for` / `Router.url_path_for`.

### Added — requests and responses

- Protocol-neutral `Request` with async and sync accessors for every body form.
- Streaming request bodies, size limits enforced while reading.
- Dependency-free multipart and urlencoded parsers, with disk spillover.
- `Response` with a chainable Express-style API and constructor-style
  subclasses.
- `FileResponse` with ETag, `Last-Modified`, and byte-range support.
- `StreamingResponse` accepting sync or async iterables.
- Background callables that run after the response is flushed.

### Added — validation and injection

- Type-driven coercion and validation with no third-party dependency;
  dataclasses, `TypedDict`, and Pydantic models as DTOs.
- `Query`, `Path`, `Header`, `Cookie`, `Body`, `Form`, `File`, `Depends`,
  `Inject` markers with constraints that also reach the OpenAPI schema.
- All validation errors collected and reported in one response.
- Handler signatures analysed once at registration.
- Generator dependencies with teardown after the response.

### Added — application structure

- A DI container with singleton, request, and transient scopes, value, factory,
  class, and token providers, cycle detection, and lifecycle hooks.
- `@controller` and `@module` with enforced `exports`.
- Guards, interceptors, and pipes, with a metadata vocabulary (`@roles`,
  `@public`, `@set_metadata`) that policy interprets.
- Declarative serialisation: `expose()`, `hidden()`, groups, aliases,
  transforms, `@serialize_with`.

### Added — middleware

CORS, security headers, trusted host, gzip, proxy headers, rate limiting,
signed-cookie sessions, request ids, and structured access logs — all following
the Express `(req, res, next)` contract.

### Added — everything else

- An autoescaping template engine with inheritance, loops, filters, and
  includes; Jinja2 as an alternative.
- Static file serving with conditional requests, ranges, and traversal
  protection.
- OpenAPI 3.1 generation, Swagger UI, and ReDoc.
- Typed settings from the environment with a `.env` loader.
- Structured JSON and console logging.
- `TestClient` driving real WSGI and ASGI adapters.
- A CLI: `run`, `routes`, `openapi`, `secret`, `new`.

### Fixed — from the original teaching project

- `start_response` was called with `header=` instead of the positional
  `headers`, which no WSGI server accepts.
- Route decorators returned `None`, so decorated handlers became `None` and
  could not be stacked or called directly.
- `parse()` results were computed but exact string comparison was used for
  matching, so path parameters never matched.
- `Request` set attributes from raw `environ` keys, so header access depended
  on WSGI naming and `self.query_string` could raise `AttributeError`.
- Query parsing assigned a string for `k=v` and appended to a list for a bare
  key, producing two different types for one field.
- Mutable default arguments (`middlewares=[]`) were shared across every route.
- `Response.render` interpolated with `re.sub` and no escaping, so any rendered
  value was an XSS vector, and the replacement string was itself interpreted.
- Middleware was restricted to `types.FunctionType`, rejecting lambdas, bound
  methods, `functools.partial`, and callable objects.
- No `Content-Length` was ever set.

[Unreleased]: https://github.com/Somilg11/slowapi/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Somilg11/slowapi/releases/tag/v0.1.0
