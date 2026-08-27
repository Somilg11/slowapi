# Errors

Two rules govern what a client is told:

1. An `HTTPException` is a **deliberate** answer, so its detail is safe to
   show.
2. Anything else is a **bug**. The traceback goes to the log with a request id;
   the client gets the id and nothing more.

Stack traces leak file paths, library versions, and sometimes credentials.
`debug=True` relaxes rule 2 for local work.

## Raising

```python
from slowapi import BadRequest, Forbidden, NotFound, Unauthorized


@app.get("/users/{id:int}")
def get_user(id: int) -> User:
    user = store.get(id)
    if user is None:
        raise NotFound(f"No user with id {id}")
    return user
```

Available: `BadRequest` (400), `Unauthorized` (401), `Forbidden` (403),
`NotFound` (404), `MethodNotAllowed` (405), `PayloadTooLarge` (413),
`UnsupportedMediaType` (415), `ValidationError` (422), `TooManyRequests` (429).
For anything else, `HTTPException(status_code, detail)`.

`Unauthorized` sets `WWW-Authenticate`; `TooManyRequests(retry_after=30)` sets
`Retry-After`; `MethodNotAllowed` sets `Allow`. Getting those headers right is
the difference between a client that backs off and one that hammers you.

## The error body

```json
{
  "error": {
    "code": "not_found",
    "status": 404,
    "message": "No user with id 42",
    "requestId": "8f1c2d3e4a5b6c7d8e9f0a1b2c3d4e5f"
  }
}
```

`code` is stable and machine-readable — clients should branch on it, not on the
message. `requestId` is the same value as the `X-Request-ID` response header
and the `requestId` field in the access log, which is what makes a support
ticket answerable.

## Custom error responses

By exception type:

```python
@app.exception_handler(NotFound)
def handle_not_found(req, exc, res):
    return res.json({"detail": exc.detail, "path": req.path}, 404)
```

By status code:

```python
@app.exception_handler(500)
def handle_server_error(req, exc, res):
    return res.json({"message": "Something went wrong. We have been notified."}, 500)
```

By your own exception type — the usual way to keep domain errors out of the
transport layer:

```python
class InsufficientFunds(Exception):
    def __init__(self, shortfall: int) -> None:
        self.shortfall = shortfall


@app.exception_handler(InsufficientFunds)
def handle_funds(req, exc, res):
    return res.json({"code": "insufficient_funds", "shortfall": exc.shortfall}, 402)
```

Handlers are matched along the exception's MRO, so registering a base class
covers its subclasses. Handlers may be sync or async, and may return a
`Response`, a dict (serialised as JSON), or `None` (having mutated `res`).

## Domain errors

Two workable patterns:

**Raise HTTP exceptions from services.** Fewer layers, and honest for an
application whose only interface is HTTP.

```python
class UserService:
    def find(self, user_id: int) -> User:
        try:
            return self._rows[user_id]
        except KeyError:
            raise NotFound(f"No user with id {user_id}") from None
```

**Raise domain exceptions and translate at the edge.** Better when the same
service is also used by a CLI, a worker, or a gRPC surface.

```python
class UserNotFound(DomainError): ...


@app.exception_handler(UserNotFound)
def handle(req, exc, res):
    return res.json({"code": "user_not_found"}, 404)
```

Pick one per codebase. Mixing them is how you end up with two error formats.

## Validation errors

A `422` lists every failure at once:

```json
{
  "error": {
    "code": "validation_error",
    "status": 422,
    "message": "Request validation failed",
    "details": [
      {"loc": ["query", "limit"], "message": "Must be <= 100", "type": "le"},
      {"loc": ["body", "payload", "email"], "message": "Field is required", "type": "missing"}
    ]
  }
}
```

`loc` is a path: source, then parameter, then nested fields. `type` is stable,
so a client can map `missing` to its own message without parsing English.

To reshape it globally, register a handler for `ValidationError`.

## Errors during startup

Configuration mistakes raise `ConfigurationError` at import or startup, never
on a request: duplicate routes, unknown converters, missing providers, circular
dependencies, an unresolvable annotation. The messages name the fix. A process
that starts is a process whose wiring is sound.

## Logging

Unhandled exceptions are logged at `ERROR` with the traceback, path, method and
request id. Deliberate `5xx` responses are logged too; `4xx` are not, because
they are the client's problem and would otherwise drown the log.

```python
from slowapi.logging import configure_logging

configure_logging("INFO", json_output=True, service="api", version="1.4.0")
```
