"""The application object.

:class:`SlowAPI` is simultaneously a WSGI application and an ASGI application.
Which one it behaves as is decided by how the server calls it -- two positional
arguments means WSGI, three means ASGI -- so the same object can be handed to
gunicorn's sync workers today and uvicorn tomorrow with no code change::

    app = SlowAPI()

    # gunicorn 'myapp:app'          -> WSGI
    # uvicorn  'myapp:app'          -> ASGI

Everything above the adapters is protocol-neutral, and everything below is two
small files.  See ``docs/internals/dual-protocol.md`` for the full picture.
"""

from __future__ import annotations

import asyncio
import inspect
import typing as t
from dataclasses import dataclass

from ._version import __version__
from .concurrency import is_async_callable, maybe_await
from .decorators import (
    CONTROLLER_ATTR,
    MODULE_ATTR,
    ModuleDefinition,
    controller_routes,
)
from .di import Container, Provider
from .exceptions import ConfigurationError
from .execution import ExecutionContext
from .guards import collect_guards, run_guards
from .injection import HandlerSignature, Resolver, analyse
from .interceptors import collect_interceptors, run_interceptors
from .logging import get_logger
from .middleware.base import Executor, build_chain
from .middleware.errors import ErrorMiddleware, ExceptionHandlers
from .middleware.logging import RequestIDMiddleware
from .pipes import collect_pipes
from .request import DEFAULT_MAX_BODY_SIZE, Request
from .response import Response
from .routing import HTTP_METHODS, Route, Router
from .serialization import serialization_options, serialize

__all__ = ["SlowAPI"]

Handler = t.Callable[..., t.Any]
Decorator = t.Callable[[Handler], Handler]


@dataclass
class _Prepared:
    """Per-route dispatch plan, computed once and reused for every request."""

    signature: HandlerSignature
    guards: tuple[t.Any, ...]
    interceptors: tuple[t.Any, ...]
    pipes: tuple[t.Any, ...]
    controller: type | None
    #: True when nothing in this route's chain is async, enabling the
    #: loop-free WSGI fast path.
    fully_sync: bool
    serialize_options: dict[str, t.Any]
    status_code: int


