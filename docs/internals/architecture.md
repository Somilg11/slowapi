# Architecture

A map of the codebase and the path a request takes through it.

## Module layout

```
src/slowapi/
├── app.py               # SlowAPI: registration, dispatch, lifecycle
├── routing.py           # Route, Router, path compilation, the trie
├── request.py           # Request, body readers
├── response.py          # Response and its subclasses
├── injection.py         # Handler signature analysis, argument resolution
├── params.py            # Query/Path/Header/Cookie/Body/Depends/Inject markers
├── validation.py        # Coercion, constraints, JSON Schema
├── di.py                # Container, Provider, scopes, lifecycle hooks
├── decorators.py        # @controller, @module, @Get/@Post, metadata
├── execution.py         # ExecutionContext
├── guards.py            # Guards and the metadata vocabulary
├── interceptors.py      # Interceptors and built-ins
├── pipes.py             # Pipes and built-ins
├── serialization.py     # expose/hidden, serialize()
├── concurrency.py       # drive(), the loop thread, the sync/async bridge
├── datastructures.py    # Headers, MultiDict, QueryParams, UploadFile, State
├── formparsers.py       # multipart and urlencoded parsing
├── templating.py        # The built-in engine
├── static.py            # StaticFiles
├── openapi.py           # Schema generation, Swagger UI, ReDoc
├── exceptions.py        # The exception hierarchy
├── signing.py           # HMAC-signed payloads
├── logging.py           # Structured logging
├── config.py            # Settings, from_env, load_dotenv
├── server.py            # Development server selection
├── testing.py           # TestClient
├── scaffold.py          # `slowapi new`
├── __main__.py          # CLI
├── middleware/
│   ├── base.py          # The onion, executors, middleware adaptation
│   ├── cors.py  errors.py  logging.py  security.py
│   ├── compression.py  proxy.py  ratelimit.py  session.py
└── adapters/
    ├── wsgi.py          # environ -> scope, executor choice, byte output
    └── asgi.py          # scope/receive/send, lifespan
```

## The dependency rule

```
adapters/  ->  app.py  ->  everything else
                  |
        (nothing below knows about WSGI or ASGI)
```

`request.py`, `response.py`, `routing.py`, `injection.py` and the rest never
import from `adapters/`. That is what makes the dual-protocol design tractable:
protocol knowledge is confined to two files of roughly 150 lines each.

## Request lifecycle

```
1.  Server calls app(environ, start_response)  or  await app(scope, receive, send)
2.  Adapter builds a protocol-neutral scope + a BodyReader
3.  Adapter chooses an executor
      WSGI  -> SyncExecutor  if the route's chain is fully synchronous
            -> AsyncExecutor otherwise (runs on the shared loop thread)
      ASGI  -> AsyncExecutor (sync callables offloaded to threads)
4.  app.dispatch() builds the middleware onion:
      ErrorMiddleware -> RequestIDMiddleware -> user middleware -> terminal
5.  terminal:
      a. router.match(method, path)          -> Route + converted path params
      b. app._prepare(route)                 -> cached dispatch plan
      c. container.create_request_scope()
      d. run_guards(...)                     -> 403 / custom exception
      e. run_interceptors(..., inner):
           inner:
             i.   Resolver.build(signature)  -> coerce, validate, inject, pipes
             ii.  route middleware, if any
             iii. handler(**kwargs)
      f. _finalise(result)                   -> Response
      g. container.close()                   -> generator teardown, file closes
6.  Adapter writes the Response using its native protocol
7.  response.background(), if set
```

## What is computed once

Per-request work is deliberately small. These happen at registration or on the
first request for a route, then are cached:

| Computed once | Where |
| --- | --- |
| Path compilation into segments and converters | `compile_path`, at registration |
| Handler signature analysis | `analyse`, in `_prepare` |
| Guard / interceptor / pipe collection | `_prepare` |
| Fully-synchronous classification | `_prepare` |
| Serialisation options | `_prepare` |
| The OpenAPI document | `app.openapi()`, cached until routes change |

Nothing in the hot path calls `inspect.signature` or `get_type_hints`.

## Routing internals

Paths compile into a trie of `_Segment` objects. Each node holds:

- `literal`: a dict of exact segment matches
- `dynamic`: parameterised segments, each with a compiled regex
- `greedy`: a terminal `{x:path}` catch-all
- `routes`: method -> `Route`

Matching is depth-first with backtracking, preferring literal over dynamic over
greedy. Backtracking matters: if `{id:int}` matches a segment but nothing
deeper does, the walk retries with `{name}`.

Cost is proportional to the number of segments in the request path, not the
number of registered routes.

## Injection internals

`analyse()` produces a flat list of `ParamSpec` objects, one per handler
parameter, each recording its source (`query`, `path`, `header`, `cookie`,
`body`, `form`, `file`, `depends`, `inject`, `request`, `response`, `context`),
its annotation, its marker, and its default.

At request time, `Resolver.build()` walks that list, extracts each raw value,
coerces it, validates constraints, runs pipes, and collects *all* errors before
raising — which is why a request with four bad fields is one round trip.

`Depends` specs carry a nested `HandlerSignature`, so the dependency graph is
resolved recursively with per-request caching.

## Concurrency internals

See [Dual-protocol dispatch](dual-protocol.md). The short version:

- `drive(coro)` steps a coroutine to completion with no loop.
- `_LoopThread` is one lazily-created daemon loop per process.
- `run_coroutine_sync(coro, loop=...)` submits to the serving loop when one
  exists, otherwise to the shared one.
- `SyncExecutor` / `AsyncExecutor` decide how user callables are invoked.

## Design constraints

Every change is measured against these. They are also the review checklist in
[CONTRIBUTING.md](../../CONTRIBUTING.md).

1. **Protocol parity.** Behaviour must be identical on WSGI and ASGI.
2. **Zero required dependencies.** Optional extras only.
3. **No per-request introspection.** Analyse at registration.
4. **Fail at import, not at 3am.** Configuration errors raise at startup.
5. **Errors name the fix.** Not just what is wrong — what to do.
6. **The fast path stays loop-free.** A new framework-internal `await` that can
   suspend must not appear in the common path.
