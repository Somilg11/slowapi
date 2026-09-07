# Controllers and modules

Function handlers are the right tool up to a few dozen endpoints. Past that,
you want the code organised by feature, with each feature declaring what it
provides and what it depends on. That is what controllers and modules are for.

Nothing here replaces the functional API — the two mix in one application.

## Controllers

```python
from slowfw import Delete, Get, Patch, Post, controller, http_code


@controller("/users", tags=["users"])
class UserController:
    def __init__(self, users: UserService) -> None:
        self.users = users                         # constructor injection

    @Get("")
    def index(self, limit: int = Query(20, le=100)) -> list[User]:
        return self.users.all(limit)

    @Get("/:id")
    def show(self, id: int) -> User:
        return self.users.find(id)

    @Post("")
    @http_code(201)
    def create(self, payload: CreateUser) -> User:
        return self.users.create(payload)

    @Delete("/:id")
    def destroy(self, id: int, res) -> None:
        self.users.delete(id)
        res.status(204).end()
```

- The class prefix and each method's path are joined: `@Get("/:id")` on a
  `@controller("/users")` becomes `/users/{id}`.
- The controller is built by the container, so its constructor can inject
  anything a provider offers.
- Method parameters follow exactly the same rules as function handlers.

Register it:

```python
app = SlowAPI(controllers=[UserController])
# or
app.register_controller(UserController)
```

### Method decorators

`Get`, `Post`, `Put`, `Patch`, `Delete`, `Head`, `Options`, and `route` for
several at once. Each accepts the same metadata as the functional decorators:

```python
@Get("/:id", summary="Fetch one user", tags=["public"], deprecated=True)
```

### Versioning

```python
@controller("/users", version="v2")
class UserControllerV2: ...        # mounted at /v2/users
```

### Inheritance

A base class's routes are inherited, which is how shared CRUD scaffolding is
usually expressed:

```python
class ReadOnlyController:
    @Get("")
    def index(self): ...

    @Get("/:id")
    def show(self, id: int): ...


@controller("/articles")
class ArticleController(ReadOnlyController):
    @Post("")
    def create(self, payload: CreateArticle): ...
```

## Modules

A module groups controllers, declares providers, and states its boundary.

```python
from slowfw import Provider, module


@module(
    imports=[CoreModule],
    controllers=[UserController],
    providers=[UserService, RoleGuard, Provider.value(SETTINGS, settings)],
    exports=[UserService],
    middlewares=[audit],
    prefix="/api",
)
class UserModule:
    """Everything about users, and nothing else."""
```

| Field | Meaning |
| --- | --- |
| `imports` | Other modules, registered before this one |
| `controllers` | Controller classes whose routes this module contributes |
| `providers` | Classes or `Provider` instances this module owns |
| `exports` | The subset of `providers` other modules may rely on |
| `middlewares` | Middleware applied to this module's routes only |
| `prefix` | Path prefix for every controller in the module |

```python
app = SlowAPI(modules=[CoreModule, UserModule, BillingModule])
```

Importing the same module from two places registers it once; diamond imports
are fine.

### What `exports` buys you

Exporting something a module does not provide raises at import time:

```
ConfigurationError: UserModule exports ['Auditor'] which it does not provide.
Add them to providers=, or re-export by importing their module.
```

The value is documentary and enforced at the boundary: a reader can see what a
module offers without reading its implementation, and a typo is caught at
startup rather than becoming an accidental public API.

## Suggested layout

```
src/myapp/
├── main.py                  # SlowAPI(modules=[...]) and nothing else
├── core/
│   ├── module.py            # CoreModule: config, logging, database
│   ├── database.py
│   └── settings.py
├── users/
│   ├── module.py            # UserModule
│   ├── controller.py
│   ├── service.py
│   ├── models.py            # DTOs
│   └── guards.py
└── billing/
    ├── module.py
    ├── controller.py
    └── service.py
```

Each feature directory is self-contained. `main.py` composes them:

```python
from slowfw import SlowAPI

from myapp.billing.module import BillingModule
from myapp.core.module import CoreModule
from myapp.users.module import UserModule

app = SlowAPI(
    title="MyApp",
    version="1.0.0",
    modules=[CoreModule, UserModule, BillingModule],
)
```

## Legacy class routing

The original class syntax still works, mapping verb-named methods to routes:

```python
@app.route("/users")
class Users:
    def get(self, req, res): ...
    def post(self, req, res): ...
```

Prefer `@controller` for new code: it supports injection, per-method paths, and
metadata.

## When to move from functions to controllers

Not on a rule, on a symptom:

- The same three dependencies appear in every handler's signature.
- You have started grouping handlers with comment banners.
- Two features need different authorisation policies.
- A file has more than a few hundred lines of handlers.

Until then, functions are less code and read better.
