# Validation

Everything on the wire is text. Validation is the layer that turns text into
the types your function actually declared, and rejects what cannot be turned.

In SlowAPI there is no separate schema to keep in sync: the annotation is the
schema, and it drives coercion, the error response, and the OpenAPI document
together.

## Declaring parameters

```python
from slowapi import Body, Cookie, Header, Path, Query


@app.get("/items/{id:int}")
def get_item(
    id: int,                                       # path, by name
    fields: str = Query("id,name"),                # query, with a default
    x_api_key: str = Header(...),                  # header, required
    session: str = Cookie(None),                   # cookie, optional
) -> dict: ...
```

`...` means required. A missing required parameter is a `422` naming it.

### `Annotated`: the preferred spelling

Putting the marker in the type instead of the default is the better form, and
both work identically:

```python
from typing import Annotated

@app.get("/search")
def search(
    q: Annotated[str, Query(min_length=2)],        # required, and still first
    tags: Annotated[list[str], Query()] = (),
    page: int = 1,
    user: Annotated[User, Depends(current_user)],
    settings: Annotated[Settings, Inject(SETTINGS)],
): ...
```

Two things get better. A marked parameter can precede an unmarked one, because
it no longer occupies the default slot — with `q: str = Query(...)`, every
parameter after `q` must also have a default. And the default stays an actual
default: `page: int = 1` says what it means, rather than `page: int = Query(1)`
saying it twice.

Metadata SlowAPI does not recognise passes through untouched, so annotations
shared with other tools keep working:

```python
def handler(user_id: Annotated[int, "the caller's id", SomeOtherTool()]): ...
```

Declaring a marker in both places is a `ConfigurationError` at startup rather
than a silent precedence rule:

```python
q: Annotated[str, Query()] = Query()    # refused: pick one
```

## How a parameter's source is decided

When you do not say explicitly, SlowAPI infers, in this order:

1. An explicit marker (`Query()`, `Header()`, `Depends()`, …) wins.
2. `Request` / `Response` / `ExecutionContext` annotations receive those
   objects — as do unannotated parameters named `req`, `res`, `ctx`.
3. A name that is a path parameter of the route is a path parameter.
4. A DTO annotation on `POST`/`PUT`/`PATCH`/`DELETE` is the request body.
5. An annotation the DI container knows is injected.
6. On `POST`/`PUT`/`PATCH`/`DELETE`, a bare scalar is read from the JSON body,
   falling back to the query string.
7. Everything else is a query parameter.

Rule 6 is the one that differs from FastAPI, and it is deliberate: a scalar on
a POST almost always comes from the body, and that is what an Express or Nest
developer expects. The query fallback means you lose nothing.

## Coercion

| Declared | `"42"` | `"true"` | `"2024-01-02T03:04:05Z"` |
| --- | --- | --- | --- |
| `int` | `42` | error | error |
| `bool` | `True` | `True` | error |
| `datetime` | epoch seconds | error | an aware `datetime` |

Supported out of the box: `str`, `bytes`, `int`, `float`, `bool`, `Decimal`,
`UUID`, `datetime`, `date`, `time`, `timedelta`, `IPv4Address`, `IPv6Address`,
`Enum`, `Literal`, `Optional[X]`, `X | Y`, `list[X]`, `set[X]`, `tuple[X, Y]`,
`dict[K, V]`, dataclasses, `TypedDict`, and Pydantic models.

Booleans accept `1/true/t/yes/y/on` and `0/false/f/no/n/off`. Anything else is
an error, not a guess — `?active=maybe` should not silently mean `False`.

## Constraints

```python
Query(10, ge=1, le=100)                     # numeric bounds
Query(..., min_length=2, max_length=64)     # length
Query(..., pattern=r"^[a-z0-9-]+$")         # regex
Query("id", examples=["id", "name"])        # documented examples
Query(None, deprecated=True)                # documented as deprecated
Query(default_factory=list)                 # mutable defaults, safely
```

All of them appear in the OpenAPI schema as well as being enforced.

## Repeated values

```python
@app.get("/posts")
def posts(tag: list[str] = Query(default_factory=list)) -> dict:
    return {"tags": tag}
```

`?tag=python&tag=web` produces `["python", "web"]`. A `list[...]` annotation is
what switches on collection; without it you get the last value.

## Request bodies

A dataclass is a DTO. No base class, no metaclass:

```python
from dataclasses import dataclass, field


@dataclass
class CreateUser:
    email: str
    age: int = 18
    tags: list[str] = field(default_factory=list)


@app.post("/users")
def create(payload: CreateUser) -> dict:
    return {"email": payload.email}
```

Nested models work, and errors point at the exact field:

```python
@dataclass
class Address:
    city: str
    postcode: str


@dataclass
class Customer:
    name: str
    address: Address
```

```json
{
  "error": {
    "code": "validation_error",
    "status": 422,
    "message": "Request validation failed",
    "details": [
      {"loc": ["body", "payload", "address", "postcode"], "message": "Field is required", "type": "missing"}
    ],
    "requestId": "8f1c..."
  }
}
```

### Every error at once

Validation collects *all* failures before responding. A form with four bad
fields is one round trip, not four.

### Rejecting unknown fields

By default extra keys are ignored, which is what most APIs want for forward
compatibility. To be strict:

```python
@dataclass
class StrictPayload:
    __slowapi_forbid_extra__ = True
    name: str
```

### Embedded scalars

```python
@app.post("/rename")
def rename(name: str = Body(..., embed=True)) -> dict:
    return {"name": name}
```

Reads `{"name": "..."}` rather than treating the whole body as the value.

## Pydantic

If Pydantic is installed, its models work anywhere a dataclass does:

```python
from pydantic import BaseModel, EmailStr


class CreateUser(BaseModel):
    email: EmailStr
    age: int = 18


@app.post("/users")
def create(payload: CreateUser) -> dict: ...
```

SlowAPI detects them by duck typing (`model_validate`) and delegates
validation and schema generation. It never imports Pydantic unless your own
annotations refer to it.

Use Pydantic when you want its validator ecosystem. Use dataclasses when you
want zero dependencies. Both are supported; neither is the "real" way.

## Error format

Every error the framework produces has one shape:

```json
{"error": {"code": "not_found", "status": 404, "message": "...", "requestId": "..."}}
```

`code` is stable and machine-readable; `message` is for humans. Validation
errors add `details`. To change the format globally, register an
[exception handler](errors.md).

## What validation does not do

It does not check business rules. `age >= 18` is a constraint; "this email is
already registered" is not — that needs a database and belongs in a service.
The dividing line is whether the check can be answered from the request alone.
