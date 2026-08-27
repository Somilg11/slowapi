"""Handler introspection: work out where each argument comes from, once.

Signatures are analysed at registration time, not per request.  The result is a
flat list of :class:`ParamSpec` objects that the dispatcher walks; nothing in
the hot path calls :func:`inspect.signature`.

Inference is deliberately unsurprising:

1. A parameter with an explicit marker (``Query()``, ``Body()``, ``Depends()``)
   uses it.
2. A parameter annotated ``Request``/``Response``/``ExecutionContext`` receives
   that object.  So do untyped parameters conventionally named ``req``/``res``,
   which is what makes Express-style handlers work verbatim.
3. A parameter whose name is a path parameter of the route is a path param.
4. A parameter annotated with a DTO (dataclass, TypedDict, pydantic model) on a
   method that can carry a body is the body.
5. A parameter annotated with an injectable class resolves from the container.
6. Anything else is a query parameter.
"""

from __future__ import annotations

import inspect
import typing as t
from dataclasses import dataclass, field

from .concurrency import maybe_await
from .datastructures import UploadFile
from .exceptions import ConfigurationError, ValidationError
from .execution import ArgumentMetadata, ExecutionContext
from .params import (
    REQUIRED,
    Body,
    Ctx,
    Depends,
    File,
    Header,
    Inject,
    Param,
    Path,
    Query,
    Req,
    Res,
)
from .request import Request
from .response import Response
from .validation import FieldError, coerce, validate_param

__all__ = ["HandlerSignature", "ParamSpec", "analyse"]

#: Parameter names that receive the request/response when left unannotated.
_REQUEST_ALIASES = frozenset({"req", "request"})
_RESPONSE_ALIASES = frozenset({"res", "response"})
_CONTEXT_ALIASES = frozenset({"ctx", "context"})

#: Methods where an unannotated DTO parameter is read from the body.
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_EMPTY = inspect.Parameter.empty


@dataclass
class ParamSpec:
    """One resolved handler parameter."""

    name: str
    #: request | response | context | path | query | header | cookie | body |
    #: form | file | depends | inject | container
    source: str
    annotation: t.Any = t.Any
    param: Param | None = None
    default: t.Any = REQUIRED
    #: The key to look up on the wire, after alias/underscore handling.
    wire_name: str = ""
    #: For ``Depends``: the nested signature.
    sub: HandlerSignature | None = None
    dependency: t.Any = None
    use_cache: bool = True
    token: t.Any = None
    #: True when the annotation is a list/set/tuple and repeats should collect.
    is_sequence: bool = False
    #: Secondary location to try when the primary one has no value.  Used for
    #: scalar parameters on body-carrying methods, which read from the JSON
    #: body first and fall back to the query string.
    fallback: str | None = None

    @property
    def required(self) -> bool:
        return self.default is REQUIRED


@dataclass
class HandlerSignature:
    """Everything the dispatcher needs to call one handler."""

    handler: t.Callable[..., t.Any]
    specs: list[ParamSpec] = field(default_factory=list)
    return_annotation: t.Any = None
    #: True when any parameter (directly or transitively) needs the body.
    reads_body: bool = False
    #: True when the handler declared a ``res``-style parameter, i.e. it is
    #: written Express-style and may return ``None`` legitimately.
    uses_response: bool = False
    is_method: bool = False

    def flatten_dependencies(self) -> t.Iterator[ParamSpec]:
        for spec in self.specs:
            if spec.source == "depends" and spec.sub is not None:
                yield from spec.sub.flatten_dependencies()
                yield spec


def _is_marker(annotation: t.Any, marker: type) -> bool:
    return annotation is marker or (inspect.isclass(annotation) and issubclass(annotation, marker))


def _sequence_annotation(annotation: t.Any) -> bool:
    return t.get_origin(annotation) in (list, set, frozenset, tuple)


