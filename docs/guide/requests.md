# Requests

A `Request` is protocol-neutral. The WSGI and ASGI adapters normalise their
native representations into the same object, so nothing you write here depends
on which server is running.

## Getting the request object

Three ways, all equivalent:

```python
from slowfw import Request


@app.get("/a")
def by_annotation(request: Request):  ...

@app.get("/b")
def by_convention(req, res):  ...        # unannotated `req` / `request`

@app.get("/c")
def by_context(ctx):  ...                # ctx.request
```

The convention form exists so Express handlers port over unchanged.

## Metadata

```python
@app.get("/inspect")
def inspect(req, res):
    res.json(
        {
            "method": req.method,               # "GET"
            "path": req.path,                   # "/inspect"
            "url": str(req.url),                # "http://host/inspect?x=1"
            "scheme": req.scheme,               # "http" | "https"
            "http_version": req.http_version,   # "1.1"
            "content_type": req.content_type,   # "application/json"
            "charset": req.charset,             # "utf-8"
            "ip": req.ip,                       # see the note below
            "request_id": req.request_id,       # correlation id
        }
    )
```

### `req.ip` and proxies

`req.ip` returns the peer address. It consults `X-Forwarded-For` **only** when
the request came through a hop you declared trusted:

```python
from slowfw.middleware import ProxyHeadersMiddleware

app.use(ProxyHeadersMiddleware(trusted_hosts=["10.0.0.0/8"]))
```

Without that, any client could set the header and forge its own address —
which silently breaks rate limiting, audit logs and IP allow-lists. Trusting
the header unconditionally is the common bug; this makes it a decision.

## Query parameters

```python
@app.get("/search")
def search(req, res):
    res.json(
        {
            "q": req.query_params.get("q", ""),
            "tags": req.query_params.getlist("tag"),   # ?tag=a&tag=b
            "all": dict(req.query_params),
        }
    )
```

`req.query` is an alias. Indexing returns the **last** value for a repeated
key; `getlist` returns all of them.

In practice, prefer declaring them — you get coercion, validation and
documentation for free. See [Validation](validation.md).

```python
@app.get("/search")
def search(q: str = Query(...), tag: list[str] = Query(default_factory=list)) -> dict:
    return {"q": q, "tags": tag}
```

## Headers

```python
req.headers["content-type"]          # case-insensitive
req.headers.get("x-trace")           # None if absent
req.headers.getlist("accept")        # every value
req.get("user-agent")                # Express alias for .headers.get
req.is_("application/json")          # does the body match this type?
req.accepts("text/html")             # does Accept allow this?
```

Or declare them:

```python
@app.get("/x")
def x(x_api_key: str = Header(...), accept: str = Header("*/*")) -> dict: ...
```

Underscores become dashes, so `x_api_key` reads `X-API-Key`. Turn that off with
`Header(..., convert_underscores=False)`.

## Cookies

```python
req.cookies.get("session")
```

Or `sid: str = Cookie("anonymous")`. For signed cookies, see
[`SessionMiddleware`](middleware.md#sessions).

## Path parameters

```python
@app.get("/users/{id:int}/posts/{slug}")
def show(req, res):
    res.json(req.path_params)     # {"id": 42, "slug": "hello"}   id is an int
```

`req.params` is the Express alias. Again, declaring them is better:

```python
def show(id: int, slug: str) -> dict: ...
```

## Bodies

Every body accessor exists in two flavours: an `async` one and a `_sync` one.
Use whichever matches the handler you are writing. Both work under both
protocols.

```python
@app.post("/echo")
async def echo_async(req) -> dict:
    return {
        "raw": (await req.body()).decode(),
        "json": await req.json(),
        "text": await req.text(),
        "form": dict(await req.form()),
    }


@app.post("/echo-sync")
def echo_sync(req, res):
    res.json({"json": req.json_sync(), "text": req.text_sync()})
```

The body is read once and cached, so calling `json()` after `body()` is free.

| Async | Sync | Returns |
| --- | --- | --- |
| `await req.body()` | `req.body_sync()` | `bytes` |
| `await req.text()` | `req.text_sync()` | `str` |
| `await req.json()` | `req.json_sync()` | parsed JSON |
| `await req.form()` | `req.form_sync()` | `FormData` |
| `req.stream()` | `req.stream_sync()` | chunk iterator |

Malformed JSON raises `BadRequest` (400) with the parser's message rather than
returning `None` — a body that was supposed to be JSON and is not is an error,
not an empty value.

### Streaming a large body

```python
@app.post("/ingest")
async def ingest(req) -> dict:
    total = 0
    async for chunk in req.stream():
        total += len(chunk)
    return {"bytes": total}
```

Use this for uploads you do not want in memory. Note that once you stream, the
body is consumed — `req.json()` afterwards sees nothing.

### Size limits

Bodies are capped at 16 MB by default, enforced both from `Content-Length` and
while reading (a lying header does not help). Exceeding it raises
`PayloadTooLarge` (413).

```python
app = SlowAPI(max_body_size=64 * 1024 * 1024)
```

## File uploads

```python
from slowfw import File, UploadFile


@app.post("/upload")
async def upload(document: UploadFile = File(...)) -> dict:
    content = await document.read()
    return {"name": document.filename, "type": document.content_type, "size": len(content)}
```

Files under 1 MB stay in memory; larger ones spill to a temporary file. Either
way, the file is closed when the request ends — you do not have to remember.

Several files under one field:

```python
async def upload(files: list[UploadFile] = File(...)) -> dict: ...
```

## Per-request state

`req.state` is a namespace for passing data from middleware to handlers:

```python
def authenticate(req, res, next):
    req.state.user = lookup(req.get("authorization"))
    next()


@app.get("/me")
def me(req, res):
    res.json({"user": req.state.user})
```

Prefer [dependencies](dependency-injection.md) when the value is *computed*;
`state` is for things a middleware already had to look up anyway.

## The escape hatch

`req.scope` is the raw dictionary. Under WSGI it also carries the original
`environ` under `scope["wsgi_environ"]`. Reach for it when you need something
protocol-specific — and know that you have just written code that only works on
one protocol.
