# Migrating to SlowAPI

Guides for the four frameworks SlowAPI borrows from. In each case the goal is
to show what carries over unchanged, since that is usually more than people
expect.

## From Flask

```python
# Flask
from flask import Flask, jsonify, request

app = Flask(__name__)


@app.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    return jsonify({"id": user_id})


@app.route("/users", methods=["POST"])
def create_user():
    data = request.get_json()
    return jsonify(data), 201
```

```python
# SlowAPI
from slowapi import SlowAPI

app = SlowAPI()


@app.get("/users/{user_id:int}")
def get_user(user_id: int) -> dict:
    return {"id": user_id}


@app.post("/users", status_code=201)
def create_user(payload: CreateUser) -> dict:
    return {"email": payload.email}
```

| Flask | SlowAPI |
| --- | --- |
| `@app.route(..., methods=["GET"])` | `@app.get(...)` |
| `<int:id>` | `{id:int}` or `:id` |
| `request` (a proxy) | a `req` parameter, or declared parameters |
| `jsonify(x)` | return `x`, or `res.json(x)` |
| `return x, 201` | `status_code=201`, or `res.status(201)` |
| `abort(404)` | `raise NotFound(...)` |
| `url_for` | `app.url_for` |
| `render_template` | `res.render` |
| `session` | `req.state.session` with `SessionMiddleware` |
| `g` | `req.state` |
| `before_request` | middleware, or a `(req, res)` function |
| Blueprints | `Router`, or `@controller` |

**What you gain:** typed parameters, validation, OpenAPI, DI, async handlers.
**What you give up:** the extension ecosystem. Check yours before committing.

There is no request-context proxy: `req` is passed explicitly. That is more
typing and considerably easier to test.

## From FastAPI

The parameter API is intentionally close, so most handlers move unchanged.

```python
# FastAPI
from fastapi import Depends, FastAPI, HTTPException, Query

app = FastAPI()


@app.get("/items/{item_id}")
async def read_item(item_id: int, q: str = Query(None), db=Depends(get_db)):
    if item_id not in db:
        raise HTTPException(404, "Not found")
    return db[item_id]
```

```python
# SlowAPI
from slowapi import Depends, NotFound, Query, SlowAPI

app = SlowAPI()


@app.get("/items/{item_id:int}")
async def read_item(item_id: int, q: str = Query(None), db=Depends(get_db)):
    if item_id not in db:
        raise NotFound("Not found")
    return db[item_id]
```

| FastAPI | SlowAPI |
| --- | --- |
| `FastAPI()` | `SlowAPI()` |
| `Query/Path/Header/Cookie/Body/Depends` | same names, same semantics |
| `HTTPException(404, ...)` | `NotFound(...)`, or `HTTPException(404, ...)` |
| `response_model=` | the return annotation, or `response_model=` |
| `APIRouter` | `Router` |
| `BackgroundTasks` | `res.background` |
| `Request` / `Response` | same |
| `@app.on_event("startup")` | same |
| Pydantic models | dataclasses, or Pydantic if installed |

Differences to know about:

- **A bare scalar on POST reads the body first**, then the query string.
  FastAPI treats it as a query parameter. Use `Query(...)` to force the old
  behaviour.
- **No `Annotated[...]` support yet.** Use default-value markers.
- **Error body shape differs**: `{"error": {"code", "status", "message"}}`
  rather than `{"detail": ...}`. Register an exception handler to keep the old
  shape if clients depend on it.
- **You can now deploy on WSGI**, which is usually the reason for the move.

## From Express (Node)

```javascript
// Express
app.get('/users/:id', (req, res) => {
  res.status(200).json({ id: req.params.id })
})

app.use((req, res, next) => {
  console.log(req.method, req.path)
  next()
})
```

```python
# SlowAPI
@app.get("/users/:id")
def get_user(req, res, id):
    res.status(200).json({"id": id})


def logger(req, res, next):
    print(req.method, req.path)
    next()


app.use(logger)
```

The `:id` syntax, the `(req, res)` handler shape, `res.status().json()`
chaining, and `(req, res, next)` middleware — including short-circuiting — all
work as you expect.

| Express | SlowAPI |
| --- | --- |
| `req.params` | `req.params` |
| `req.query` | `req.query` |
| `req.body` | `await req.json()` / `req.json_sync()` |
| `req.get('header')` | `req.get("header")` |
| `res.send/json/status/set/redirect/cookie` | same names |
| `res.render` | `res.render` |
| `express.static` | `app.mount_static` |
| `next(err)` | `raise` |
| Routers | `Router` or `@controller` |

Add type annotations to handlers as you go and validation, coercion, and
OpenAPI appear for free.

## From NestJS

```typescript
// NestJS
@Controller('users')
export class UserController {
  constructor(private readonly users: UserService) {}

  @Get(':id')
  @UseGuards(RoleGuard)
  findOne(@Param('id') id: number) {
    return this.users.find(id)
  }
}
```

```python
# SlowAPI
@controller("/users")
class UserController:
    def __init__(self, users: UserService) -> None:
        self.users = users

    @Get("/:id")
    @use_guards(RoleGuard)
    def find_one(self, id: int) -> User:
        return self.users.find(id)
```

| NestJS | SlowAPI |
| --- | --- |
| `@Controller('x')` | `@controller("/x")` |
| `@Get()/@Post()/...` | `@Get()/@Post()/...` |
| `@Module({...})` | `@module(...)` |
| `@Injectable()` | `@injectable()` |
| `@Inject(TOKEN)` | `Inject(TOKEN)` as a default |
| `@UseGuards`/`@UseInterceptors`/`@UsePipes` | `use_guards`/`use_interceptors`/`use_pipes` |
| `@SetMetadata` / `Reflector` | `set_metadata` / `ctx.get(key)` |
| `@HttpCode(201)` | `@http_code(201)` |
| `ExecutionContext` | `ExecutionContext` |
| `ClassSerializerInterceptor` + `@Exclude` | `expose()` / `hidden()` |
| `@Param`/`@Query`/`@Body` | inferred, or `Query()`/`Body()` |
| Providers, `useValue`, `useFactory`, `useClass` | `Provider.value/.factory/.klass` |

Differences:

- **No decorator metadata reflection needed.** Python annotations are the
  metadata, so `@Param('id') id: number` is just `id: int`.
- **Modules do not nest containers at runtime.** `exports` is enforced at
  registration; resolution uses one root container plus a per-request child.
- **No `forwardRef`.** Circular dependencies are reported with the cycle path;
  break them by injecting a factory.

## Incremental migration

You do not have to move everything at once. SlowAPI is a WSGI application, so
you can mount it beside an existing one and route by path at the proxy:

```nginx
location /v2/ { proxy_pass http://slowapi-service; }
location /    { proxy_pass http://legacy-service;  }
```

Move one endpoint, verify it under both protocols, repeat.
