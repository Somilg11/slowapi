<div align="center">

# SlowAPI

**One handler. Two protocols.**

Express ergonomics · FastAPI typing · NestJS structure — on WSGI **and** ASGI, at the same time.

[![CI](https://github.com/Somilg11/slowapi/actions/workflows/ci.yml/badge.svg)](https://github.com/Somilg11/slowapi/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13%20|%203.14-blue)](https://pypi.org/project/slowapi-framework/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dependencies](https://img.shields.io/badge/runtime%20dependencies-0-brightgreen)](pyproject.toml)
[![Docs](https://img.shields.io/badge/docs-somilg11.github.io%2Fslowapi-0f766e)](https://somilg11.github.io/slowapi/)

**[Documentation](https://somilg11.github.io/slowapi/)** · [Quickstart](docs/quickstart.md) · [Guide](docs/README.md) · [Why it exists](docs/growth.md) · [How it works](docs/internals/dual-protocol.md)

</div>

---

```python
from slowapi import SlowAPI

app = SlowAPI()


@app.get("/users/{id:int}")
def get_user(id: int) -> dict:
    return {"id": id}


@app.get("/fanout")
async def fanout() -> dict:
    return {"results": await asyncio.gather(*(fetch(i) for i in range(10)))}
```

```bash
gunicorn app:app --workers 4     # WSGI — and the async handler still works
uvicorn  app:app                 # ASGI — and the sync handler still works
```

Same file. No `wsgi.py` shim. No rewrite. No decision made in week one that you
have to live with in year two.

---

## Why this exists

Every Python web framework makes you choose sync or async before you write a
line of code, and that choice decides your deployment for years.

Pick FastAPI, then find a library whose only client is blocking. Pick Flask,
then need server-sent events. Deploy on a platform that only speaks WSGI, and
typed handlers with generated OpenAPI are simply unavailable to you.

**That split is an artefact, not a necessity.** Routing does not care about the
protocol. Neither does validation, dependency injection, serialisation, or
OpenAPI generation. Only two things care: how bytes arrive, and how bytes leave.
In SlowAPI that is two files of about 150 lines, and nothing else in the
codebase imports either of them.

So: write the handler that fits the work. Choose the server separately, later,
and reversibly.

The full argument is in [docs/growth.md](docs/growth.md).

---

## What makes it different

**It is not a compatibility shim.** A fully synchronous request on the WSGI
path never creates an event loop at all — no task, no scheduler, no thread hop.
The dispatch pipeline is written once as `async def` so both protocols share
one implementation, and then stepped to completion by hand when nothing in the
chain can suspend:

```python
def drive(coro):
    try:
        coro.send(None)          # a coroutine that never awaits a future
    except StopIteration as stop:
        return stop.value        # runs straight through, no loop required
```

SlowAPI works out, at route registration, whether a route's entire chain —
handler, middleware, guards, interceptors, pipes, and every transitive
dependency — is synchronous, and picks the cheapest correct strategy per route.

| Handler | Server | Strategy | Cost |
| --- | --- | --- | --- |
| `def` | WSGI | called directly, no loop exists | none |
| `async def` | ASGI | awaited on the server's loop | none |
| `def` | ASGI | offloaded to a worker thread | one thread hop |
| `async def` | WSGI | driven on a shared background loop | one thread hop |

The fourth row is the one most frameworks refuse. It works because SlowAPI keeps
**one long-lived loop per process** rather than calling `asyncio.run` per
request — so async connection pools and locks survive across requests on a
gunicorn worker.

The claim is asserted, not promised:

```python
def test_a_fully_sync_wsgi_request_creates_no_event_loop():
    shutdown_loop_thread()
    TestClient(app, protocol="wsgi").get("/plain")
    assert _LoopThread._instance is None
```

And the fast path is defended, not just achieved. A helper that is `async def`
but awaits nothing except `call_next()` cannot suspend, so `@never_suspends`
lets it keep the loop-free path instead of quietly costing it — which is what
the built-in interceptors and `TimeoutMiddleware` would otherwise do to every
synchronous route just by being installed.

Details: [Dual-protocol dispatch](docs/internals/dual-protocol.md).

---

## Broken routes fail the build, not the deploy

Every handler signature, `Depends` chain, guard, interceptor, pipe and injected
provider is analysed before the process serves anything:

```bash
$ slowapi check main:app
FAIL  2 route(s) failed validation:
  GET /reports/{id} (get_report): Could not resolve type hints for 'get_report':
    name 'ReportService' is not defined.
  POST /items (create_item): Parameter 'body' declares Body() inside Annotated[...]
    and Query() as its default. Pick one.
```

Every broken route, not just the first. The same analysis runs during startup,
so a typo that would have surfaced on the first production request instead
stops the process from coming up — and in CI, stops the merge.

---

## Three frameworks, one file

Two of the three things Python teams keep asking for live in JavaScript. SlowAPI
brings them together, because they are complementary rather than competing.

### Express gives it ergonomics

```python
@app.get("/users/:id")                       # Express path syntax works
def get_user(req, res, id):
    res.status(200).json({"id": id})         # chainable response


def timing(req, res, next):                  # (req, res, next) middleware
    started = time.perf_counter()
    next()                                   # skip it and nothing downstream runs
    res.set("X-Elapsed", f"{(time.perf_counter() - started) * 1000:.1f}ms")


app.use(timing)
```

### FastAPI gives it types

```python
@dataclass
class CreateUser:
    email: str
    age: int = 18


@app.post("/users", status_code=201)
def create(payload: CreateUser, notify: bool = Query(False)) -> User:
    return service.create(payload)
```

Coerced from the wire, validated, every error reported at once, and documented
at `/docs` — from the same annotations that enforce it. Dataclasses need no
dependency; Pydantic works if you have it.

### NestJS gives it structure

```python
@controller("/users", tags=["users"])
@use_guards(RoleGuard)
class UserController:
    def __init__(self, users: UserService):        # constructor injection
        self.users = users

    @Get("/:id")
    @roles("admin")
    def show(self, id: int) -> User:
        return self.users.find(id)


@module(
    imports=[CoreModule],
    controllers=[UserController],
    providers=[UserService, RoleGuard],
    exports=[UserService],
)
class UserModule: ...


app = SlowAPI(modules=[UserModule])
```

A real container with singleton/request/transient scopes, guards that run
*before* anything is injected, interceptors that see the returned object, and
pipes that transform one argument.

**All three styles mix in one file.** None of them is a legacy path.

---

## Output shaping, because over-serialisation is the bug

```python
@dataclass
class User:
    id: int
    email: str = field(metadata=expose(groups=("admin",)))
    password_hash: str = field(default="", metadata=hidden())
    created: datetime = field(default=None, metadata=expose(alias="createdAt"))
```

```python
@Get("")
@public
def index(self) -> list[User]: ...              # {"id":1,"createdAt":"..."}

@Get("/directory")
@roles("admin")
@serialize_with(groups=("admin",))
def directory(self) -> list[User]: ...          # ...plus "email"
```

Same objects, different shapes, one decorator apart. `password_hash` never
leaves the process, and making it leak takes a deliberate act.

---

## Install

```bash
pip install slowapi-framework
```

**Zero required runtime dependencies.** No Pydantic, no Starlette, no `anyio`,
no `click`. Everything else is opt-in:

```bash
pip install "slowapi-framework[asgi]"        # uvicorn
pip install "slowapi-framework[wsgi]"        # gunicorn
pip install "slowapi-framework[templates]"   # jinja2
pip install "slowapi-framework[pydantic]"    # pydantic models as DTOs
pip install "slowapi-framework[all]"
```

> The PyPI name `slowapi` belongs to an unrelated rate-limiting library. This
> project publishes as **`slowapi-framework`** and imports as **`slowapi`**.

```bash
python -m slowapi new my-service     # scaffold a deployable project
cd my-service && python -m slowapi run main:app --reload
```

---

## Batteries, all of them optional

| | |
| --- | --- |
| **Routing** | Trie matching, `{id:int}` and `:id` syntaxes, six converters plus regex, reverse URLs, correct `HEAD`/`405`/`Allow` |
| **Validation** | `Annotated` or defaults; dataclasses, `TypedDict`, Pydantic; every error at once; constraints in the schema |
| **Injection** | `Depends` with caching and generator teardown, plus a scoped DI container |
| **Structure** | Controllers, modules, enforced `exports`, guards, interceptors, pipes |
| **Middleware** | CORS, security headers, trusted host, gzip, proxy headers, rate limit, sessions, request id, access logs, timeouts |
| **Responses** | JSON, HTML, redirects, streaming, SSE, files with ETag + byte ranges, automatic `304`s |
| **Operations** | Background tasks, liveness/readiness probes, per-request deadlines |
| **Templating** | Autoescaping engine with inheritance, loops, filters — or Jinja2 |
| **Static files** | ETags, `304`s, byte ranges, traversal and symlink protection, SPA fallback |
| **OpenAPI** | 3.1 generated from the running code, Swagger UI and ReDoc |
| **Config** | Typed settings from the environment, `.env` loader, production guardrails |
| **Observability** | Structured JSON logs, correlation ids threaded through logs and error bodies |
| **Testing** | `TestClient` over real WSGI and ASGI adapters, with uploads and a settable peer address |
| **CLI** | `run`, `check`, `routes`, `openapi`, `secret`, `new` |

---

## Security posture

On by default: no tracebacks to clients, autoescaped templates, `HttpOnly` +
`SameSite=Lax` cookies, confined static paths, body and multipart limits,
`X-Forwarded-*` ignored unless the hop is trusted, HMAC-signed sessions with
constant-time comparison, and a correlation id on every response.

Opt-in with your values: CORS, trusted hosts, HSTS and CSP, proxy networks,
rate limits.

Not provided, on purpose: authentication, an ORM, an admin. See
[docs/guide/security.md](docs/guide/security.md) for the full line — including
what remains yours.

---

## Performance

`make bench` — in-process through the real adapters, so this includes building
the request rather than only dispatch. M-series laptop, 3000 iterations:

| Route | WSGI | ASGI |
| --- | --- | --- |
| plain text, `def` | **48µs** | 147µs |
| JSON, `def` | **56µs** | 152µs |
| typed params + validation, `def` | **67µs** | 163µs |
| `Depends`, `def` | **62µs** | 159µs |
| `async def` | 107µs | **115µs** |

Read the diagonal. A synchronous handler is roughly **3× cheaper on WSGI**,
because the fast path never touches an event loop. An async handler is cheapest
on ASGI, because there is no thread hop back to a background loop.

The ASGI column for `def` handlers is dominated by `asyncio.to_thread` — the
honest cost of running blocking code without stalling the loop. Every ASGI
framework pays it. SlowAPI is the one that lets you stop paying it by changing
a deployment command rather than a codebase.

Framework overhead is rarely your bottleneck. The point of the table is not the
absolute numbers; it is that the right protocol depends on your code, and here
that is a decision you can defer and revisit.

---

## Documentation

| | |
| --- | --- |
| [Quickstart](docs/quickstart.md) | Ten minutes to a working typed service |
| [Guide](docs/README.md) | Routing, requests, responses, validation, DI, modules, deployment |
| [Dual-protocol dispatch](docs/internals/dual-protocol.md) | How one handler serves two protocols |
| [Architecture](docs/internals/architecture.md) | Module map and request lifecycle |
| [Migrating](docs/migration.md) | From Flask, FastAPI, Express, or NestJS |
| [Growth plan](docs/growth.md) | Why this exists and where it goes |
| [FAQ](docs/faq.md) | Including the honest "should I use this?" |
| [Examples](examples/) | Five runnable applications |

---

## Project status

**0.1.0.** The API is stable enough to build on and young enough to change
before 1.0.

- 214 tests, every dispatch scenario asserted on **both** protocols
- CI across Python 3.10–3.13 on Linux, macOS and Windows
- A CI job that installs with no extras and proves the zero-dependency claim
- Every configuration error raises at import, with a message naming the fix

Read [what is deliberately absent](docs/growth.md#3-what-is-deliberately-absent)
before adopting it. A framework that tells you what it will not do is easier to
plan around than one that implies it will do everything.

---

## Contributing

Two rules cover most of it:

1. **Tests run on both protocols.** Use the parameterised `client` fixture.
2. **No new required runtime dependencies.** Optional extras only.

See [CONTRIBUTING.md](CONTRIBUTING.md) and the
[invariants](docs/growth.md#2-the-invariants) they come from.

```bash
git clone https://github.com/Somilg11/slowapi && cd slowapi
make install
make check        # lint, types, and the full suite
```

---

## License

MIT. See [LICENSE](LICENSE).
