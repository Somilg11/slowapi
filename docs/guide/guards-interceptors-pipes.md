# Guards, interceptors, and pipes

Three hooks for cross-cutting concerns, each at a different point in the
request. The order is the design:

```
middleware
   └─ route matched
        └─ GUARD        may this proceed?          (nothing injected yet)
             └─ INTERCEPTOR (before)
                  └─ PIPE   transform one argument (after coercion)
                       └─ HANDLER
                  └─ INTERCEPTOR (after)           sees the returned object
        └─ response
```

A guard runs before the body is parsed, before dependencies are constructed,
and before the handler. That ordering is the point: rejecting an unauthorised
request should never cost a database connection.

## Guards

A guard answers one question — *may this request proceed?*

```python
from slowapi import ExecutionContext, injectable, use_guards


@injectable()
class RoleGuard:
    def __init__(self, users: UserService) -> None:
        self.users = users

    async def can_activate(self, ctx: ExecutionContext) -> bool:
        if ctx.get("public"):
            return True
        required = ctx.get("roles", [])
        if not required:
            return True
        user = await self.users.from_token(ctx.request.get("authorization"))
        return bool(user and set(required) & set(user.roles))
```

Returning `False` produces a `403` naming the guard. Raise an `HTTPException`
for anything more specific:

```python
if token is None:
    raise Unauthorized("Send an Authorization header")     # 401, not 403
```

Guards are injectable, so policy can consult real services instead of
duplicating what they know.

### Attaching

```python
@controller("/admin")
@use_guards(RoleGuard)          # every route in the controller
class AdminController:
    @Get("/logs")
    @use_guards(AuditGuard)     # this route as well
    def logs(self): ...
```

Class-level guards run first, then method-level, in registration order.

### Metadata

`@roles(...)`, `@public`, and `@set_metadata(key, value)` **record** metadata;
they decide nothing. A guard reads it via `ctx.get(...)`:

```python
@Get("/reports")
@roles("admin", "auditor")
@set_metadata("audit_level", "high")
def reports(self): ...
```

```python
ctx.get("roles")           # ["admin", "auditor"]
ctx.get("audit_level")     # "high"
ctx.get("public", False)
```

Keeping the vocabulary separate from the policy is what lets one set of
annotations serve JWTs today and a session store tomorrow. Handler metadata
overrides controller metadata of the same key.

### Built-ins

`AllowAll`, `DenyAll`, `RequireHeader("X-API-Key", "secret")` — useful as
defaults and for disabling a route quickly.

## Interceptors

An interceptor wraps the handler call and sees the *return value*, which is
what separates it from middleware.

```python
from slowapi.interceptors import Interceptor


class Envelope(Interceptor):
    async def intercept(self, ctx, call_next):
        data = await call_next()
        return {"data": data, "requestId": ctx.request.request_id}


@app.get("/users")
@use_interceptors(Envelope())
def users() -> list[User]: ...
```

Interceptors nest: the first registered is outermost.

### Built-ins

```python
from slowapi.interceptors import CacheInterceptor, EnvelopeInterceptor, TimingInterceptor

@use_interceptors(
    TimingInterceptor(),                 # X-Handler-Time, and request.state.handler_ms
    EnvelopeInterceptor(key="data"),     # consistent response envelope
    CacheInterceptor(ttl=5.0),           # in-process cache for GET
)
```

`CacheInterceptor` is per-process; for anything multi-process, write one
against Redis using it as the template.

### Good uses

- Response envelopes and pagination wrappers
- Timing and tracing spans around the handler specifically
- Audit trails that need the returned object
- Caching keyed on the route rather than the raw URL

An interceptor that returns a `Response` object passes it through untouched, so
file downloads and streams are unaffected by an envelope.

## Pipes

A pipe transforms or validates a single argument, after type coercion.

```python
from slowapi.pipes import Pipe, use_pipes


class NormaliseEmail(Pipe):
    def transform(self, value, meta):
        if meta.name == "email" and isinstance(value, str):
            return value.strip().lower()
        return value


@app.post("/signup")
@use_pipes(NormaliseEmail())
def signup(email: str) -> dict: ...
```

`meta` is an `ArgumentMetadata(location, name, annotation)`, so one pipe can
behave differently depending on where the value came from.

### Built-ins

`TrimPipe`, `LowercasePipe`, `ParseIntPipe`, `DefaultValuePipe(x)`,
`NotEmptyPipe`, `ClampPipe(minimum=1, maximum=100)`.

`ClampPipe` is worth calling out: it narrows instead of rejecting, which is
usually right for pagination — `?limit=100000` should return the maximum page,
not a `422`.

### Pipes or annotations?

Annotations already coerce and validate. Reach for a pipe when the rule is not
expressible as a type — normalisation, app-specific formats, clamping — or when
you want one transformation reused across many handlers.

## Choosing between the three

| You want to… | Use |
| --- | --- |
| Set a response header on every request | Middleware |
| Reject a request without touching the database | Guard |
| Wrap every payload in an envelope | Interceptor |
| Trim whitespace from an argument | Pipe |
| Compress the response body | Middleware |
| Cache what a handler returned | Interceptor |
| Enforce `1 <= limit <= 100` | An annotation, or `ClampPipe` |
