# Responses

SlowAPI supports two response styles. They are equally supported, they mix
freely inside one application, and neither is a legacy path.

## Style 1: mutate `res`

```python
@app.get("/users/{id:int}")
def get_user(id: int, res):
    res.status(200).json({"id": id})
```

Every mutator returns `self`, so calls chain. Use this when you care about
status codes, headers, cookies, or redirects — that is, most of the time in a
web application as opposed to an API.

## Style 2: return a value

```python
@app.get("/users/{id:int}")
def get_user(id: int) -> dict:
    return {"id": id}
```

The return value is serialised and sent. Use this when the handler is a pure
function of its inputs. Returned dataclasses, lists, and Pydantic models are
serialised through the [serialization](serialization.md) rules.

Default status codes when you return a value: `201` for `POST`, `200`
otherwise. Override with `status_code=` on the route or `@http_code(...)`.

## Sending content

```python
res.send("<h1>hi</h1>")            # guesses: text/html
res.send({"a": 1})                 # guesses: application/json
res.json({"a": 1})                 # explicit
res.text("plain")                  # text/plain
res.html("<p>hi</p>")              # text/html
res.end()                          # no body
res.redirect("/login", 303)
res.render("index", {"user": user})   # a template
```

`res.send` mirrors Express's overloading: mappings and sequences become JSON,
everything else becomes text.

## Status and headers

```python
res.status(404)
res.set("X-Trace", "abc")          # replaces
res.header("X-Trace", "abc")       # alias
res.append("Link", "<...>; rel=next")   # adds without replacing
res.type("json")                   # sets Content-Type, expands extensions
res.vary("Accept-Encoding")        # adds without duplicating
```

## Caching and validators

```python
res.cache(3600)                            # public, max-age=3600
res.cache(31536000, immutable=True)        # for content-hashed assets
res.no_cache()                             # no-store, no-cache, must-revalidate
res.etag()                                 # computed from the body
res.etag("v3", weak=True)                  # or supplied
```

### Conditional requests are answered for you

Setting an `ETag` and then sending the body anyway saves nothing, so the
framework compares it against the request:

```python
@app.get("/config")
def config(res):
    return res.etag().json(load_config())
```

A `GET` or `HEAD` whose `If-None-Match` matches gets a `304` with no body.
Comparison is RFC 9110 weak comparison, so `W/"abc"` and `"abc"` are the same
version, `*` matches anything, and a comma-separated list matches if any entry
does. With no `ETag` set, `If-Modified-Since` is compared against
`Last-Modified` instead.

Only safe methods are downgraded. Answering a `POST` with `304` would report a
write as a no-op.

A computed tag is deferred until send time, so both orders give the same
answer:

```python
res.etag().json(payload)     # same tag
res.json(payload).etag()     # as this
```

Hashing eagerly would make the first line hash an empty body — one tag shared
by every such response, which is a cache-poisoning bug wearing the costume of a
working `ETag`.

## Cookies

```python
res.cookie("session", token, max_age=86400, secure=True, samesite="lax")
res.clear_cookie("session")
```

Defaults are the secure ones: `HttpOnly` on, `SameSite=Lax`. `SameSite=None`
without `secure=True` raises rather than silently producing a cookie browsers
will drop. Multiple cookies produce multiple `Set-Cookie` headers, as required.

## Downloads

```python
res.attachment("report.csv")       # Content-Disposition, with UTF-8 filename*
```

## Response classes

For the return-a-value style, or when you want to construct a response
directly:

```python
from slowapi import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    NoContentResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)


@app.get("/report")
def report() -> FileResponse:
    return FileResponse("reports/2026.pdf", filename="annual-report.pdf")
```

## Serving files

```python
@app.get("/videos/{name}")
def video(name: str, req) -> FileResponse:
    return FileResponse(f"media/{name}.mp4", range_header=req.get("range"))
```

Passing `range_header` is what makes `<video>` seeking and resumable downloads
work: the response becomes a `206` with a `Content-Range`, or a `416` if the
range is unsatisfiable. `ETag` and `Last-Modified` are set from the file's
metadata, and the file is read in 64 KB chunks rather than loaded whole.

For a whole directory, use `app.mount_static(...)` instead — it adds
conditional-request handling and path-traversal protection.

## Streaming

```python
@app.get("/export")
def export(res):
    def rows():
        for row in cursor:                     # a database cursor
            yield (json.dumps(row) + "\n").encode()

    res.stream(rows(), media_type="application/x-ndjson")
```

Sync generators and async generators both work, under both protocols. Streaming
removes `Content-Length`, so the server uses chunked encoding.

Server-sent events are just a stream with the right media type:

```python
@app.get("/events")
async def events() -> StreamingResponse:
    async def gen():
        while True:
            yield f"data: {json.dumps(next_event())}\n\n".encode()
            await asyncio.sleep(1)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"cache-control": "no-store", "x-accel-buffering": "no"},
    )
```

`x-accel-buffering: no` tells nginx not to buffer the stream, which is the
usual reason SSE "works locally and not in production".

## Background work

```python
@app.post("/signup")
def signup(res, email: str) -> None:
    def send_welcome():
        mailer.send(email)

    res.background = send_welcome
    res.status(202).json({"queued": email})
```

The callable runs after the response has been flushed, on both protocols, and
may be `async def`. It is not a task queue: if the process dies, the work is
lost. Use it for best-effort work — a metric, a cache warm, a non-critical
email — and a real queue for anything else.

For more than one piece of work, declare a `BackgroundTasks` parameter instead
of assigning a single callable; it queues several, keeps them in order, and
does not let one failure cancel the rest. See
[Background tasks](background-and-health.md#background-tasks).

## Empty responses

Returning `None` from a handler that declared a `res` parameter means "I wrote
to `res`". Returning `None` from a handler that did **not** take `res` produces
a `204 No Content`, because there is nothing else it could reasonably mean.

## Status codes without bodies

`204`, `304`, and the `1xx` range must not carry a body, per RFC 9110. SlowAPI
drops the body and the `Content-Length`/`Content-Type` headers automatically
rather than emitting a response that proxies will reject.