def analyse(
    handler: t.Callable[..., t.Any],
    *,
    path_params: t.Collection[str] = (),
    method: str = "GET",
    container_tokens: t.Collection[t.Any] = (),
    is_method: bool = False,
) -> HandlerSignature:
    """Build a :class:`HandlerSignature` for ``handler``.

    :param container_tokens:
        Tokens the DI container knows about.  A parameter annotated with one of
        them is injected rather than being read off the query string, which is
        what lets services be constructor-free in function handlers.
    """
    from .validation import is_dto

    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Cannot inspect handler {handler!r}: {exc}") from exc

    # A callable object's annotations live on its ``__call__``; asking the
    # instance yields nothing and every parameter looks untyped.
    hint_target: t.Any = handler
    if not (inspect.isfunction(handler) or inspect.ismethod(handler)):
        hint_target = type(handler).__call__ if callable(handler) else handler
    try:
        hints = t.get_type_hints(hint_target, include_extras=True)
    except Exception as exc:
        raise ConfigurationError(
            f"Could not resolve type hints for {getattr(handler, '__qualname__', handler)!r}: {exc}. "
            "Check for forward references to names that are not importable at runtime."
        ) from exc

    result = HandlerSignature(
        handler=handler,
        return_annotation=hints.get("return"),
        is_method=is_method,
    )

    parameters = list(signature.parameters.items())
    if is_method and parameters and parameters[0][0] in ("self", "cls"):
        parameters = parameters[1:]

    for name, parameter in parameters:
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue

        annotation = hints.get(name, parameter.annotation)
        default = parameter.default
        marker = default if isinstance(default, (Param, Depends, Inject)) else None
        plain_default = REQUIRED if (marker is not None or default is _EMPTY) else default

        spec = _classify(
            name=name,
            annotation=annotation,
            marker=marker,
            plain_default=plain_default,
            path_params=path_params,
            method=method,
            container_tokens=container_tokens,
            is_dto=is_dto,
        )

        if spec.source == "response":
            result.uses_response = True
        if spec.source in ("body", "form", "file"):
            result.reads_body = True
        if spec.source == "depends":
            spec.sub = analyse(
                spec.dependency,
                path_params=path_params,
                method=method,
                container_tokens=container_tokens,
                is_method=inspect.isclass(spec.dependency),
            )
            if spec.sub.reads_body:
                result.reads_body = True
        result.specs.append(spec)

    return result


def _classify(
    *,
    name: str,
    annotation: t.Any,
    marker: t.Any,
    plain_default: t.Any,
    path_params: t.Collection[str],
    method: str,
    container_tokens: t.Collection[t.Any],
    is_dto: t.Callable[[t.Any], bool],
) -> ParamSpec:
    """Decide the source of a single parameter."""
    sequence = _sequence_annotation(annotation)

    if isinstance(marker, Depends):
        dependency = marker.dependency
        if dependency is None:
            if annotation is _EMPTY:
                raise ConfigurationError(
                    f"Depends() on parameter {name!r} needs either a callable or a "
                    "type annotation to resolve."
                )
            dependency = annotation
        return ParamSpec(
            name=name,
            source="depends",
            annotation=annotation,
            dependency=dependency,
            use_cache=marker.use_cache,
            default=plain_default,
        )

    if isinstance(marker, Inject):
        return ParamSpec(
            name=name,
            source="inject",
            annotation=annotation,
            token=marker.token or annotation,
            default=plain_default,
        )

    if isinstance(marker, Param):
        location = marker.location
        wire = marker.alias or name
        if isinstance(marker, Header) and marker.alias is None and marker.convert_underscores:
            wire = name.replace("_", "-")
        return ParamSpec(
            name=name,
            source=location,
            annotation=annotation,
            param=marker,
            default=marker.default if not marker.required else REQUIRED,
            wire_name=wire,
            is_sequence=sequence or marker.explode,
        )

    # Framework objects, by annotation or by convention.
    if annotation is Request or _is_marker(annotation, Req):
        return ParamSpec(name, "request", annotation)
    if annotation is Response or _is_marker(annotation, Res):
        return ParamSpec(name, "response", annotation)
    if annotation is ExecutionContext or _is_marker(annotation, Ctx):
        return ParamSpec(name, "context", annotation)
    if annotation is _EMPTY:
        if name in _REQUEST_ALIASES:
            return ParamSpec(name, "request", annotation)
        if name in _RESPONSE_ALIASES:
            return ParamSpec(name, "response", annotation)
        if name in _CONTEXT_ALIASES:
            return ParamSpec(name, "context", annotation)

    if name in path_params:
        return ParamSpec(name, "path", annotation, Path(), REQUIRED, name)

    if annotation is UploadFile or (sequence and t.get_args(annotation)[:1] == (UploadFile,)):
        return ParamSpec(
            name, "file", annotation, File(), plain_default, name, is_sequence=sequence
        )

    if annotation is not _EMPTY and annotation in container_tokens:
        return ParamSpec(name, "inject", annotation, token=annotation, default=plain_default)

    if annotation is not _EMPTY and is_dto(annotation) and method in _BODY_METHODS:
        return ParamSpec(name, "body", annotation, Body(plain_default), plain_default, name)

    if method in _BODY_METHODS and not sequence:
        # On POST/PUT/PATCH/DELETE a bare scalar almost always comes from the
        # JSON body -- that is what an Express or Nest handler would expect --
        # so try the body first and fall back to the query string.
        return ParamSpec(
            name,
            "body",
            annotation,
            Body(plain_default, embed=True),
            plain_default,
            name,
            fallback="query",
        )

    return ParamSpec(
        name,
        "query",
        annotation,
        Query(plain_default),
        plain_default,
        name,
        is_sequence=sequence,
    )


