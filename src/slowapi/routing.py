"""Path matching and route registration.

Paths are compiled once at registration time into a prefix tree of segments, so
matching costs O(number of path segments) rather than O(number of routes).  Two
syntaxes are accepted interchangeably, because SlowAPI is deliberately a bridge
between the Express and FastAPI worlds::

    /users/{id}          # FastAPI / Starlette style
    /users/:id           # Express style
    /users/{id:int}      # typed, coerced and validated before the handler runs
    /assets/{path:path}  # greedy catch-all, matches slashes

Supported converters: ``str`` (default), ``int``, ``float``, ``uuid``,
``slug``, ``path``, and ``re:<pattern>`` for anything else.
"""

from __future__ import annotations

import re
import typing as t
import uuid as _uuid
from dataclasses import dataclass, field

from .exceptions import ConfigurationError, MethodNotAllowed, NotFound

__all__ = ["CONVERTORS", "Convertor", "Route", "Router", "compile_path"]

HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"})


class Convertor:
    """Turns a raw URL segment into a typed Python value."""

    regex = "[^/]+"
    openapi_type = "string"
    openapi_format: str | None = None

    def convert(self, value: str) -> t.Any:  # pragma: no cover - trivial
        return value

    def to_string(self, value: t.Any) -> str:
        return str(value)


class StringConvertor(Convertor):
    pass


class IntConvertor(Convertor):
    regex = "[0-9]+"
    openapi_type = "integer"

    def convert(self, value: str) -> int:
        return int(value)


class FloatConvertor(Convertor):
    regex = r"[0-9]+(?:\.[0-9]+)?"
    openapi_type = "number"

    def convert(self, value: str) -> float:
        return float(value)


class UUIDConvertor(Convertor):
    regex = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    openapi_format = "uuid"

    def convert(self, value: str) -> _uuid.UUID:
        return _uuid.UUID(value)


class SlugConvertor(Convertor):
    regex = "[a-z0-9]+(?:-[a-z0-9]+)*"


class PathConvertor(Convertor):
    """Greedy converter that matches across ``/`` boundaries."""

    regex = ".*"


CONVERTORS: dict[str, Convertor] = {
    "str": StringConvertor(),
    "int": IntConvertor(),
    "float": FloatConvertor(),
    "uuid": UUIDConvertor(),
    "slug": SlugConvertor(),
    "path": PathConvertor(),
}

# {name}, {name:conv}, {name:re:<pattern>} or Express-flavoured :name
_PARAM_RE = re.compile(
    r"\{(?P<brace>[a-zA-Z_][a-zA-Z0-9_]*)(?::(?P<conv>re:.+|[a-zA-Z_]+))?\}"
    r"|:(?P<colon>[a-zA-Z_][a-zA-Z0-9_]*)"
)


@dataclass(frozen=True)
class _Param:
    name: str
    convertor: Convertor
    declared: str


@dataclass
class _Segment:
    """One ``/``-delimited piece of a path template."""

    raw: str
    literal: str | None = None
    params: tuple[_Param, ...] = ()
    pattern: re.Pattern[str] | None = None
    #: True when the whole segment is a single ``path`` converter.
    greedy: bool = False

    @property
    def is_literal(self) -> bool:
        return self.literal is not None


def _make_convertor(declared: str | None) -> Convertor:
    if declared is None:
        return CONVERTORS["str"]
    if declared.startswith("re:"):
        custom = Convertor()
        custom.regex = declared[3:]
        return custom
    try:
        return CONVERTORS[declared]
    except KeyError:
        raise ConfigurationError(
            f"Unknown path converter {declared!r}. "
            f"Available: {', '.join(sorted(CONVERTORS))} or 're:<regex>'."
        ) from None


