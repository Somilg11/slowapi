---
hide:
  - navigation
  - toc
---

<div class="sa-hero" markdown="1">

# SlowAPI

<p class="sa-tagline">One handler. Two protocols.</p>

<p class="sa-sub">A Python web framework that is a WSGI application and an ASGI
application at the same time. Express ergonomics, FastAPI typing, NestJS
structure — and zero required runtime dependencies.</p>

<div class="sa-cta" markdown>
[Quickstart](quickstart.md){ .md-button .md-button--primary }
[Read the guide](guide/routing.md){ .md-button }
[Why it exists](growth.md){ .md-button }
</div>

</div>

## The whole idea, in one file

```python
from slowapi import SlowAPI

app = SlowAPI()


@app.get("/users/{id:int}")
def get_user(id: int) -> dict:          # (1)!
    return {"id": id}


@app.get("/fanout")
async def fanout() -> dict:             # (2)!
    return {"results": await gather_everything()}
```

1. A synchronous handler. On WSGI it runs with no event loop involved at all.
2. An asynchronous handler, in the same file, served by the same object.

```bash
gunicorn app:app --workers 4     # WSGI — and the async handler still works
uvicorn  app:app                 # ASGI — and the sync handler still works
```

Same file. No `wsgi.py` shim. No rewrite. No decision made in week one that you
have to live with in year two.

---

## Start here

<div class="grid cards" markdown>

-   :material-download: **[Installation](installation.md)**

    ---

    `pip install slowapi-framework`. Nothing else is required — every
    dependency is an opt-in extra.

-   :material-rocket-launch: **[Quickstart](quickstart.md)**

    ---

    A working, tested, validated service in about ten minutes.

-   :material-swap-horizontal: **[Migrating](migration.md)**

    ---

    What maps to what, coming from Flask, FastAPI, Express or NestJS.

-   :material-book-open-variant: **[The guide](guide/routing.md)**

    ---

    Sixteen chapters, written to be read in order rather than only grepped.

</div>

---

## What makes it different

**It is not a compatibility shim.** A fully synchronous request on the WSGI path
never creates an event loop — no task, no scheduler, no thread hop. The dispatch
pipeline is written once as `async def` so both protocols share one
implementation, then stepped to completion by hand when nothing in the chain can
suspend. [How that works](internals/dual-protocol.md).

That is also where the performance difference comes from:

<div class="sa-figure" markdown>

| Route | FastAPI (ASGI) | SlowAPI (ASGI) | SlowAPI (WSGI) |
| --- | ---: | ---: | ---: |
| plain text | 145.6µs | 60.4µs | **17.4µs** |
| json dict | 148.1µs | 68.8µs | **23.2µs** |
| path + query validated | 172.8µs | 78.7µs | **33.2µs** |
| async json | **16.1µs** | 17.4µs | — |

</div>

<p class="sa-note">Median per-request framework overhead, in-process, identical
handlers, byte-identical responses. One laptop, no concurrency. Reproduce it
with <code>python benchmarks/compare.py</code>.</p>

Read that as one result rather than four. **The gap is the worker-thread hop,
not the framework.** A `def` handler under ASGI must be offloaded to a thread so
it cannot stall the event loop, and that hop costs more than everything else on
the row combined. On WSGI, SlowAPI never makes it. Where no hop is involved — an
`async def` handler — the two are level.

So the honest claim is a narrow one: synchronous code is substantially cheaper
here, asynchronous code is a wash, and you should be choosing on features and
ecosystem, where FastAPI is far ahead.

---

## What is in the box

<div class="grid cards" markdown>

-   :material-check-decagram: **Typed and validated**

    ---

    Parameters, bodies and DTOs are coerced and validated from annotations,
    with or without Pydantic. OpenAPI is generated from the same source.

    [Validation](guide/validation.md) · [Serialization](guide/serialization.md)

-   :material-layers-triple: **Structure when you need it**

    ---

    Controllers, modules, providers and scoped dependency injection — available
    when an application grows, absent until then.

    [Modules](guide/controllers-modules.md) · [DI](guide/dependency-injection.md)

-   :material-shield-lock: **Secure by default**

    ---

    Signed sessions, path-traversal defences, proxy-header handling and security
    headers that are on before you configure anything.

    [Security](guide/security.md)

-   :material-heart-pulse: **Ready for an operator**

    ---

    Liveness and readiness endpoints, background tasks, request deadlines, and
    startup route validation that fails the build instead of the deploy.

    [Background and health](guide/background-and-health.md) · [Deployment](guide/deployment.md)

</div>

---

## Honest limits

This is a young framework, and the useful question is not whether the code is
good but whether the project is old enough to trust with someone else's data.

It has no WebSocket support yet, no third-party ecosystem, one maintainer, and
no release history. Nobody adversarial has audited it. Those are properties of
a project's age, and no amount of engineering shortens them.

Use it for internal tools, side projects and services you own end to end. The
[FAQ](faq.md) answers this at more length, and [why it exists](growth.md) makes
the case for the design itself.
