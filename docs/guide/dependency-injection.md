# Dependency injection

SlowAPI has two injection systems, and they cooperate. Knowing which to reach
for is most of the learning.

| | `Depends(fn)` | The container |
| --- | --- | --- |
| Comes from | FastAPI | NestJS |
| Declared | per parameter | per module |
| Resolved by | calling a function | building a class |
| Lifetime | per request | singleton / request / transient |
| Best for | request-derived values | services and clients |

`Depends` answers "compute this from the request". The container answers "give
me the thing this application already has".

## `Depends`

A dependency is any callable. Its own parameters are resolved the same way a
handler's are, recursively.

```python
from slowfw import Depends, Query


def pagination(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    return {"limit": limit, "offset": offset}


@app.get("/users")
def list_users(paging: dict = Depends(pagination)) -> dict:
    return paging
```

`limit` and `offset` are validated, documented in the OpenAPI schema, and
enforced everywhere the dependency is used — declared once.

### Caching

Within one request, a dependency is called once no matter how many places
depend on it:

```python
@app.get("/dashboard")
def dashboard(
    a: User = Depends(current_user),
    b: User = Depends(current_user),      # the same object; called once
) -> dict: ...
```

Opt out with `Depends(current_user, use_cache=False)`.

### Teardown

A generator dependency yields the value and cleans up after the response is
sent — even if the handler raised:

```python
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.post("/users")
def create(payload: CreateUser, db=Depends(get_session)) -> dict: ...
```

`async def` generator dependencies work identically.

### Dependencies that authorise

```python
def current_user(authorization: str = Header(...)) -> User:
    user = decode(authorization)
    if user is None:
        raise Unauthorized("Invalid token")
    return user


@app.get("/me")
def me(user: User = Depends(current_user)) -> User:
    return user
```

This works, and for small applications it is the right amount of machinery.
Past that, prefer a [guard](guards-interceptors-pipes.md) — guards run before
anything is injected, so rejecting a request costs nothing.

## The container

### Providers

```python
from slowfw import InjectionToken, Provider, injectable


@injectable()                       # singleton by default
class UserService:
    def __init__(self, db: Database) -> None:
        self.db = db
```

Four ways to register:

```python
Provider(UserService)                                  # build the class
Provider.value(SETTINGS, Settings(...))                # a ready-made object
Provider.factory(Database, make_db, inject=[SETTINGS]) # call something
Provider.klass(Store, RedisStore)                      # bind interface to impl
```

Tokens do not have to be classes:

```python
DATABASE_URL = InjectionToken("DATABASE_URL")

Provider.value(DATABASE_URL, os.environ["DATABASE_URL"])
```

Use a token when the thing you inject is a value, or when the consumer should
depend on an interface rather than a concrete class.

### Injecting

Into a controller or service constructor, by annotation:

```python
@controller("/users")
class UserController:
    def __init__(self, users: UserService) -> None:
        self.users = users
```

Into a function handler, also by annotation:

```python
@app.get("/audit")
def audit(auditor: Auditor) -> dict:
    return {"entries": auditor.entries}
```

By token, when the annotation is not the token:

```python
from slowfw import Inject


class UserService:
    def __init__(self, dsn: str = Inject(DATABASE_URL)) -> None: ...
```

### Scopes

```python
@injectable(scope="singleton")     # default: one per process
@injectable(scope="request")       # one per request, torn down after
@injectable(scope="transient")     # a new one at every injection site
```

Choose `singleton` for anything stateless or pooled. Choose `request` for
things that carry request identity — a unit of work, a tenant-scoped client.
`transient` is rarely what you want.

Request-scoped instances live in a child container created per request and
disposed when the response is flushed.

### Factory providers with cleanup

```python
def make_pool(dsn: str = Inject(DATABASE_URL)):
    pool = create_pool(dsn)
    yield pool
    pool.close()


Provider.factory(Pool, make_pool, scope="singleton")
```

Singleton generators are closed during application shutdown; request-scoped
ones at the end of each request.

### Lifecycle hooks

```python
@injectable()
class Cache:
    async def on_module_init(self) -> None:
        self.client = await connect()

    async def on_module_destroy(self) -> None:
        await self.client.close()
```

Every singleton is built at startup and its `on_module_init` awaited, so a
misconfigured dependency fails at boot rather than on the first request that
happens to touch it.

## Registering providers

Inside a module, which is the usual way:

```python
@module(providers=[UserService, Provider.value(SETTINGS, settings)], exports=[UserService])
class UserModule: ...
```

Or directly, which is convenient in scripts and tests:

```python
app.provide(UserService)
app.provide(SETTINGS, Settings(environment="test"))
```

## Errors you will meet

| Message | Meaning |
| --- | --- |
| `No provider registered for X` | The token is not in any module's `providers` |
| `Circular dependency detected: A -> B -> A` | Break the cycle, or inject a factory |
| `Cannot inject parameter 'x': no type annotation and no default` | Annotate it |
| `X exports [Y] which it does not provide` | Add `Y` to that module's `providers` |

All of these are raised at import or startup, not on a request.

## Overriding in tests

```python
app.provide(Provider.klass(EmailService, FakeEmailService))
```

Registering the same token again replaces the previous provider, which is what
makes swapping a real client for a fake a one-liner. See [Testing](testing.md).
