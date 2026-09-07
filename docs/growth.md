# Growth plan

Why SlowAPI exists, what it is deliberately not, and where it goes next.

This document is opinionated on purpose. A roadmap that lists only features is
a wish list; one that states the constraints those features must satisfy is a
plan. Everything below is subject to the [invariants](#2-the-invariants), which
are not.

---

## 1. Why this exists

### The problem is not "another framework"

Python has excellent web frameworks. Django is the most complete web
application platform in the language. FastAPI made typed handlers and generated
documentation the default expectation. Flask remains the clearest small
framework anyone has written. Starlette is a well-built ASGI toolkit.

SlowAPI does not exist because those are bad. It exists because of a decision
they all force, in the same place, for the same structural reason.

### The decision: sync or async, chosen once, at the start

In 2018 the Python web ecosystem split. WSGI frameworks (Django, Flask,
Pyramid, Bottle) were synchronous. ASGI frameworks (Starlette, FastAPI,
Sanic, Quart) were asynchronous. Seven years later the split is deeper, not
shallower, and it has a cost that is rarely stated directly:

**Your deployment protocol is now an architectural decision you make before you
have written any code, and it is expensive to revisit.**

Consider what actually happens:

- A team picks FastAPI for the typing and the docs. Six months later they need
  a library whose only client is synchronous — a payment gateway SDK, a legacy
  SOAP client, a CPU-bound serialiser. Now every call to it either blocks the
  event loop or goes through `run_in_threadpool`, and the "async framework"
  they chose is running a thread pool.
- A team picks Flask because their ORM is synchronous and their stack is
  gunicorn. Two years later they need server-sent events for a dashboard. Now
  they are looking at a rewrite, or at running two services.
- A team runs on a platform where ASGI is awkward — an older gunicorn
  deployment, a PaaS with a WSGI-shaped contract, a corporate environment where
  the approved server is `mod_wsgi`. Typed handlers and generated OpenAPI are
  simply unavailable to them, for reasons that have nothing to do with their
  code.

The framework asked a question the application was not ready to answer, and
then made the answer structural.

### The observation

The split is not fundamental. It is an artefact of how the frameworks are
built, not of what they do.

Routing does not care. Validation does not care. Dependency injection does not
care. Serialisation, templating, error handling, OpenAPI generation — none of
these have any relationship to whether the transport is a blocking call or an
awaited coroutine. Only two things care:

1. How the request bytes arrive.
2. How the response bytes leave.

That is a boundary you can put in two files. In SlowAPI it is
`adapters/wsgi.py` and `adapters/asgi.py`, about 150 lines each, and nothing
else in the codebase imports either of them.

### The thesis

> **Protocol is a deployment concern, not a programming model.**

If that is true, then a framework should let you write the handler that fits
the work — `def` for blocking work, `async def` for concurrent I/O — and choose
the server separately, later, and reversibly.

SlowAPI is what that looks like when you build it all the way through. The
same application object is a WSGI application and an ASGI application. The same
handler runs under either. Deployment stops being a decision made in week one
and becomes a decision made from measurement.

The engineering that makes it more than a wrapper is documented in
[Dual-protocol dispatch](internals/dual-protocol.md): a fully synchronous
request on the WSGI path never creates an event loop, because the shared
`async def` pipeline is stepped to completion by hand when nothing in it can
suspend. It is not a compatibility shim with a shim's cost. It is a native
path on both sides.

### The second reason: the three-framework problem

There is a smaller, more practical reason.

Python teams routinely want three things that currently live in three different
ecosystems:

| Want | Where it lives today |
| --- | --- |
| `res.status(201).json(...)`, `(req, res, next)` middleware | Express (Node) |
| Typed parameters, validation, generated OpenAPI | FastAPI |
| Modules, controllers, DI, guards, interceptors | NestJS (Node) |

Two of those three are in JavaScript. A Python team that wants NestJS's
structure either writes it themselves, adopts a heavy DI library that does not
know about HTTP, or does without.

SlowAPI takes all three, because they are complementary rather than competing:
Express contributes ergonomics, FastAPI contributes types, NestJS contributes
structure. They compose in one framework without contradiction, and the result
is something none of them individually is.

### What is genuinely new here

Being honest about novelty is more useful than claiming it broadly. Of the
things SlowAPI does, these are the ones no other Python framework does:

1. **One application object, natively both protocols.** Not an adapter, not a
   bridge — two first-class paths over one shared pipeline.
2. **A loop-free synchronous fast path over a shared async pipeline.** One
   implementation of dispatch, driven without a scheduler when nothing in the
   chain can suspend.
3. **Per-route synchrony classification.** The framework determines, at
   registration, whether a route's entire chain is synchronous, and picks the
   cheapest correct execution strategy per route.
4. **A persistent background loop rather than per-request `asyncio.run`.**
   Which is what makes async connection pools survive on a WSGI worker.
5. **Express, FastAPI and NestJS idioms in one Python framework**, mixable in
   the same file.
6. **A test client that drives real adapters for both protocols**, making
   parity a tested property rather than a claim.
7. **Zero required runtime dependencies** while providing all of the above.

Everything else — routing, validation, OpenAPI, sessions, CORS — is
well-trodden ground done carefully. The list above is what would be missing
from the ecosystem if this project did not exist.

---

## 2. The invariants

These constrain every future change. A feature that cannot satisfy them does
not get built, however useful it would be.

### I. Protocol parity

Any behaviour must be identical under WSGI and ASGI. If a feature can only work
on one, it is not a framework feature — it is an extension, clearly labelled,
that raises a comprehensible error on the other side.

*Why:* the moment parity becomes "mostly", the promise is worthless. Users
would have to know which subset they are allowed to use, which is the decision
the framework exists to remove.

### II. Zero required runtime dependencies

The core installs with nothing. Optional features are extras.

*Why:* every dependency is code you ship, audit, patch, and inherit
vulnerabilities from. A framework that pulls in twelve packages has made a
supply-chain decision on the user's behalf. It also keeps the codebase honest —
you cannot paper over a bad design by adding a library.

### III. The fast path stays loop-free

No framework-internal `await` that can actually suspend may appear in the
common synchronous path. New internals must be classifiable.

*Why:* this is the property that makes the design an engineering result rather
than a convenience wrapper. It is asserted in the test suite, not assumed.

### IV. Analyse at registration, not per request

Signature inspection, path compilation, and chain classification happen once.
Nothing in the hot path calls `inspect.signature` or `get_type_hints`.

*Why:* per-request reflection is the single most common source of avoidable
overhead in Python frameworks.

### V. Fail at import, not at 3am

Configuration errors — duplicate routes, unknown converters, missing providers,
circular dependencies, bad middleware arity — raise at import or startup.

*Why:* a process that starts is a process whose wiring is sound. Deferred
errors turn a typo into an incident.

### VI. Errors name the fix

Not just what is wrong: what to do about it.

```
ConfigurationError: UserModule exports ['Auditor'] which it does not provide.
Add them to providers=, or re-export by importing their module.
```

*Why:* the error message is the most-read documentation in any framework.

### VII. Both styles stay first-class

Express-style `res` mutation and FastAPI-style return values are equally
supported, forever. Neither becomes a legacy path.

*Why:* the mixture is the product. Deprecating half of it would make this an
inferior FastAPI.

### VIII. Additions are opt-in

New capability arrives as something you can choose. Existing applications keep
working unchanged, and defaults do not shift under people.

*Why:* a framework's real product is stability. Churn is a tax paid by every
user of every version.

---

## 3. What is deliberately absent

Stating what a project will not do is more useful than listing what it might.

| Not provided | Why |
| --- | --- |
| An ORM or query builder | Persistence is not a web concern. SQLAlchemy, Django ORM, Tortoise, raw drivers — all work fine, and none should be blessed. |
| A user model, password hashing, JWT handling | Authentication is application policy with real cryptographic consequences. Guards are the hook; `argon2-cffi` and `PyJWT` are the tools. |
| An admin interface | Django does this well. A half-admin is worse than none. |
| A background task queue | `res.background` is best-effort and says so. Real queues need persistence, retries, visibility, and a worker model. Celery, RQ, and Dramatiq exist. |
| A migration tool | Alembic exists. |
| A plugin auto-discovery mechanism | Import the thing and call `app.use(...)`. Magic registration is how import order becomes a debugging concern. |
| A required settings singleton | `Settings` is a dataclass you may use. Global mutable configuration is a testing problem. |
| Request-context globals (`g`, `current_app`) | `req.state` and explicit parameters. Context proxies are convenient until you write a test. |

---

## 4. Roadmap

Versions are directional, not dated. Each item states what problem it solves,
because a feature without a problem is scope.

### 0.2 — Depth on what exists

*Theme: make the current surface complete before widening it.*

**`Annotated` support**

```python
def search(q: Annotated[str, Query(min_length=2)]) -> dict: ...
```

*Problem:* default-value markers are incompatible with positional arguments and
read oddly to anyone used to modern FastAPI. `Annotated` fixes both, and it is
what the wider ecosystem has standardised on. The existing syntax continues to
work — invariant VIII.

**Content negotiation**

```python
@app.get("/report")
@produces("application/json", "text/csv", "application/xml")
def report(): ...
```

*Problem:* today an endpoint that serves both JSON and CSV branches on
`Accept` by hand, and the OpenAPI document does not know about it.

**Response-model enforcement in development**

*Problem:* a return annotation currently documents; it does not verify. In
debug mode, checking that the returned object matches the declared shape turns
a class of drift bugs into an immediate error, without paying for it in
production.

**A structured request-log formatter for OpenTelemetry**

*Problem:* every deployment writes the same adapter to get request ids into
traces. Zero-dependency by design: emit the fields, let the collector consume
them.

**Route-level middleware in `include_router`**

*Problem:* router-level middleware is declared but only fully applied through
modules. Closing the gap.

### 0.3 — WebSockets, honestly

*Theme: the biggest hole, solved without breaking invariant I.*

WebSockets are natively ASGI. WSGI has no bidirectional model. Pretending
otherwise would break protocol parity, so the design has to be explicit:

```python
@app.websocket("/live")
async def live(ws: WebSocket):
    await ws.accept()
    async for message in ws:
        await ws.send_json({"echo": message})
```

Under ASGI this works fully. Under WSGI, `app.websocket(...)` registers the
route but the handler raises a clear, documented error at startup:

```
ConfigurationError: 3 WebSocket route(s) are registered, but this application
is running under WSGI, which has no bidirectional transport. Deploy on ASGI
(uvicorn), or remove the routes. See docs/guide/websockets.md.
```

That is invariant I applied honestly: the feature is labelled, the failure is
loud, and nobody discovers it in production.

Server-sent events already work on both protocols and remain the recommended
answer for one-way streaming.

**Also in 0.3:** a `Channel` abstraction over the pub/sub pattern that every
WebSocket application reimplements, with an in-memory backend and a documented
protocol for Redis.

### 0.4 — Production surface

*Theme: the things every deployment ends up writing.*

**Distributed rate limiting**

A Redis-backed `RateLimitStore` in `slowapi.contrib.redis`, opt-in. The
protocol already exists; this is a reference implementation so that four
workers do not silently mean a 4× limit.

**CSRF protection**

Synchroniser tokens for form-heavy applications, integrated with sessions and
the template engine:

```python
app.use(CSRFMiddleware(secret))
```
```html
<form method="post">{{ csrf_field | safe }}</form>
```

*Problem:* `SameSite=Lax` covers most cases and not all. Server-rendered apps
need the rest.

**Health and readiness helpers**

```python
app.add_health_checks(liveness=[], readiness=[db.ping, cache.ping])
```

*Problem:* everyone writes these two endpoints, and roughly half get the
distinction wrong in a way that makes a database blip restart every pod.

**A Prometheus-shaped metrics middleware**

Emitting text-format metrics without importing a client library.

**Request timeouts**

```python
app.use(TimeoutMiddleware(seconds=30))
```

Honest about the difference: cancellable on ASGI, best-effort on WSGI.

### 0.5 — Scale of codebase, not of traffic

*Theme: what a hundred-endpoint application needs.*

**Lazy module loading**

```python
@module(imports=[LazyModule("myapp.billing:BillingModule")])
```

*Problem:* import time on large applications, and serverless cold starts.

**API versioning as a first-class concept**

```python
app = SlowAPI(versioning="url")     # or "header", or "accept"
```

*Problem:* `@controller(version="v2")` handles the simple case. Header and
media-type versioning need router support and correct OpenAPI output.

**Schema-diff tooling**

```bash
slowapi openapi-diff main:app --against openapi.json --fail-on breaking
```

*Problem:* the OpenAPI document is generated, so breaking changes are
detectable mechanically. Making that a CI gate is a small amount of work with a
large payoff.

**A dependency graph visualiser**

```bash
slowapi graph main:app --format mermaid
```

*Problem:* on a large application, "what does this module actually depend on"
is currently answered by reading.

### 1.0 — Stability commitment

Not a feature release. 1.0 means:

- The public API is frozen under semantic versioning.
- Every public name has a docstring and a test.
- Coverage above 95%, with both protocols asserted for every dispatch path.
- Published benchmarks, with methodology and a reproduction script.
- A security policy with a stated response window.
- A documented deprecation policy: two minor versions of warning, minimum.
- At least three independent production deployments known to the maintainers.

That last one is the real gate. A framework declares 1.0 when other people are
relying on it, not when its author feels ready.

### Beyond 1.0 — research, not promises

Ideas that need proving before they are commitments.

**HTTP/3 and QUIC.** Currently an ASGI-server concern. If the server ecosystem
exposes stream-level control, there may be a framework-level story.

**A typed client generator.**

```bash
slowapi client main:app --output client.py
```

The OpenAPI document is generated from real annotations, so a fully typed
client is derivable. The interesting question is whether it stays honest across
versions.

**Compile-time route optimisation.** Generating a specialised dispatch function
per route rather than walking a plan. Promising for the loop-free path, where
per-request overhead is already visible in the profile.

**A structured concurrency layer.** Task groups and cancellation scopes that
behave the same on both protocols. Hard, because WSGI has no cancellation.

**Edge and WASI targets.** Both protocols are already abstracted behind one
boundary; a third adapter is a smaller change here than in most frameworks.

**Zero-copy responses.** `sendfile` on WSGI, and the ASGI extension where the
server supports it.

---

## 5. How decisions get made

Every proposal is judged on five questions, in this order:

1. **Does it work identically on both protocols?** If not, it is an extension
   with a documented failure mode, or it is not built.
2. **Does it require a new runtime dependency?** If yes, it belongs in
   `slowapi.contrib` behind an extra, or outside the project entirely.
3. **Can it be analysed at registration?** Per-request reflection is not
   acceptable.
4. **Is it opt-in?** Existing applications must keep working unchanged.
5. **Does it belong in a web framework?** Persistence, authentication, queues
   and admin interfaces do not.

A proposal that fails 1 or 2 needs a reasoned case, not just a use case.

---

## 6. What success looks like

Not stars, and not benchmark position.

- **A team migrates from gunicorn to uvicorn without touching application
  code**, and says so publicly. That is the thesis surviving contact.
- **Someone writes a handler that mixes a blocking driver and an async client**
  and does not have to think about which colour their functions are.
- **A Python team stops maintaining a NestJS service** because the structure
  they wanted now exists in Python.
- **A contributor finds and fixes a protocol-parity bug** using the dual test
  client — which means the invariant is being maintained by the process, not by
  one person's vigilance.
- **The framework stays boring.** The best outcome for a web framework is that
  people stop talking about it and use it.

---

## 7. Contributing to the direction

The roadmap is a proposal, not a contract. Concrete arguments beat votes.

- Open a [discussion](https://github.com/Somilg11/slowapi/discussions) for
  direction.
- Open an [issue](https://github.com/Somilg11/slowapi/issues) for a specific
  proposal, and describe the situation you are in rather than the API you want.
- Read [CONTRIBUTING.md](https://github.com/Somilg11/slowapi/blob/master/CONTRIBUTING.md) before opening a pull request.

The most valuable contribution is not a feature. It is a report of the shape
*"I tried to do X and the framework made it hard, and here is what I did
instead."* That is the input this document is written from.
