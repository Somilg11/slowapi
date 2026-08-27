"""Class-based routing: controllers and modules.

The functional API (``@app.get("/x")``) is fine for small services.  Past a few
dozen endpoints you want the code organised by feature, with each feature
declaring its own providers and boundaries.  That is what controllers and
modules give you, in roughly the shape NestJS made popular::

    @controller("/users", tags=["users"])
    class UserController:
        def __init__(self, service: UserService):
            self.service = service

        @Get("/:id")
        async def find_one(self, id: int) -> User:
            return await self.service.find(id)

    @module(controllers=[UserController], providers=[UserService], exports=[UserService])
    class UserModule:
        pass

    app = SlowAPI(modules=[UserModule])

Controllers are instantiated by the DI container, so constructor injection just
works, and a controller's scope follows the usual rules.
"""

from __future__ import annotations

import typing as t

from .exceptions import ConfigurationError
from .execution import set_metadata_on

__all__ = [
    "Delete",
    "Get",
    "Head",
    "ModuleDefinition",
    "Options",
    "Patch",
    "Post",
    "Put",
    "controller",
    "controller_routes",
    "http_code",
    "module",
    "never_suspends",
    "route",
    "set_metadata",
    "version",
]

CONTROLLER_ATTR = "__slowapi_controller_meta__"
ROUTE_ATTR = "__slowapi_route_meta__"
MODULE_ATTR = "__slowapi_module__"


class _RouteMeta(t.NamedTuple):
    """What a method-level decorator records for the app to pick up later."""

    method: str
    path: str
    name: str | None
    status_code: int | None
    summary: str | None
    description: str | None
    tags: tuple[str, ...]
    deprecated: bool
    include_in_schema: bool
    response_model: t.Any
    responses: dict[int, dict[str, t.Any]]


class _ControllerMeta(t.NamedTuple):
    prefix: str
    tags: tuple[str, ...]
    version: str | None


def controller(
    prefix: str = "",
    *,
    tags: t.Sequence[str] = (),
    version: str | None = None,
) -> t.Callable[[type], type]:
    """Mark a class as a controller and set the prefix for its routes.

    :param version:
        Optional API version segment.  When set, routes are mounted under
        ``/{version}{prefix}`` -- ``version="v2"`` yields ``/v2/users/...``.
    """

    def decorator(cls: type) -> type:
        normalised = "/" + prefix.strip("/") if prefix.strip("/") else ""
        setattr(cls, CONTROLLER_ATTR, _ControllerMeta(normalised, tuple(tags), version))
        return cls

    return decorator


