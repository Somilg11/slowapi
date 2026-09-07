# Serialization

The most common security bug in a JSON API is an over-serialised model: a
password hash, an internal flag, or another tenant's id, returned because a
handler forwarded a database row verbatim.

SlowAPI's answer is to make the safe thing the short thing. Describe exposure
once on the DTO, and every handler that returns it is shaped the same way.

## Field rules

```python
from dataclasses import dataclass, field
from datetime import datetime

from slowfw import expose, hidden


@dataclass
class User:
    id: int
    name: str
    email: str = field(metadata=expose(groups=("self", "admin")))
    password_hash: str = field(default="", metadata=hidden())
    created: datetime = field(default=None, metadata=expose(alias="createdAt"))
    balance: int = field(default=0, metadata=expose(transform=lambda c: c / 100))
```

| Helper | Effect |
| --- | --- |
| `hidden()` | Never serialised, in any context |
| `expose(groups=(...))` | Only serialised when the caller has one of those groups |
| `expose(alias="x")` | Renamed on the way out |
| `expose(transform=fn)` | Value passed through `fn` first |

Fields with no metadata are serialised normally.

```python
serialize(user)                      # {"id":1,"name":"Ada","createdAt":"...","balance":0.0}
serialize(user, groups=("admin",))   # ...plus "email"
```

`password_hash` never appears. Leaking it takes a deliberate act.

## Automatic serialisation

Returned values are serialised automatically:

```python
@app.get("/users/:id")
def show(id: int) -> User:
    return self.users.find(id)        # shaped by the rules above
```

Turn it off with `SlowAPI(auto_serialize=False)` if you want to control every
response body yourself.

## Per-route options

```python
from slowfw import serialize_with


@Get("/directory")
@roles("admin")
@serialize_with(groups=("admin",))
def directory(self) -> list[User]:
    return self.users.all()
```

Same objects, more fields, one decorator apart — no second DTO and no mapping
function to keep in sync.

Other options: `include={...}`, `exclude={...}`, `exclude_none=True`. Applied
to a controller class, they cover every route in it; a method-level decorator
overrides.

## Doing it manually

```python
from slowfw import serialize

payload = serialize(user, groups=("self",), exclude={"balance"})
```

Useful in background jobs, message payloads, and anywhere else the same shaping
rules should apply outside a request.

## What is serialisable

`serialize` handles dataclasses, dicts, lists, tuples, sets, enums,
`datetime`/`date`/`time`, `UUID`, `Decimal`, and Pydantic models, recursing all
the way down. Anything else is returned unchanged and must be JSON-encodable.

## Input versus output

The rules above are about **output**. Input shaping is
[validation](validation.md), and the two are deliberately separate: the fields
you accept and the fields you return are different sets in most real APIs.

The `write_only` flag on `hidden()` names that case exactly — a password is
accepted on input and never returned:

```python
@dataclass
class CreateUser:
    email: str
    password: str = field(metadata=hidden(write_only=True))
```

## A caution

Serialisation rules are the last line, not the only one. A field that must
never reach a particular caller should also not be loaded into a DTO that
caller can reach. Defence in depth: query narrowly, then shape.