def compile_path(path: str) -> tuple[list[_Segment], list[_Param], str]:
    """Compile a path template into segments, its params, and a canonical form.

    The canonical form normalises Express ``:id`` into ``{id}`` so that both
    syntaxes collapse onto a single route identity.
    """
    # An empty path means "the prefix itself", which is how a router or
    # controller registers a handler for its own root.
    if path == "":
        path = "/"
    if not path.startswith("/"):
        raise ConfigurationError(f"Route path must start with '/': {path!r}")

    segments: list[_Segment] = []
    all_params: list[_Param] = []
    canonical_parts: list[str] = []

    for raw in path.strip("/").split("/") if path.strip("/") else []:
        matches = list(_PARAM_RE.finditer(raw))
        if not matches:
            segments.append(_Segment(raw, literal=raw))
            canonical_parts.append(raw)
            continue

        params: list[_Param] = []
        pattern_parts: list[str] = []
        canonical = ""
        cursor = 0
        for match in matches:
            pattern_parts.append(re.escape(raw[cursor : match.start()]))
            canonical += raw[cursor : match.start()]
            name = match.group("brace") or match.group("colon")
            convertor = _make_convertor(match.group("conv"))
            param = _Param(name, convertor, match.group("conv") or "str")
            params.append(param)
            pattern_parts.append(f"(?P<{name}>{convertor.regex})")
            canonical += f"{{{name}}}"
            cursor = match.end()
        pattern_parts.append(re.escape(raw[cursor:]))
        canonical += raw[cursor:]

        whole = len(matches) == 1 and matches[0].span() == (0, len(raw))
        greedy = whole and isinstance(params[0].convertor, PathConvertor)
        segments.append(
            _Segment(
                raw,
                params=tuple(params),
                pattern=re.compile("^" + "".join(pattern_parts) + "$"),
                greedy=greedy,
            )
        )
        all_params.extend(params)
        canonical_parts.append(canonical)

    duplicates = {p.name for p in all_params if [q.name for q in all_params].count(p.name) > 1}
    if duplicates:
        raise ConfigurationError(f"Duplicate path parameter(s) {sorted(duplicates)} in {path!r}")
    return segments, all_params, "/" + "/".join(canonical_parts)


@dataclass
class Route:
    """A single method+path binding and everything attached to it."""

    path: str
    method: str
    handler: t.Callable[..., t.Any]
    name: str
    middlewares: tuple[t.Any, ...] = ()
    #: Populated by :mod:`slowapi.injection` at registration time.
    signature: t.Any = None
    #: OpenAPI metadata.
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    include_in_schema: bool = True
    response_model: t.Any = None
    status_code: int | None = None
    responses: dict[int, dict[str, t.Any]] = field(default_factory=dict)
    params: tuple[_Param, ...] = ()

    def __repr__(self) -> str:
        return f"<Route {self.method} {self.path} -> {self.name}>"


class _Node:
    """One level of the routing trie."""

    __slots__ = ("dynamic", "greedy", "literal", "routes")

    def __init__(self) -> None:
        self.literal: dict[str, _Node] = {}
        #: ``(segment, node)`` pairs for parameterised segments.
        self.dynamic: list[tuple[_Segment, _Node]] = []
        #: ``(param_name, node)`` for a terminal ``{x:path}`` catch-all.
        self.greedy: tuple[_Param, _Node] | None = None
        self.routes: dict[str, Route] = {}


