# API reference

Every public name, grouped by what it is for. Import everything from `slowfw`
unless noted.

## Application

```python
SlowAPI(
    *,
    title="SlowAPI Application", version="0.1.0", description="",
    debug=False,
    middlewares=(), modules=(), providers=(), controllers=(),
    template_dir=None, template_engine="builtin",
    docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json",
    root_path="", max_body_size=16 * 1024 * 1024,
    auto_serialize=True, request_id_header="X-Request-ID",
    on_startup=(), on_shutdown=(), logger=None,
)
```

| Method | Purpose |
| --- | --- |
| `.get/.post/.put/.patch/.delete/.head/.options(path, **meta)` | Register a route |
| `.route(path, methods=..., **meta)` | Several methods, or a class |
| `.add_route(path, handler, methods, **meta)` | Programmatic registration |
| `.use(*middlewares)` | Add application middleware |
| `.include_router(router, prefix="")` | Merge a router |
| `.register_controller(cls, prefix=None, ...)` | Register a controller |
| `.register_module(cls)` | Register a module and its imports |
| `.provide(token, value=None)` | Register a provider |
| `.mount_static(path, directory, **options)` | Serve a directory |
| `.configure_templates(directory, engine=..., ...)` | Enable `res.render` |
| `.exception_handler(type_or_status)` | Decorator: custom error response |
| `.on_event("startup" / "shutdown")` | Decorator: lifecycle hook |
| `.url_for(name, **params)` | Reverse a route |
| `.openapi()` | The generated document |
| `.run(host, port, **options)` | Development server |
| `await .startup()` / `await .shutdown()` | Lifecycle, run by adapters |

| Attribute | |
| --- | --- |
| `.routes` | Every registered `Route` |
| `.router` | The `Router` |
| `.container` | The DI `Container` |
| `.state` | Application-wide dict |
| `.logger` | The application logger |
| `.wsgi_app` / `.asgi_app` | Explicit protocol entry points |

Route metadata accepted by every registration method: `name`, `middlewares`,
`summary`, `description`, `tags`, `deprecated`, `include_in_schema`,
`response_model`, `status_code`, `responses`.

## Routing

```python
Router(prefix="", *, middlewares=(), tags=())
```

`.get/.post/...(path, **meta)`, `.route(path, methods=...)`, `.add(route)`,
`.include(other, prefix="")`, `.match(method, path)`, `.url_path_for(name, **p)`,
`.routes`.

From `slowfw.routing`: `Route`, `Convertor`, `CONVERTORS`, `compile_path`,
`HTTP_METHODS`.

## Request

| | |
| --- | --- |
| Metadata | `.method` `.path` `.url` `.scheme` `.http_version` `.root_path` |
| Inputs | `.headers` `.query_params` (`.query`) `.path_params` (`.params`) `.cookies` |
| Client | `.client` `.ip` |
| Body (async) | `await .body()` `.text()` `.json()` `.form()` `.stream()` |
| Body (sync) | `.body_sync()` `.text_sync()` `.json_sync()` `.form_sync()` `.stream_sync()` |
| Content | `.content_type` `.charset` |
| Helpers | `.get(header, default)` `.is_(type)` `.accepts(type)` |
| Scratch | `.state` `.scope` `.app` `.request_id` |

## Response

| | |
| --- | --- |
| Content | `.send()` `.json()` `.text()` `.html()` `.end()` `.render()` |
| Status | `.status(code)` `.status_code` `.status_line` |
| Headers | `.set()` (`.header()`) `.append()` `.type()` `.vary()` `.headers` |
| Caching | `.cache(seconds, ...)` `.no_cache()` `.etag(value=None, weak=False)` |
| Cookies | `.cookie(...)` `.clear_cookie(...)` |
| Navigation | `.redirect(location, status=302)` |
| Files | `.attachment(filename=None)` |
| Streaming | `.stream(iterable, media_type=None)` `.is_streaming` |
| Other | `.background` |

Classes: `Response`, `JSONResponse`, `HTMLResponse`, `PlainTextResponse`,
`RedirectResponse`, `StreamingResponse`, `FileResponse`, `NoContentResponse`.

## Parameters

`Query`, `Path`, `Header`, `Cookie`, `Body`, `Form`, `File`, `Depends`,
`Inject`, `Req`, `Res`, `Ctx`.

Common arguments: `default`, `default_factory`, `alias`, `title`,
`description`, `ge`, `gt`, `le`, `lt`, `min_length`, `max_length`, `pattern`,
`examples`, `deprecated`, `include_in_schema`, `explode`.

