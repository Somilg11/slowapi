# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

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

[Unreleased]: https://github.com/slowapi/slowapi/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/slowapi/slowapi/releases/tag/v0.1.0