class Resolver:
    """Turns a :class:`HandlerSignature` into a kwargs dict for one request."""

    def __init__(self, ctx: ExecutionContext, pipes: t.Sequence[t.Any] = ()) -> None:
        self.ctx = ctx
        self.pipes = pipes
        self._body: t.Any = _MISSING
        self._form: t.Any = _MISSING
        self._cache: dict[t.Any, t.Any] = {}
        self.errors: list[dict[str, t.Any]] = []

    async def _get_body(self) -> t.Any:
        if self._body is _MISSING:
            self._body = await self.ctx.request.json()
        return self._body

    async def _get_form(self) -> t.Any:
        if self._form is _MISSING:
            self._form = await self.ctx.request.form()
            # Spooled uploads hold file descriptors; tie them to the request.
            self.ctx.container.on_close(self._form.close)
        return self._form

    async def build(self, signature: HandlerSignature) -> dict[str, t.Any]:
        """Resolve every parameter, collecting all validation errors at once."""
        kwargs: dict[str, t.Any] = {}
        for spec in signature.specs:
            try:
                kwargs[spec.name] = await self._resolve(spec)
            except FieldError as exc:
                self.errors.append(exc.as_dict())
            except ValidationError as exc:
                self.errors.extend(exc.errors)
        if self.errors:
            raise ValidationError(self.errors)
        return kwargs

    async def _resolve(self, spec: ParamSpec) -> t.Any:
        request = self.ctx.request

        if spec.source == "request":
            return request
        if spec.source == "response":
            return self.ctx.response
        if spec.source == "context":
            return self.ctx
        if spec.source == "inject":
            return await self.ctx.container.resolve(spec.token)
        if spec.source == "depends":
            return await self._resolve_dependency(spec)

        raw = await self._extract(spec)
        loc = (spec.source, spec.wire_name or spec.name)

        if raw is _MISSING:
            if spec.required:
                raise FieldError(loc, "Field is required", "missing")
            value = spec.param.get_default() if spec.param is not None else spec.default
        else:
            value = coerce(raw, spec.annotation, loc)
            if spec.param is not None:
                value = validate_param(value, spec.param, loc)

        if self.pipes:
            from .pipes import run_pipes

            value = await run_pipes(
                self.pipes,
                value,
                ArgumentMetadata(spec.source, spec.name, spec.annotation),
                self.ctx,
            )
        return value

    async def _extract(self, spec: ParamSpec) -> t.Any:
        request = self.ctx.request
        key = spec.wire_name or spec.name

        if spec.source == "path":
            return request.path_params.get(spec.name, _MISSING)

        if spec.source == "query":
            params = request.query_params
            if key not in params:
                return _MISSING
            return params.getlist(key) if spec.is_sequence else params[key]

        if spec.source == "header":
            values = request.headers.getlist(key)
            if not values:
                return _MISSING
            return values if spec.is_sequence else values[-1]

        if spec.source == "cookie":
            return request.cookies.get(key, _MISSING)

        if spec.source == "body":
            payload = await self._get_body()
            embed = getattr(spec.param, "embed", False)
            if payload is not None and embed:
                if isinstance(payload, dict) and key in payload:
                    return payload[key]
            elif payload is not None:
                return payload
            if spec.fallback == "query" and key in request.query_params:
                return request.query_params[key]
            return _MISSING

        if spec.source in ("form", "file"):
            form = await self._get_form()
            if key not in form:
                return _MISSING
            return form.getlist(key) if spec.is_sequence else form[key]

        return _MISSING

    async def _resolve_dependency(self, spec: ParamSpec) -> t.Any:
        cache_key = spec.dependency
        if spec.use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        assert spec.sub is not None
        kwargs = await self.build(spec.sub)
        target = spec.dependency

        if inspect.isclass(target):
            value = target(**kwargs)
        else:
            value = target(**kwargs)
            value = await self._finish(value)

        if spec.use_cache:
            self._cache[cache_key] = value
        return value

    async def _finish(self, value: t.Any) -> t.Any:
        """Drive generator dependencies to their first yield and defer cleanup."""
        if inspect.isasyncgen(value):
            first = await value.__anext__()
            self.ctx.container._teardowns.append(value)
            return first
        if inspect.isgenerator(value):
            first = next(value)
            self.ctx.container._teardowns.append(value)
            return first
        return await maybe_await(value)


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<missing>"

    def __bool__(self) -> bool:
        return False


_MISSING = _Missing()