def _method_decorator(http_method: str) -> t.Callable[..., t.Any]:
    def factory(
        path: str = "",
        *,
        name: str | None = None,
        status_code: int | None = None,
        summary: str | None = None,
        description: str | None = None,
        tags: t.Sequence[str] = (),
        deprecated: bool = False,
        include_in_schema: bool = True,
        response_model: t.Any = None,
        responses: dict[int, dict[str, t.Any]] | None = None,
    ) -> t.Callable[[t.Callable[..., t.Any]], t.Callable[..., t.Any]]:
        def decorator(fn: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
            existing = list(getattr(fn, ROUTE_ATTR, []))
            existing.append(
                _RouteMeta(
                    method=http_method,
                    path=path,
                    name=name,
                    status_code=status_code,
                    summary=summary,
                    description=description,
                    tags=tuple(tags),
                    deprecated=deprecated,
                    include_in_schema=include_in_schema,
                    response_model=response_model,
                    responses=dict(responses or {}),
                )
            )
            setattr(fn, ROUTE_ATTR, existing)
            return fn

        return decorator

    factory.__name__ = http_method.capitalize()
    factory.__doc__ = f"Bind a controller method to ``{http_method}`` on ``path``."
    return factory


Get = _method_decorator("GET")
Post = _method_decorator("POST")
Put = _method_decorator("PUT")
Patch = _method_decorator("PATCH")
Delete = _method_decorator("DELETE")
Head = _method_decorator("HEAD")
Options = _method_decorator("OPTIONS")


def route(methods: t.Sequence[str], path: str = "", **kwargs: t.Any) -> t.Callable[..., t.Any]:
    """Bind a controller method to several HTTP methods at once."""

    def decorator(fn: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
        for method in methods:
            fn = _method_decorator(method.upper())(path, **kwargs)(fn)
        return fn

    return decorator


def http_code(status_code: int) -> t.Callable[[t.Any], t.Any]:
    """Set the success status code for a handler (NestJS ``@HttpCode``)."""

    def decorator(fn: t.Any) -> t.Any:
        fn.__slowapi_status_code__ = int(status_code)
        return fn

    return decorator


def set_metadata(key: str, value: t.Any) -> t.Callable[[t.Any], t.Any]:
    """Attach arbitrary metadata readable from guards via ``ctx.get(key)``."""

    def decorator(target: t.Any) -> t.Any:
        return set_metadata_on(target, key, value)

    return decorator


def version(tag: str) -> t.Callable[[t.Any], t.Any]:
    """Pin a controller or handler to an API version segment."""

    def decorator(target: t.Any) -> t.Any:
        target.__slowapi_version__ = tag
        return target

    return decorator


class ModuleDefinition(t.NamedTuple):
    """The declarative description attached to a ``@module`` class."""

    imports: tuple[type, ...]
    controllers: tuple[type, ...]
    providers: tuple[t.Any, ...]
    exports: tuple[t.Any, ...]
    #: Middleware applied to every route this module contributes.
    middlewares: tuple[t.Any, ...]
    prefix: str


def module(
    *,
    imports: t.Sequence[type] = (),
    controllers: t.Sequence[type] = (),
    providers: t.Sequence[t.Any] = (),
    exports: t.Sequence[t.Any] = (),
    middlewares: t.Sequence[t.Any] = (),
    prefix: str = "",
) -> t.Callable[[type], type]:
    """Declare a feature module.

    :param imports: Other modules whose ``exports`` become visible here.
    :param controllers: Controller classes whose routes this module registers.
    :param providers: Classes or :class:`~slowapi.di.Provider` instances.
    :param exports: The subset of ``providers`` importing modules may resolve.
    :param middlewares: Middleware applied to this module's routes only.
    :param prefix: Path prefix prepended to every controller in this module.
    """

    def decorator(cls: type) -> type:
        unknown = [e for e in exports if e not in providers and not _provides(providers, e)]
        if unknown:
            names = [getattr(u, "__name__", u) for u in unknown]
            raise ConfigurationError(
                f"{cls.__name__} exports {names} which it does not provide. "
                "Add them to providers=, or re-export by importing their module."
            )
        setattr(
            cls,
            MODULE_ATTR,
            ModuleDefinition(
                imports=tuple(imports),
                controllers=tuple(controllers),
                providers=tuple(providers),
                exports=tuple(exports),
                middlewares=tuple(middlewares),
                prefix=prefix,
            ),
        )
        return cls

    return decorator


def _provides(providers: t.Sequence[t.Any], token: t.Any) -> bool:
    from .di import Provider

    return any(isinstance(p, Provider) and p.token is token for p in providers)


def controller_routes(cls: type) -> list[tuple[str, _RouteMeta, t.Callable[..., t.Any]]]:
    """Yield ``(attribute_name, route_meta, function)`` for a controller class.

    Walks the MRO so that a controller can inherit routes from a base class,
    which is how shared CRUD scaffolding is usually expressed.
    """
    found: list[tuple[str, _RouteMeta, t.Callable[..., t.Any]]] = []
    seen: set[str] = set()
    for klass in cls.__mro__:
        for name, attribute in vars(klass).items():
            if name in seen:
                continue
            fn = (
                attribute.__func__
                if isinstance(attribute, (staticmethod, classmethod))
                else attribute
            )
            metas = getattr(fn, ROUTE_ATTR, None)
            if not metas:
                continue
            seen.add(name)
            for meta in metas:
                found.append((name, meta, fn))
    return found


def never_suspends(target: t.Any) -> t.Any:
    """Promise that this participant never suspends, keeping the fast path.

    SlowAPI decides at registration time whether a route can be driven without
    an event loop, and it treats every ``async def`` participant as a reason to
    give that up.  That is the safe default, but it is pessimistic: middleware
    that awaits nothing except ``call_next()`` cannot suspend on its own, so it
    is transparent to the loop-free path.  Mark it and the fast path survives::

        @never_suspends
        class Timing:
            async def dispatch(self, req, res, call_next):
                start = time.perf_counter()
                result = await call_next()          # the only await -- fine
                res.set("x-elapsed", f"{time.perf_counter() - start:.4f}")
                return result

    The obligation is real and it is yours: inside a marked participant, do not
    await anything but ``call_next()``.  No ``asyncio.sleep``, no async client,
    no ``asyncio.wait_for``, no lock.  Break the promise and a synchronous
    request raises ``RuntimeError`` from ``drive()`` naming this as the cause,
    rather than hanging -- but it is still your bug, not a framework one.
    """
    target.__slowapi_never_suspends__ = True
    return target