class Router:
    """Holds routes and resolves an incoming ``(method, path)`` to one.

    Routers nest: ``app.include(other_router, prefix="/v2")`` copies the child's
    routes in with the prefix and any router-level middleware applied.
    """

    def __init__(
        self,
        prefix: str = "",
        *,
        middlewares: t.Sequence[t.Any] = (),
        tags: t.Sequence[str] = (),
    ) -> None:
        self.prefix = prefix.rstrip("/")
        self.middlewares = tuple(middlewares)
        self.tags = tuple(tags)
        self._root = _Node()
        self._routes: list[Route] = []
        self._by_name: dict[str, Route] = {}

    # ------------------------------------------------------------ mutation

    @property
    def routes(self) -> list[Route]:
        return list(self._routes)

    def add(self, route: Route) -> Route:
        """Insert ``route`` into the trie."""
        segments, params, canonical = compile_path(route.path)
        route.path = canonical
        route.params = tuple(params)

        node = self._root
        for index, segment in enumerate(segments):
            if segment.greedy:
                if index != len(segments) - 1:
                    raise ConfigurationError(
                        f"A 'path' converter must be the last segment: {route.path!r}"
                    )
                param = segment.params[0]
                if node.greedy is None:
                    node.greedy = (param, _Node())
                node = node.greedy[1]
            elif segment.literal is not None:
                node = node.literal.setdefault(segment.literal, _Node())
            else:
                for existing, child in node.dynamic:
                    if existing.raw == segment.raw:
                        node = child
                        break
                else:
                    child = _Node()
                    node.dynamic.append((segment, child))
                    node = child

        if route.method in node.routes:
            previous = node.routes[route.method]
            raise ConfigurationError(
                f"Duplicate route: {route.method} {route.path} is already handled by "
                f"{previous.handler!r}"
            )
        node.routes[route.method] = route
        self._routes.append(route)
        if route.name in self._by_name and self._by_name[route.name].path != route.path:
            raise ConfigurationError(
                f"Route name {route.name!r} is already used by {self._by_name[route.name].path!r}"
            )
        self._by_name[route.name] = route
        return route

    def _verb(self, method: str) -> t.Callable[..., t.Any]:
        def factory(
            path: str | None = None,
            *,
            middlewares: t.Sequence[t.Any] = (),
            name: str | None = None,
            **meta: t.Any,
        ) -> t.Callable[[t.Callable[..., t.Any]], t.Callable[..., t.Any]]:
            def decorator(handler: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
                self.add(
                    Route(
                        path=path if path is not None else f"/{handler.__name__}",
                        method=method,
                        handler=handler,
                        name=name or handler.__name__,
                        middlewares=tuple(middlewares),
                        **meta,
                    )
                )
                return handler  # returning the handler keeps decorators stackable

            return decorator

        factory.__name__ = method.lower()
        factory.__doc__ = f"Register a handler for ``{method}`` on ``path``."
        return factory

    def __getattr__(self, name: str) -> t.Any:
        """Generate ``get``/``post``/... on demand, mirroring the application."""
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
        **kwargs: t.Any,
    ) -> t.Callable[[t.Callable[..., t.Any]], t.Callable[..., t.Any]]:
        """Register one handler for several methods."""

        def decorator(handler: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
            for method in methods:
                self._verb(method.upper())(path, **kwargs)(handler)
            return handler

        return decorator

    def include(self, other: Router, prefix: str = "") -> None:
        """Merge another router's routes into this one."""
        base = (self.prefix + prefix + other.prefix) or ""
        for route in other._routes:
            merged = Route(
                path=base + route.path,
                method=route.method,
                handler=route.handler,
                name=route.name,
                middlewares=self.middlewares + other.middlewares + route.middlewares,
                signature=route.signature,
                summary=route.summary,
                description=route.description,
                tags=tuple(dict.fromkeys(self.tags + other.tags + route.tags)),
                deprecated=route.deprecated,
                include_in_schema=route.include_in_schema,
                response_model=route.response_model,
                status_code=route.status_code,
                responses=dict(route.responses),
            )
            self.add(merged)

    # ------------------------------------------------------------ matching

    def match(self, method: str, path: str) -> tuple[Route, dict[str, t.Any]]:
        """Resolve a request, or raise 404/405.

        ``HEAD`` transparently falls back to the ``GET`` route, as required by
        RFC 9110; the adapter drops the body afterwards.
        """
        parts = path.strip("/").split("/") if path.strip("/") else []
        found: list[tuple[_Node, dict[str, t.Any]]] = []
        self._walk(self._root, parts, {}, found)
        if not found:
            raise NotFound(f"No route matches {path!r}")

        allowed: set[str] = set()
        for node, params in found:
            allowed.update(node.routes)
            if method in node.routes:
                return node.routes[method], params
            if method == "HEAD" and "GET" in node.routes:
                return node.routes["GET"], params
        raise MethodNotAllowed(
            sorted(allowed | {"OPTIONS"}),
            f"{method} is not allowed on {path!r}",
        )

    def _walk(
        self,
        node: _Node,
        parts: list[str],
        params: dict[str, t.Any],
        found: list[tuple[_Node, dict[str, t.Any]]],
    ) -> None:
        """Depth-first match with backtracking, literals taking priority."""
        if not parts:
            if node.routes:
                found.append((node, params))
            return

        head, rest = parts[0], parts[1:]

        child = node.literal.get(head)
        if child is not None:
            self._walk(child, rest, params, found)

        for segment, dyn_child in node.dynamic:
            assert segment.pattern is not None
            match = segment.pattern.match(head)
            if match is None:
                continue
            try:
                extracted = {
                    p.name: p.convertor.convert(match.group(p.name)) for p in segment.params
                }
            except (ValueError, TypeError):
                continue
            self._walk(dyn_child, rest, {**params, **extracted}, found)

        if node.greedy is not None:
            param, greedy_child = node.greedy
            if greedy_child.routes:
                found.append((greedy_child, {**params, param.name: "/".join(parts)}))

    def url_path_for(self, name: str, **params: t.Any) -> str:
        """Build the path for a named route.  Raises if a param is missing."""
        try:
            route = self._by_name[name]
        except KeyError:
            known = ", ".join(sorted(self._by_name)) or "none registered"
            raise ConfigurationError(f"No route named {name!r}. Known names: {known}") from None

        path = route.path
        for param in route.params:
            if param.name not in params:
                raise ConfigurationError(
                    f"url_path_for({name!r}) is missing path parameter {param.name!r}"
                )
            value = param.convertor.to_string(params[param.name])
            path = path.replace(f"{{{param.name}}}", value)
        return path
