# Routing

## Registering a route

```python
@app.get("/health")
def health() -> dict:
    return {"ok": True}
```

One decorator exists per HTTP method: `get`, `post`, `put`, `patch`, `delete`,
`head`, `options`. Each returns the handler unchanged, so decorators stack:

```python
@app.get("/admin")
@app.get("/administrator")     # same handler, two paths
@use_guards(AdminGuard)
def admin(res):
    res.text("ok")
```

For several methods at once:

```python
@app.route("/resource", methods=["PUT", "PATCH"])
def upsert(res):
    res.status(204).end()
```

## Path parameters

Two syntaxes are accepted and are exactly equivalent — they compile to the same
route. Use whichever your team already reads fluently.

```python
@app.get("/users/{id}")        # FastAPI / Starlette style
@app.get("/users/:id")         # Express style
```

### Converters

Add `:converter` to type and constrain a parameter *before* matching:

```python
@app.get("/users/{id:int}")
@app.get("/posts/{slug:slug}")
@app.get("/orders/{ref:uuid}")
@app.get("/assets/{path:path}")
@app.get("/codes/{code:re:[A-Z]{3}-[0-9]{4}}")
```

| Converter | Matches | Arrives as |
| --- | --- | --- |
| `str` (default) | anything except `/` | `str` |
| `int` | `[0-9]+` | `int` |
| `float` | `1` or `1.5` | `float` |
| `uuid` | a canonical UUID | `uuid.UUID` |
| `slug` | `lower-case-with-dashes` | `str` |
| `path` | anything, including `/` | `str` |
| `re:<pattern>` | your pattern | `str` |

Converters run at match time, which changes the failure mode in a way worth
internalising:

```python
@app.get("/users/{id:int}")
def get_user(id: int) -> dict: ...
```

`GET /users/abc` is a **404**, because no route matched. Without the converter
it would be a 422 from validation. Neither is wrong; the converter version is
cheaper and reads better in logs.

A `path` converter is greedy and must be the last segment:

```python
@app.get("/files/{path:path}")     # /files/a/b/c.txt -> path == "a/b/c.txt"
```

### Partial segments

A parameter does not have to fill a whole segment:

```python
@app.get("/report.{fmt}")          # /report.csv -> fmt == "csv"
@app.get("/{year:int}-{month:int}")
```

## How matching works

Paths are compiled once, at registration, into a trie of segments. Matching
costs time proportional to the number of segments in the *request*, not the
number of routes in the application — a thousand routes match as fast as ten.

Within one segment the order is: literal, then parameterised, then a greedy
`path` catch-all.

```python
@app.get("/users/me")          # wins for /users/me
@app.get("/users/{id:int}")    # wins for /users/42
@app.get("/users/{name}")      # wins for /users/ada
```

Backtracking is real: if `{id:int}` matches a segment but nothing deeper
matches, the router falls back and tries `{name}`.

## Methods that are handled for you

- **`HEAD`** falls back to the `GET` route and the body is dropped, per RFC 9110.
- **`405`** responses carry a correct `Allow` header listing what *is* allowed.
- **`OPTIONS`** is included in `Allow` even if you did not register it.

## Routers

Group routes and merge them under a prefix:

```python
# users/routes.py
from slowfw import Router

users = Router(prefix="/users", tags=["users"])


@users.get("/{id:int}", name="user_detail")
def get_user(id: int) -> dict:
    return {"id": id}


@users.post("", status_code=201)
def create_user(payload: CreateUser) -> dict: ...
```

```python
# main.py
from users.routes import users

app.include_router(users, prefix="/api/v1")     # -> /api/v1/users/{id}
```

A router carries its own prefix, tags, and middleware, and merging applies all
three. Routers nest, so a `v1` router can include a `users` router.

In practice, most applications that need this reach for
[controllers](controllers-modules.md) instead, which give the same grouping
plus dependency injection.

## Reverse URLs

Never build a path with an f-string; ask the router:

```python
@app.get("/users/{id:int}", name="user_detail")
def get_user(id: int) -> dict: ...


app.url_for("user_detail", id=7)      # "/users/7"
```

Rename the path and every `url_for` follows. Forget a parameter and you get a
`ConfigurationError` naming it, at the call site, rather than a broken link in
production. Route names default to the handler's function name.

## Static files

```python
app.mount_static("/assets", "public", max_age=31536000, immutable=True)
```

Mounts a `{path:path}` route that serves `public/`, with ETags, `Last-Modified`,
conditional `304` responses, and byte-range support. Every candidate path is
resolved and checked against the root, so `..` traversal and symlinks pointing
outside the directory are refused. See
[Templates and static files](templates-static.md).

## Common errors, and what they mean

| Message | Cause |
| --- | --- |
| `Route path must start with '/'` | You wrote `"users/{id}"` |
| `Unknown path converter 'x'` | Typo, or you wanted `re:` |
| `A 'path' converter must be the last segment` | `{p:path}` is not final |
| `Duplicate route: GET /x is already handled by ...` | Two handlers, one method and path |
| `Duplicate path parameter(s) ['id']` | `/{id}/y/{id}` |
| `Route name 'x' is already used by ...` | Two routes, same `name=` |

All of these raise at import time, not on the first request.
