"""Declarative output shaping: decide what leaves the process.

The most common security bug in a JSON API is an over-serialised model -- a
password hash, an internal flag, another tenant's id -- returned because the
handler forwarded a database row verbatim.  This module makes the safe thing
the short thing: describe exposure once on the DTO, and every handler that
returns it is shaped the same way.

::

    @dataclass
    class User:
        id: int
        email: str = field(metadata=expose(groups=("self", "admin")))
        password_hash: str = field(metadata=hidden())
        created: datetime = field(metadata=expose(alias="createdAt"))

    serialize(user)                    # -> {"id": 1, "createdAt": "..."}
    serialize(user, groups=("admin",)) # -> {"id": 1, "email": ..., "createdAt": ...}
"""

from __future__ import annotations

import dataclasses
import typing as t

from .execution import ExecutionContext
from .interceptors import CallNext, Interceptor
from .validation import dump

__all__ = [
    "FieldOptions",
    "SerializerInterceptor",
    "expose",
    "hidden",
    "serialize",
    "serialize_with",
]

METADATA_KEY = "slowfw"


class FieldOptions(t.NamedTuple):
    """Per-field serialisation rules, stored in dataclass field metadata."""

    exposed: bool = True
    #: Only serialise when the caller asks for one of these groups.
    groups: tuple[str, ...] = ()
    #: Rename the key on the way out (``created`` -> ``createdAt``).
    alias: str | None = None
    #: Accepted on input but never written to output (passwords, tokens).
    write_only: bool = False
    #: Applied to the value just before it is emitted.
    transform: t.Callable[[t.Any], t.Any] | None = None


def expose(
    *,
    groups: t.Sequence[str] = (),
    alias: str | None = None,
    transform: t.Callable[[t.Any], t.Any] | None = None,
) -> dict[str, t.Any]:
    """Field metadata marking a field as serialisable, optionally gated."""
    return {
        METADATA_KEY: FieldOptions(
            exposed=True, groups=tuple(groups), alias=alias, transform=transform
        )
    }


def hidden(*, write_only: bool = True) -> dict[str, t.Any]:
    """Field metadata that keeps a field out of every response."""
    return {METADATA_KEY: FieldOptions(exposed=False, write_only=write_only)}


def _options_for(field: dataclasses.Field) -> FieldOptions:
    raw = field.metadata.get(METADATA_KEY)
    return raw if isinstance(raw, FieldOptions) else FieldOptions()


def serialize(
    value: t.Any,
    *,
    groups: t.Sequence[str] = (),
    include: t.Collection[str] | None = None,
    exclude: t.Collection[str] | None = None,
    exclude_none: bool = False,
) -> t.Any:
    """Recursively convert ``value`` into JSON-ready data, applying rules.

    :param groups:
        Group names the caller is entitled to; a field gated on groups is only
        emitted when one of its groups is present here.
    :param include:
        Whitelist of top-level field names.  Wins over everything else.
    :param exclude:
        Blacklist of top-level field names, applied after ``include``.
    """
    active = set(groups)

    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            serialize(v, groups=groups, include=include, exclude=exclude, exclude_none=exclude_none)
            for v in value
        ]

    if isinstance(value, dict):
        result = {
            k: serialize(v, groups=groups, exclude_none=exclude_none) for k, v in value.items()
        }
        if include is not None:
            result = {k: v for k, v in result.items() if k in include}
        if exclude:
            result = {k: v for k, v in result.items() if k not in exclude}
        return result

    if not (dataclasses.is_dataclass(value) and not isinstance(value, type)):
        return dump(value)

    class_exclude = set(getattr(type(value), "__slowfw_exclude__", ()))
    output: dict[str, t.Any] = {}
    for field in dataclasses.fields(value):
        options = _options_for(field)
        name = field.name
        if name in class_exclude or not options.exposed or options.write_only:
            continue
        if options.groups and not (active & set(options.groups)):
            continue
        if include is not None and name not in include:
            continue
        if exclude and name in exclude:
            continue

        raw = getattr(value, name)
        if options.transform is not None:
            raw = options.transform(raw)
        rendered = serialize(raw, groups=groups, exclude_none=exclude_none)
        if exclude_none and rendered is None:
            continue
        output[options.alias or name] = rendered
    return output


def serialize_with(
    *,
    groups: t.Sequence[str] = (),
    include: t.Collection[str] | None = None,
    exclude: t.Collection[str] | None = None,
    exclude_none: bool = False,
) -> t.Callable[[t.Any], t.Any]:
    """Handler/controller decorator that pins serialisation options.

    Pair it with :class:`SerializerInterceptor`, or let the application apply
    it automatically -- ``SlowAPI(auto_serialize=True)`` is the default.
    """

    def decorator(target: t.Any) -> t.Any:
        target.__slowfw_serialize__ = {
            "groups": tuple(groups),
            "include": include,
            "exclude": exclude,
            "exclude_none": exclude_none,
        }
        return target

    return decorator


def serialization_options(handler: t.Any, controller: type | None) -> dict[str, t.Any]:
    """Merge class-level and handler-level serialisation options."""
    merged: dict[str, t.Any] = {}
    if controller is not None:
        merged.update(getattr(controller, "__slowfw_serialize__", {}) or {})
    merged.update(getattr(handler, "__slowfw_serialize__", {}) or {})
    return merged


class SerializerInterceptor(Interceptor):
    """Apply :func:`serialize` to whatever the handler returned."""

    def __init__(self, **options: t.Any) -> None:
        self.options = options

    async def intercept(self, ctx: ExecutionContext, call_next: CallNext) -> t.Any:
        from .response import Response

        result = await call_next()
        if isinstance(result, Response) or result is None:
            return result
        merged = {**self.options, **serialization_options(ctx.handler, ctx.controller)}
        return serialize(result, **merged)