`Header(..., convert_underscores=True)`. `Body(..., embed=False,
media_type=...)`. `Depends(fn=None, use_cache=True)`. `Inject(token=None)`.

## Dependency injection

`Container`, `Provider`, `InjectionToken`, `injectable(scope=...)`.

`Provider(token, use_class=..., ...)`, `Provider.value(token, value)`,
`Provider.factory(token, fn, inject=(), scope=...)`,
`Provider.klass(token, impl, scope=...)`.

Scopes: `"singleton"`, `"request"`, `"transient"`. Hooks: `on_module_init`,
`on_module_destroy`.

## Controllers and modules

`controller(prefix, *, tags=(), version=None)`,
`module(*, imports=(), controllers=(), providers=(), exports=(), middlewares=(), prefix="")`,
`Get`, `Post`, `Put`, `Patch`, `Delete`, `Head`, `Options`, `route(methods, path)`,
`http_code(code)`, `set_metadata(key, value)`, `version(tag)`.

## Guards, interceptors, pipes

`use_guards(*guards)`, `roles(*names)`, `public`, `ExecutionContext`.
From `slowfw.guards`: `AllowAll`, `DenyAll`, `RequireHeader`.

`use_interceptors(*interceptors)`, `Interceptor`.
From `slowfw.interceptors`: `TimingInterceptor`, `EnvelopeInterceptor`,
`CacheInterceptor`.

`use_pipes(*pipes)`, `Pipe`.
From `slowfw.pipes`: `TrimPipe`, `LowercasePipe`, `ParseIntPipe`,
`DefaultValuePipe`, `ClampPipe`, `NotEmptyPipe`.

`ExecutionContext`: `.request` `.response` `.route` `.container` `.app`
`.handler` `.controller` `.metadata` `.get(key, default)` `.state`
`await .resolve(token)`.

## Serialization

`expose(groups=(), alias=None, transform=None)`, `hidden(write_only=True)`,
`serialize(value, groups=(), include=None, exclude=None, exclude_none=False)`,
`serialize_with(...)`. From `slowfw.serialization`: `SerializerInterceptor`,
`FieldOptions`.

## Middleware

From `slowfw.middleware`: `CORSMiddleware`, `SecurityHeadersMiddleware`,
`TrustedHostMiddleware`, `GZipMiddleware`, `ProxyHeadersMiddleware`,
`RateLimitMiddleware`, `MemoryRateLimitStore`, `RateLimitStore`,
`SessionMiddleware`, `Session`, `RequestIDMiddleware`, `AccessLogMiddleware`,
`ErrorMiddleware`, `ExceptionHandlers`, `Middleware`, `Executor`,
`SyncExecutor`, `AsyncExecutor`, `build_chain`, `adapt`.

## Exceptions

`SlowAPIError` → `ConfigurationError`, `HTTPException`, `WebSocketDisconnect`.

`HTTPException` → `BadRequest` (400), `Unauthorized` (401), `Forbidden` (403),
`NotFound` (404), `MethodNotAllowed` (405), `PayloadTooLarge` (413),
`UnsupportedMediaType` (415), `ValidationError` (422), `TooManyRequests` (429).

## Configuration and logging

`Settings`, `Settings.load()`, `from_env(model, prefix="")`,
`load_dotenv(path=".env", override=False)`.

From `slowfw.logging`: `configure_logging(level, json_output=False,
service=None, version=None)`, `get_logger(name)`, `JSONFormatter`,
`ConsoleFormatter`.

## Testing

From `slowfw.testing`: `TestClient(app, protocol="wsgi"|"asgi", base_url=...,
headers=..., follow_cookies=True)`, `TestResponse`.

## Data structures

`Headers`, `QueryParams`, `UploadFile`, `URL`, `State`.
From `slowfw.datastructures`: `MultiDict`, `MutableHeaders`, `FormData`,
`Address`.

## Concurrency

From `slowfw.concurrency`: `drive(coro)`, `is_async_callable(obj)`,
`run_in_threadpool(fn, *a)`, `run_coroutine_sync(coro, loop=None)`,
`maybe_await(value)`, `call_maybe_async(fn, *a)`, `shutdown_loop_thread()`.

## Other

`StaticFiles(directory, ...)`, `slowfw.templating.TemplateEngine`,
`slowfw.templating.Markup`, `slowfw.signing.Signer`,
`slowfw.validation.coerce/json_schema_for/FieldError`,
`slowfw.openapi.generate/swagger_html/redoc_html`,
`slowfw.server.run/detect_server`.
