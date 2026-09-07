"""Declarative markers that tell SlowAPI where a handler argument comes from.

These are the FastAPI half of the framework's personality.  Annotate a handler
parameter with one of these and the value is extracted, coerced to the declared
type, validated, and documented in the OpenAPI schema -- all from one line::

    @app.get("/items")
    def list_items(limit: int = Query(20, ge=1, le=100), token: str = Header(...)):
        ...

``...`` (Ellipsis) as the default means *required*.
"""

from __future__ import annotations

import typing as t

__all__ = [
    "REQUIRED",
    "Body",
    "Cookie",
    "Ctx",
    "Depends",
    "File",
    "Form",
    "Header",
    "Inject",
    "Param",
    "Path",
    "Query",
    "Req",
    "Res",
]

#: Sentinel meaning "this parameter has no default and must be supplied".
REQUIRED: t.Any = ...


class Param:
    """Base class for all request-derived parameter markers."""

    #: Where the value is read from; used by the OpenAPI generator.
    location: str = "query"

    def __init__(
        self,
        default: t.Any = REQUIRED,
        *,
        default_factory: t.Callable[[], t.Any] | None = None,
        alias: str | None = None,
        title: str | None = None,
        description: str | None = None,
        ge: float | None = None,
        gt: float | None = None,
        le: float | None = None,
        lt: float | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        pattern: str | None = None,
        examples: list[t.Any] | None = None,
        deprecated: bool = False,
        include_in_schema: bool = True,
        explode: bool = False,
    ) -> None:
        self.default = default
        self.default_factory = default_factory
        self.alias = alias
        self.title = title
        self.description = description
        self.ge, self.gt, self.le, self.lt = ge, gt, le, lt
        self.min_length, self.max_length = min_length, max_length
        self.pattern = pattern
        self.examples = examples
        self.deprecated = deprecated
        self.include_in_schema = include_in_schema
        #: Whether repeated query keys collect into a list (``?tag=a&tag=b``).
        self.explode = explode

    @property
    def required(self) -> bool:
        return self.default is REQUIRED and self.default_factory is None

    def get_default(self) -> t.Any:
        if self.default_factory is not None:
            return self.default_factory()
        return self.default

    def __repr__(self) -> str:
        return f"{type(self).__name__}(default={self.default!r}, alias={self.alias!r})"


class Query(Param):
    """Read from the URL query string."""

    location = "query"


class Path(Param):
    """Read from a path parameter.  Always required."""

    location = "path"

    def __init__(self, default: t.Any = REQUIRED, **kw: t.Any) -> None:
        super().__init__(default, **kw)


class Header(Param):
    """Read from a request header.  Underscores map to dashes by default."""

    location = "header"

    def __init__(
        self, default: t.Any = REQUIRED, *, convert_underscores: bool = True, **kw: t.Any
    ) -> None:
        super().__init__(default, **kw)
        #: ``x_api_key`` reads the ``x-api-key`` header when True.
        self.convert_underscores = convert_underscores


class Cookie(Param):
    """Read from a request cookie."""

    location = "cookie"


class Body(Param):
    """Read from the JSON request body.

    :param embed:
        When ``True``, the value is looked up under the parameter's own name
        inside the JSON object instead of being the whole body.
    """

    location = "body"

    def __init__(
        self,
        default: t.Any = REQUIRED,
        *,
        embed: bool = False,
        media_type: str = "application/json",
        **kw: t.Any,
    ) -> None:
        super().__init__(default, **kw)
        #: Look the value up under this parameter's name inside the body.
        self.embed = embed
        self.media_type = media_type


class Form(Body):
    """Read a single field from a urlencoded or multipart body."""

    location = "form"

    def __init__(self, default: t.Any = REQUIRED, **kw: t.Any) -> None:
        kw.setdefault("media_type", "application/x-www-form-urlencoded")
        super().__init__(default, embed=True, **kw)


class File(Body):
    """Read an uploaded file from a multipart body."""

    location = "file"

    def __init__(self, default: t.Any = REQUIRED, **kw: t.Any) -> None:
        kw.setdefault("media_type", "multipart/form-data")
        super().__init__(default, embed=True, **kw)


class Depends:
    """Resolve this parameter by calling another function (or class).

    The dependency may itself declare parameters and dependencies; the resolver
    walks the whole graph.  Generator dependencies get teardown after the
    response is sent::

        def get_db():
            session = Session()
            try:
                yield session
            finally:
                session.close()

        @app.get("/users")
        def users(db = Depends(get_db)):
            ...
    """

    __slots__ = ("dependency", "scope", "use_cache")

    def __init__(
        self,
        dependency: t.Callable[..., t.Any] | None = None,
        *,
        use_cache: bool = True,
        scope: str = "request",
    ) -> None:
        #: ``None`` means "use the parameter's type annotation as the provider".
        self.dependency = dependency
        #: Cache the result for the lifetime of the scope.
        self.use_cache = use_cache
        #: One of ``"request"``, ``"singleton"``, ``"transient"``.
        self.scope = scope

    def __repr__(self) -> str:
        name = getattr(self.dependency, "__name__", self.dependency)
        return f"Depends({name}, scope={self.scope!r})"


class Inject:
    """Resolve this parameter from the module container by token.

    The NestJS-style counterpart to :class:`Depends`: instead of naming a
    callable, you name a *token* that some module provides::

        @injectable()
        class UserService: ...

        @controller("/users")
        class UserController:
            def __init__(self, users: UserService = Inject(UserService)): ...
    """

    __slots__ = ("token",)

    def __init__(self, token: t.Any = None) -> None:
        self.token = token

    def __repr__(self) -> str:
        return f"Inject({getattr(self.token, '__name__', self.token)!r})"


class _Marker:
    """Annotation-only marker (``req: Req``) requiring no default value."""

    __slots__ = ()


class Req(_Marker):
    """Annotate a parameter to receive the :class:`~slowfw.request.Request`."""


class Res(_Marker):
    """Annotate a parameter to receive the :class:`~slowfw.response.Response`."""


class Ctx(_Marker):
    """Annotate a parameter to receive the execution context."""
