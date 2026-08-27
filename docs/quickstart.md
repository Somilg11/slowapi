# Quickstart

Ten minutes, one file, a working typed service that runs on two protocols.

## 1. A first handler

```bash
pip install slowapi-framework
```

```python
# main.py
from slowapi import SlowAPI

app = SlowAPI(title="Quickstart", version="1.0.0")


@app.get("/")
def index(res):
    res.json({"hello": "world"})
```

```bash
python -m slowapi run main:app --reload
curl localhost:8000/
```

`res` is the response object, injected because the parameter is named `res`.
Mutate it and return nothing — this is the Express style.

## 2. Or return a value instead

```python
@app.get("/ping")
def ping() -> dict:
    return {"pong": True}
```

Both styles are first-class and mix freely in one application. Use `res` when
you need control over headers and status; return a value when you do not.

## 3. Typed parameters

```python
from slowapi import Query


@app.get("/search")
def search(q: str = Query(..., min_length=2), limit: int = Query(10, ge=1, le=100)) -> dict:
    return {"q": q, "limit": limit}
```

```bash
curl "localhost:8000/search?q=hi&limit=5"   # {"q":"hi","limit":5}   limit is an int
curl "localhost:8000/search?q=x"            # 422, and it says why
curl "localhost:8000/search?limit=99999"    # 422, and it says which bound
```

`...` means required. Everything on the wire is text; the annotation is what
turns it into the type your function actually wants.

## 4. Path parameters, in either syntax

```python
@app.get("/users/{id:int}")       # FastAPI style, typed
def get_user(id: int) -> dict:
    return {"id": id}


@app.get("/posts/:slug")          # Express style
def get_post(slug: str) -> dict:
    return {"slug": slug}
```

`/users/abc` is a 404, not a 500 — the `:int` converter refuses to match before
your handler is ever called.

## 5. Request bodies

```python
from dataclasses import dataclass


@dataclass
class CreateUser:
    email: str
    age: int = 18


@app.post("/users", status_code=201)
def create_user(payload: CreateUser) -> dict:
    return {"email": payload.email, "age": payload.age}
```

```bash
curl -X POST localhost:8000/users -H 'content-type: application/json' \
     -d '{"email":"a@b.c","age":"30"}'
```

A plain dataclass is a DTO. Every field error is reported at once, not one per
round trip. (Pydantic models work here too if you prefer them.)

## 6. Middleware

```python
import time

from slowapi.middleware import CORSMiddleware, SecurityHeadersMiddleware


def timing(req, res, next):
    started = time.perf_counter()
    next()                                   # everything downstream runs here
    res.set("X-Elapsed", f"{(time.perf_counter() - started) * 1000:.1f}ms")


app.use(SecurityHeadersMiddleware(), CORSMiddleware(allow_origins=["*"]), timing)
```

Express's `(req, res, next)` contract, including short-circuiting: skip
`next()` and nothing downstream runs. `async def` middleware works the same way
with `await next()`.

## 7. Async where it helps

```python
import asyncio


@app.get("/fanout")
async def fanout() -> dict:
    async def unit(i):
        await asyncio.sleep(0.05)
        return i * i

    return {"results": await asyncio.gather(*(unit(i) for i in range(10)))}
```

This handler runs under **both** servers. Under uvicorn it uses the server's
loop; under gunicorn's sync workers SlowAPI runs it on a shared background
loop. You are not asked to pick a colour for your functions.

## 8. Free API documentation

Already running:

- `http://localhost:8000/docs` — Swagger UI
- `http://localhost:8000/redoc` — ReDoc
- `http://localhost:8000/openapi.json` — the raw document

It is generated from the annotations that also do the validating, so it cannot
drift from the implementation.

## 9. Tests that cover both protocols

```python
# test_main.py
import pytest

from slowapi.testing import TestClient

from main import app


@pytest.fixture(params=["wsgi", "asgi"])
def client(request):
    with TestClient(app, protocol=request.param) as c:
        yield c


def test_search_rejects_a_short_term(client):
    assert client.get("/search", params={"q": "x"}).status_code == 422
```

Every test now runs twice, once per protocol. A regression that only affects
one transport cannot reach production quietly.

## 10. Deploy it

```bash
gunicorn main:app --workers 4 --bind 0.0.0.0:8000    # WSGI
uvicorn  main:app --host 0.0.0.0 --port 8000         # ASGI
```

Same file. No `wsgi.py` shim, no `asgi.py` shim, no rewrite.

## Where to go next

- [Routing](guide/routing.md) for converters, mounting and reverse URLs
- [Dependency injection](guide/dependency-injection.md) for sharing a database session
- [Controllers and modules](guide/controllers-modules.md) once one file stops being enough
- [Deployment](guide/deployment.md) for choosing between the two protocols on purpose