class SlowAPI:
    """A dual-protocol web application."""

    def __init__(
        self,
        *,
        title: str = "SlowAPI Application",
        version: str = "0.1.0",
        description: str = "",
        debug: bool = False,
        middlewares: t.Sequence[t.Any] = (),
        modules: t.Sequence[type] = (),
        providers: t.Sequence[t.Any] = (),
        controllers: t.Sequence[type] = (),
        template_dir: str | None = None,
        template_engine: str = "builtin",
        docs_url: str | None = "/docs",
        redoc_url: str | None = "/redoc",
        openapi_url: str | None = "/openapi.json",
        root_path: str = "",
        max_body_size: int = DEFAULT_MAX_BODY_SIZE,
        auto_serialize: bool = True,
        request_id_header: str | None = "X-Request-ID",
        on_startup: t.Sequence[t.Callable[..., t.Any]] = (),
        on_shutdown: t.Sequence[t.Callable[..., t.Any]] = (),
        logger: t.Any = None,
    ) -> None:
        self.title = title
        self.version = version
        self.description = description
        self.debug = debug
        self.root_path = root_path.rstrip("/")
        self.max_body_size = max_body_size
        self.auto_serialize = auto_serialize
        self.docs_url = docs_url
        self.redoc_url = redoc_url
        self.openapi_url = openapi_url

        self.router = Router()
        self.container = Container(name="app")
        self.exception_handlers = ExceptionHandlers()
        self.state: dict[str, t.Any] = {}
        self.logger = logger or get_logger("slowapi.app")

        self._user_middlewares: list[t.Any] = list(middlewares)
        self._startup_hooks: list[t.Callable[..., t.Any]] = list(on_startup)
        self._shutdown_hooks: list[t.Callable[..., t.Any]] = list(on_shutdown)
        self._prepared: dict[int, _Prepared] = {}
        self._started = False
        self._templates: t.Any = None
        self._openapi_cache: dict[str, t.Any] | None = None

        self._error_middleware = ErrorMiddleware(
            debug=debug, handlers=self.exception_handlers, logger=self.logger
        )
        self._request_id_middleware = (
            RequestIDMiddleware(request_id_header) if request_id_header else None
        )

        if template_dir:
            self.configure_templates(template_dir, engine=template_engine)

        self.container.register_all(providers)
        for controller_class in controllers:
            self.register_controller(controller_class)
        for module_class in modules:
            self.register_module(module_class)

        if self.openapi_url:
            self._install_docs_routes()

    # --------------------------------------------------------------- config

    def configure_templates(
        self, directory: str, *, engine: str = "builtin", **options: t.Any
    ) -> SlowAPI:
        """Enable ``res.render(...)`` from ``directory``."""
        from .templating import TemplateEngine

        self._templates = TemplateEngine(directory, engine=engine, cache=not self.debug, **options)
        return self

    def use(self, *middlewares: t.Any) -> SlowAPI:
        """Add application-wide middleware, in the order they should run."""
        if self._started:
            raise ConfigurationError(
                "Middleware cannot be added after startup; register it before the first request."
            )
        self._user_middlewares.extend(middlewares)
        self._prepared.clear()
        return self

    def exception_handler(self, key: type | int) -> Decorator:
        """Register a handler for an exception type or an HTTP status code."""

        def decorator(fn: Handler) -> Handler:
            self.exception_handlers.add(key, fn)
            return fn

        return decorator

    def on_event(self, event: str) -> Decorator:
        """Register a ``startup`` or ``shutdown`` hook."""
        if event not in ("startup", "shutdown"):
            raise ConfigurationError("event must be 'startup' or 'shutdown'")

        def decorator(fn: Handler) -> Handler:
            (self._startup_hooks if event == "startup" else self._shutdown_hooks).append(fn)
            return fn

        return decorator

    # ------------------------------------------------------------ route API

    def add_route(
        self,
        path: str,
        handler: Handler,
        methods: t.Sequence[str],
        *,
        middlewares: t.Sequence[t.Any] = (),
        name: str | None = None,
        controller: type | None = None,
        **meta: t.Any,
    ) -> Handler:
        """Register ``handler`` for every method in ``methods``."""
        for method in methods:
            method = method.upper()
            if method not in HTTP_METHODS:
                raise ConfigurationError(f"Unsupported HTTP method {method!r}")
            route = Route(
                path=self.root_path + path,
                method=method,
                handler=handler,
                name=str(name or getattr(handler, "__name__", "handler")),
                middlewares=tuple(middlewares),
                **meta,
            )
            if controller is not None:
                handler.__slowapi_controller__ = controller  # type: ignore[attr-defined]
            self.router.add(route)
        self._openapi_cache = None
        return handler

    def _verb(self, method: str) -> t.Callable[..., Decorator]:
        def factory(
            path: str | None = None, *, middlewares: t.Sequence[t.Any] = (), **meta: t.Any
        ) -> Decorator:
            def decorator(handler: Handler) -> Handler:
                route_path = path if path is not None else f"/{handler.__name__}"
                self.add_route(route_path, handler, [method], middlewares=middlewares, **meta)
                return handler  # returning the handler keeps decorators stackable

            return decorator

        factory.__name__ = method.lower()
        factory.__doc__ = f"Register a handler for ``{method}`` on ``path``."
        return factory

    def __getattr__(self, name: str) -> t.Any:
        # get/post/put/patch/delete/head/options are generated on demand so the
        # verb list stays in one place.
        upper = name.upper()
        if upper in HTTP_METHODS:
            verb = self._verb(upper)
            setattr(self, name, verb)
            return verb
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def route(
        self,
        path: str | None = None,
        *,
        methods: t.Sequence[str] = ("GET",),
        middlewares: t.Sequence[t.Any] = (),
        **meta: t.Any,
    ) -> Decorator:
        """Register one handler -- or a whole class -- for several methods.

        Applied to a class, each method named after an HTTP verb becomes a
        route, which is how the original ``@app.route`` class syntax works.
        """

        def decorator(target: Handler | type) -> Handler | type:
            if inspect.isclass(target):
                prefix = path if path is not None else f"/{target.__name__.lower()}"
                self.register_controller(target, prefix=prefix, middlewares=middlewares)
                return target
            route_path = path if path is not None else f"/{target.__name__}"
            self.add_route(route_path, target, methods, middlewares=middlewares, **meta)
            return target

        return decorator

    def include_router(self, router: Router, *, prefix: str = "") -> SlowAPI:
        """Merge a :class:`~slowapi.routing.Router` into the application."""
        self.router.include(router, prefix=self.root_path + prefix)
        self._openapi_cache = None
        return self

    def mount_static(self, path: str, directory: str, **options: t.Any) -> SlowAPI:
        """Serve ``directory`` under ``path``."""
        from .static import StaticFiles

        files = StaticFiles(directory, **options)
        mount = path.rstrip("/") + "/{path:path}"
        self.add_route(
            mount, files, ["GET", "HEAD"], name=f"static:{path}", include_in_schema=False
        )
        return self

    # ------------------------------------------------------- controllers/DI

    def register_controller(
        self,
        controller_class: type,
        *,
        prefix: str | None = None,
        middlewares: t.Sequence[t.Any] = (),
        tags: t.Sequence[str] = (),
    ) -> SlowAPI:
        """Register every ``@Get``/``@Post``/... method on a controller class."""
        meta = getattr(controller_class, CONTROLLER_ATTR, None)
        base = prefix if prefix is not None else (meta.prefix if meta else "")
        base = "/" + base.strip("/") if base.strip("/") else ""
        version = getattr(controller_class, "__slowapi_version__", None) or (
            meta.version if meta else None
        )
        if version:
            base = f"/{version.strip('/')}{base}"
        class_tags = tuple(tags) + (meta.tags if meta else ())

        self.container.register(controller_class)
        found = controller_routes(controller_class)
        if not found:
            raise ConfigurationError(
                f"{controller_class.__name__} has no routes. Decorate its methods with "
                "@Get(...), @Post(...), and so on."
            )

        for attribute, route_meta, fn in found:
            suffix = "/" + route_meta.path.strip("/") if route_meta.path.strip("/") else ""
            full_path = (base + suffix) or "/"
            fn.__slowapi_controller__ = controller_class  # type: ignore[attr-defined]
            self.add_route(
                full_path,
                fn,
                [route_meta.method],
                middlewares=middlewares,
                name=route_meta.name or f"{controller_class.__name__}.{attribute}",
                controller=controller_class,
                summary=route_meta.summary,
                description=route_meta.description or inspect.getdoc(fn),
                tags=tuple(dict.fromkeys(class_tags + route_meta.tags)),
                deprecated=route_meta.deprecated,
                include_in_schema=route_meta.include_in_schema,
                response_model=route_meta.response_model,
                status_code=route_meta.status_code or getattr(fn, "__slowapi_status_code__", None),
                responses=route_meta.responses,
            )
        return self

    def register_module(self, module_class: type, *, _seen: set[type] | None = None) -> SlowAPI:
        """Register a ``@module`` class: its imports, providers and controllers."""
        definition: ModuleDefinition | None = getattr(module_class, MODULE_ATTR, None)
        if definition is None:
            raise ConfigurationError(
                f"{module_class.__name__} is not a module. Decorate it with @module(...)."
            )

        seen = _seen if _seen is not None else set()
        if module_class in seen:
            return self  # diamond imports are fine; register once
        seen.add(module_class)

        for imported in definition.imports:
            self.register_module(imported, _seen=seen)

        # A flat root container keeps resolution simple; `exports` is enforced
        # at registration time rather than by nesting scopes at runtime.
        self.container.register_all(definition.providers)
        for controller_class in definition.controllers:
            self.register_controller(
                controller_class,
                prefix=(
                    definition.prefix
                    + (
                        getattr(controller_class, CONTROLLER_ATTR).prefix
                        if hasattr(controller_class, CONTROLLER_ATTR)
                        else ""
                    )
                )
                or None,
                middlewares=definition.middlewares,
            )
        return self

    def provide(self, token: t.Any, value: t.Any = None, **kwargs: t.Any) -> SlowAPI:
        """Register a provider without a module.  Handy in tests and scripts."""
        if isinstance(token, Provider):
            self.container.register(token)
        elif value is not None or kwargs:
            self.container.register(Provider.value(token, value))
        else:
            self.container.register(token)
        return self

    # ----------------------------------------------------------- lifecycle

    def startup_is_sync(self) -> bool:
        """True when startup can run without an event loop.

        The WSGI adapter checks this so that an application with no async
        startup work never pays for the background loop thread.
        """
        participants: list[t.Any] = list(self._startup_hooks)
        for provider in self.container._providers.values():
            participants.append(provider.use_factory)
            target = provider.use_class or (
                provider.token if inspect.isclass(provider.token) else None
            )
            if target is not None:
                participants.append(getattr(target, "on_module_init", None))
        return not any(_maybe_async(p) for p in participants)

    async def startup(self) -> None:
        """Build singletons and run startup hooks.  Idempotent."""
        if self._started:
            return
        self._started = True
        await self.container.startup()
        for hook in self._startup_hooks:
            await maybe_await(hook())
        self.logger.info(
            "startup",
            extra={"routes": len(self.router.routes), "version": __version__},
        )

    async def shutdown(self) -> None:
        """Run shutdown hooks and dispose singletons."""
        if not self._started:
            return
        for hook in reversed(self._shutdown_hooks):
            await maybe_await(hook())
        await self.container.shutdown()
        self._started = False
        self.logger.info("shutdown")

    # ------------------------------------------------------------ dispatch

    def _global_middlewares(self) -> list[t.Any]:
        chain: list[t.Any] = [self._error_middleware]
        if self._request_id_middleware is not None:
            chain.append(self._request_id_middleware)
        chain.extend(self._user_middlewares)
        return chain

    def _prepare(self, route: Route) -> _Prepared:
        """Compute (and cache) the dispatch plan for ``route``."""
        cached = self._prepared.get(id(route))
        if cached is not None:
            return cached

        controller = getattr(route.handler, "__slowapi_controller__", None)
        signature = analyse(
            route.handler,
            path_params=[p.name for p in route.params],
            method=route.method,
            container_tokens=list(self.container._providers),
            is_method=controller is not None,
        )
        guards = collect_guards(route.handler, controller)
        interceptors = collect_interceptors(route.handler, controller)
        pipes = collect_pipes(route.handler, controller)

        participants: list[t.Any] = [
            route.handler,
            *self._user_middlewares,
            *route.middlewares,
            *guards,
            *interceptors,
            *pipes,
        ]
        participants.extend(spec.dependency for spec in signature.flatten_dependencies())
        fully_sync = not any(_maybe_async(p) for p in participants)

        plan = _Prepared(
            signature=signature,
            guards=guards,
            interceptors=interceptors,
            pipes=pipes,
            controller=controller,
            fully_sync=fully_sync,
            serialize_options=serialization_options(route.handler, controller),
            status_code=route.status_code or (201 if route.method == "POST" else 200),
        )
        self._prepared[id(route)] = plan
        return plan

    async def dispatch(
        self,
        request: Request,
        response: Response,
        executor: Executor,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> Response:
        """Run the full pipeline for one request and return the response."""
        response._templates = self._templates

        async def terminal() -> t.Any:
            route, path_params = self.router.match(request.method, request.path)
            request.path_params.update(path_params)
            request.scope["route"] = route
            plan = self._prepare(route)

            container = self.container.create_request_scope()
            request.scope["container"] = container
            ctx = ExecutionContext(request, response, route, container, self)

            try:
                await run_guards(plan.guards, ctx)
                inner = self._make_handler_call(ctx, plan, executor)
                result = await run_interceptors(plan.interceptors, ctx, inner)
                return self._finalise(result, response, plan)
            finally:
                await container.close()

        chain = build_chain(
            self._global_middlewares() + list(self._route_middlewares(request)),
            terminal,
            request,
            response,
            executor,
            loop,
        )
        result = await chain()
        return result if isinstance(result, Response) else response

    def _route_middlewares(self, request: Request) -> tuple[t.Any, ...]:
        """Route-scoped middleware, resolved before the route is matched.

        Matching happens inside the terminal so that a 404 still passes through
        global middleware; route middleware therefore attaches at dispatch time
        via the matched route stored on the scope.
        """
        return ()

    def _make_handler_call(
        self, ctx: ExecutionContext, plan: _Prepared, executor: Executor
    ) -> t.Callable[[], t.Awaitable[t.Any]]:
        async def call() -> t.Any:
            route: Route = ctx.request.scope["route"]

            async def invoke() -> t.Any:
                resolver = Resolver(ctx, plan.pipes)
                kwargs = await resolver.build(plan.signature)
                if plan.controller is not None:
                    instance = await ctx.container.resolve(plan.controller)
                    return await executor.call(route.handler, instance, **kwargs)
                return await executor.call(route.handler, **kwargs)

            if route.middlewares:
                chain = build_chain(route.middlewares, invoke, ctx.request, ctx.response, executor)
                return await chain()
            return await invoke()

        return call

    def _finalise(self, result: t.Any, response: Response, plan: _Prepared) -> Response:
        """Turn whatever the handler returned into the outgoing response."""
        if isinstance(result, Response):
            result._templates = self._templates
            return result
        if result is None:
            # Express style: the handler wrote to `res`.  A response that was
            # never touched means the handler forgot to answer.
            if response.status_code == 200 and not response.body and not response.is_streaming:
                if plan.signature.uses_response:
                    return response
                return response.status(204).end()
            return response

        payload = serialize(result, **plan.serialize_options) if self.auto_serialize else result
        if isinstance(payload, (bytes, str)) and not plan.serialize_options:
            response.send(payload)
        else:
            response.json(payload)
        if response.status_code == 200:
            response.status(plan.status_code)
        return response

    # -------------------------------------------------------------- servers

    def __call__(self, *args: t.Any) -> t.Any:
        """Serve as WSGI or ASGI, decided by the calling convention.

        WSGI servers call ``app(environ, start_response)`` and expect an
        iterable; ASGI servers call ``app(scope, receive, send)`` and expect an
        awaitable.  Dispatching on the argument count is unambiguous because no
        protocol uses the other's arity.
        """
        if len(args) == 3:
            from .adapters.asgi import ASGIAdapter

            return ASGIAdapter(self)(*args)
        if len(args) == 2:
            from .adapters.wsgi import WSGIAdapter

            return WSGIAdapter(self)(*args)
        raise TypeError(
            f"SlowAPI application called with {len(args)} arguments; expected 2 (WSGI) or 3 (ASGI)."
        )

    @property
    def wsgi_app(self) -> t.Any:
        """Explicit WSGI entry point, for servers that introspect signatures."""
        from .adapters.wsgi import WSGIAdapter

        return WSGIAdapter(self)

    @property
    def asgi_app(self) -> t.Any:
        """Explicit ASGI entry point."""
        from .adapters.asgi import ASGIAdapter

        return ASGIAdapter(self)

    def run(self, host: str = "127.0.0.1", port: int = 8000, **options: t.Any) -> None:
        """Start a development server.  Never use this in production."""
        from .server import run as _run

        _run(self, host=host, port=port, **options)

    # ---------------------------------------------------------------- docs

    def openapi(self) -> dict[str, t.Any]:
        """Return (and cache) the generated OpenAPI 3.1 document."""
        if self._openapi_cache is None:
            from .openapi import generate

            self._openapi_cache = generate(self)
        return self._openapi_cache

    def _install_docs_routes(self) -> None:
        from .openapi import redoc_html, swagger_html

        def openapi_json(res: Response) -> Response:
            return res.json(self.openapi())

        self.add_route(
            self.openapi_url or "/openapi.json",
            openapi_json,
            ["GET"],
            name="openapi",
            include_in_schema=False,
        )

        if self.docs_url:

            def docs(res: Response) -> Response:
                return res.html(swagger_html(self.openapi_url or "/openapi.json", self.title))

            self.add_route(self.docs_url, docs, ["GET"], name="docs", include_in_schema=False)

        if self.redoc_url:

            def redoc(res: Response) -> Response:
                return res.html(redoc_html(self.openapi_url or "/openapi.json", self.title))

            self.add_route(self.redoc_url, redoc, ["GET"], name="redoc", include_in_schema=False)

    def url_for(self, name: str, **params: t.Any) -> str:
        """Reverse-resolve a route name to a path."""
        return self.router.url_path_for(name, **params)

    @property
    def routes(self) -> list[Route]:
        return self.router.routes

    def __repr__(self) -> str:
        return f"<SlowAPI {self.title!r} routes={len(self.router.routes)}>"


def _maybe_async(participant: t.Any) -> bool:
    """Would this participant force the async path?"""
    if participant is None:
        return False
    if inspect.isclass(participant):
        for attribute in ("dispatch", "intercept", "can_activate", "transform", "__call__"):
            member = getattr(participant, attribute, None)
            if member is not None and is_async_callable(member):
                return True
        return False
    if is_async_callable(participant):
        return True
    if inspect.isgeneratorfunction(participant) or inspect.isasyncgenfunction(participant):
        return inspect.isasyncgenfunction(participant)
    for attribute in ("dispatch", "intercept", "can_activate", "transform"):
        member = getattr(participant, attribute, None)
        if member is not None and is_async_callable(member):
            return True
    return False
