"""Type-driven coercion and validation, with no third-party dependency.

Everything is driven by standard :mod:`typing` annotations and
:mod:`dataclasses`, so a DTO is just::

    @dataclass
    class CreateUser:
        email: str
        age: int = 18
        tags: list[str] = field(default_factory=list)

If :mod:`pydantic` happens to be installed, models are detected by duck typing
and delegated to, so teams already invested in it lose nothing.  It is never
imported unless the user's own annotation refers to it.
"""

from __future__ import annotations

import dataclasses
import decimal
import enum
import inspect
import ipaddress
import re
import types
import typing as t
import uuid as _uuid
from datetime import date, datetime, time, timedelta, timezone

from .exceptions import ValidationError

__all__ = [
    "FieldError",
    "coerce",
    "dump",
    "is_dto",
    "json_schema_for",
    "validate_param",
]

NoneType = type(None)
_TRUE = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE = frozenset({"0", "false", "f", "no", "n", "off", ""})


class FieldError(Exception):
    """A single validation failure, collected into a 422 response."""

    def __init__(self, loc: tuple[str, ...], message: str, kind: str = "type_error") -> None:
        self.loc = loc
        self.message = message
        self.kind = kind
        super().__init__(message)

    def as_dict(self) -> dict[str, t.Any]:
        return {"loc": list(self.loc), "message": self.message, "type": self.kind}


# --------------------------------------------------------------------- probes


def is_union(origin: t.Any) -> bool:
    """True for ``Union[...]`` and for the ``X | Y`` form.

    These are the same object on Python 3.14 and two different ones before it,
    so both have to be named.  Checking only one silently skipped every
    ``X | None`` annotation on 3.10 through 3.13 -- an optional nested model was
    handed to the handler as a raw dict rather than the model it declared.
    """
    return origin is t.Union or origin is types.UnionType


def _is_pydantic(annotation: t.Any) -> bool:
    return inspect.isclass(annotation) and (
        hasattr(annotation, "model_validate") or hasattr(annotation, "parse_obj")
    )


def _is_typed_dict(annotation: t.Any) -> bool:
    return (
        inspect.isclass(annotation)
        and issubclass(annotation, dict)
        and hasattr(annotation, "__annotations__")
        and hasattr(annotation, "__total__")
    )


def is_dto(annotation: t.Any) -> bool:
    """Return True for anything we can build from a JSON object."""
    return (
        (dataclasses.is_dataclass(annotation) and inspect.isclass(annotation))
        or _is_pydantic(annotation)
        or _is_typed_dict(annotation)
    )


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    # Unix timestamps are common enough to be worth accepting.
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (ValueError, OSError):
        raise ValueError(f"{value!r} is not an ISO-8601 datetime") from None


_SCALARS: dict[t.Any, t.Callable[[t.Any], t.Any]] = {
    str: lambda v: v if isinstance(v, str) else str(v),
    bytes: lambda v: v if isinstance(v, bytes) else str(v).encode(),
    int: lambda v: int(v),
    float: lambda v: float(v),
    decimal.Decimal: lambda v: decimal.Decimal(str(v)),
    _uuid.UUID: lambda v: v if isinstance(v, _uuid.UUID) else _uuid.UUID(str(v)),
    datetime: lambda v: v if isinstance(v, datetime) else _parse_datetime(str(v)),
    date: lambda v: (
        v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v))
    ),
    time: lambda v: v if isinstance(v, time) else time.fromisoformat(str(v)),
    timedelta: lambda v: v if isinstance(v, timedelta) else timedelta(seconds=float(v)),
    ipaddress.IPv4Address: lambda v: ipaddress.IPv4Address(str(v)),
    ipaddress.IPv6Address: lambda v: ipaddress.IPv6Address(str(v)),
}


def _coerce_bool(value: t.Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{value!r} is not a valid boolean")


# ------------------------------------------------------------------- coercion


def coerce(value: t.Any, annotation: t.Any, loc: tuple[str, ...] = ()) -> t.Any:
    """Convert ``value`` to ``annotation``, raising :class:`FieldError`.

    Coercion is deliberately lenient about *representation* (``"3"`` becomes
    ``3``) and strict about *meaning* (``"three"`` is an error).  Query strings
    and headers are always text on the wire, so refusing to coerce would make
    typed handlers useless.
    """
    if annotation is inspect.Parameter.empty or annotation is t.Any or annotation is None:
        return value

    origin = t.get_origin(annotation)
    args = t.get_args(annotation)

    # Optional[X] / Union[...]
    if is_union(origin):
        if value is None and NoneType in args:
            return None
        errors: list[str] = []
        for candidate in args:
            if candidate is NoneType:
                continue
            try:
                return coerce(value, candidate, loc)
            except FieldError as exc:
                errors.append(exc.message)
        if NoneType in args and value in (None, ""):
            return None
        raise FieldError(loc, f"Value does not match any of: {'; '.join(errors)}", "union_error")

    if origin is t.Literal:
        for candidate in args:
            if value == candidate or str(value) == str(candidate):
                return candidate
        raise FieldError(loc, f"Must be one of {list(args)!r}", "literal_error")

    if origin in (list, set, frozenset, tuple):
        if isinstance(value, (str, bytes)):
            value = [value]
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise FieldError(loc, "Expected a sequence", "type_error")
        item_type = args[0] if args else t.Any
        if origin is tuple and len(args) > 1 and Ellipsis not in args:
            if len(value) != len(args):
                raise FieldError(loc, f"Expected exactly {len(args)} items", "length_error")
            return tuple(
                coerce(item, arg, (*loc, str(i)))
                for i, (item, arg) in enumerate(zip(value, args, strict=False))
            )
        items = [coerce(item, item_type, (*loc, str(i))) for i, item in enumerate(value)]
        return origin(items)

    if origin is dict:
        if not isinstance(value, dict):
            raise FieldError(loc, "Expected an object", "type_error")
        key_type = args[0] if args else t.Any
        val_type = args[1] if len(args) > 1 else t.Any
        return {
            coerce(k, key_type, loc): coerce(v, val_type, (*loc, str(k))) for k, v in value.items()
        }

    if annotation is bool:
        try:
            return _coerce_bool(value)
        except ValueError as exc:
            raise FieldError(loc, str(exc), "bool_error") from None

    if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
        try:
            return annotation(value)
        except ValueError:
            try:
                return annotation[str(value)]
            except KeyError:
                allowed = [e.value for e in annotation]
                raise FieldError(loc, f"Must be one of {allowed!r}", "enum_error") from None

    converter = _SCALARS.get(annotation)
    if converter is not None:
        try:
            return converter(value)
        except (TypeError, ValueError, decimal.InvalidOperation) as exc:
            name = getattr(annotation, "__name__", str(annotation))
            raise FieldError(loc, f"Not a valid {name}: {exc}", "type_error") from None

    if _is_pydantic(annotation):
        try:
            if hasattr(annotation, "model_validate"):
                return annotation.model_validate(value)
            return annotation.parse_obj(value)  # pydantic v1
        except Exception as exc:
            raise FieldError(loc, str(exc), "model_error") from None

    if dataclasses.is_dataclass(annotation) and inspect.isclass(annotation):
        return _coerce_dataclass(value, annotation, loc)

    if _is_typed_dict(annotation):
        return _coerce_typed_dict(value, annotation, loc)

    # Unknown annotation: hand the value through untouched rather than guessing.
    return value


def _coerce_dataclass(value: t.Any, model: type, loc: tuple[str, ...]) -> t.Any:
    if isinstance(value, model):
        return value
    if not isinstance(value, dict):
        raise FieldError(loc, f"Expected an object for {model.__name__}", "type_error")

    hints = t.get_type_hints(model)
    errors: list[FieldError] = []
    kwargs: dict[str, t.Any] = {}
    known = {f.name for f in dataclasses.fields(model)}

    for field in dataclasses.fields(model):
        if not field.init:
            continue
        annotation = hints.get(field.name, t.Any)
        if field.name in value:
            try:
                kwargs[field.name] = coerce(value[field.name], annotation, (*loc, field.name))
            except FieldError as exc:
                errors.append(exc)
        elif field.default is not dataclasses.MISSING:
            kwargs[field.name] = field.default
        elif field.default_factory is not dataclasses.MISSING:
            kwargs[field.name] = field.default_factory()
        else:
            errors.append(FieldError((*loc, field.name), "Field is required", "missing"))

    extra = set(value) - known
    if extra and getattr(model, "__slowapi_forbid_extra__", False):
        errors.append(FieldError(loc, f"Unexpected field(s): {sorted(extra)}", "extra_forbidden"))

    if errors:
        raise ValidationError([e.as_dict() for e in errors])
    return model(**kwargs)


def _coerce_typed_dict(value: t.Any, model: type, loc: tuple[str, ...]) -> t.Any:
    if not isinstance(value, dict):
        raise FieldError(loc, f"Expected an object for {model.__name__}", "type_error")
    hints = t.get_type_hints(model)
    required = getattr(model, "__required_keys__", frozenset(hints))
    errors: list[FieldError] = []
    result: dict[str, t.Any] = {}
    for key, annotation in hints.items():
        if key in value:
            try:
                result[key] = coerce(value[key], annotation, (*loc, key))
            except FieldError as exc:
                errors.append(exc)
        elif key in required:
            errors.append(FieldError((*loc, key), "Field is required", "missing"))
    if errors:
        raise ValidationError([e.as_dict() for e in errors])
    return result


# ----------------------------------------------------------------- constraints


def validate_param(value: t.Any, param: t.Any, loc: tuple[str, ...]) -> t.Any:
    """Apply the numeric/string constraints declared on a :class:`Param`."""
    if value is None:
        return value
    if isinstance(value, (int, float, decimal.Decimal)) and not isinstance(value, bool):
        if param.ge is not None and value < param.ge:
            raise FieldError(loc, f"Must be >= {param.ge}", "ge")
        if param.gt is not None and value <= param.gt:
            raise FieldError(loc, f"Must be > {param.gt}", "gt")
        if param.le is not None and value > param.le:
            raise FieldError(loc, f"Must be <= {param.le}", "le")
        if param.lt is not None and value >= param.lt:
            raise FieldError(loc, f"Must be < {param.lt}", "lt")
    if isinstance(value, (str, bytes, list, tuple, set)):
        if param.min_length is not None and len(value) < param.min_length:
            raise FieldError(loc, f"Must have at least {param.min_length} characters", "min_length")
        if param.max_length is not None and len(value) > param.max_length:
            raise FieldError(loc, f"Must have at most {param.max_length} characters", "max_length")
    if param.pattern is not None and isinstance(value, str):
        if re.search(param.pattern, value) is None:
            raise FieldError(loc, f"Must match {param.pattern!r}", "pattern")
    return value


# ---------------------------------------------------------------- JSON Schema

_PRIMITIVE_SCHEMA: dict[t.Any, dict[str, t.Any]] = {
    str: {"type": "string"},
    bytes: {"type": "string", "format": "binary"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    decimal.Decimal: {"type": "string", "format": "decimal"},
    _uuid.UUID: {"type": "string", "format": "uuid"},
    datetime: {"type": "string", "format": "date-time"},
    date: {"type": "string", "format": "date"},
    time: {"type": "string", "format": "time"},
    timedelta: {"type": "number", "format": "duration"},
    ipaddress.IPv4Address: {"type": "string", "format": "ipv4"},
    ipaddress.IPv6Address: {"type": "string", "format": "ipv6"},
    NoneType: {"type": "null"},
}


def json_schema_for(
    annotation: t.Any, components: dict[str, t.Any] | None = None
) -> dict[str, t.Any]:
    """Produce an OpenAPI 3.1 schema fragment for ``annotation``.

    Named models are hoisted into ``components`` and referenced by ``$ref`` so
    the generated document stays small and readable.
    """
    if annotation is inspect.Parameter.empty or annotation is t.Any:
        return {}
    if annotation in _PRIMITIVE_SCHEMA:
        return dict(_PRIMITIVE_SCHEMA[annotation])

    origin = t.get_origin(annotation)
    args = t.get_args(annotation)

    if is_union(origin):
        variants = [json_schema_for(a, components) for a in args]
        if len(variants) == 2 and {"type": "null"} in variants:
            other = next(v for v in variants if v != {"type": "null"})
            return {**other, "nullable": True} if other else {}
        return {"anyOf": variants}

    if origin is t.Literal:
        return {"enum": list(args)}

    if origin in (list, set, frozenset):
        return {"type": "array", "items": json_schema_for(args[0] if args else t.Any, components)}

    if origin is tuple:
        if args and Ellipsis not in args and len(args) > 1:
            return {"type": "array", "prefixItems": [json_schema_for(a, components) for a in args]}
        return {"type": "array", "items": json_schema_for(args[0] if args else t.Any, components)}

    if origin is dict:
        return {
            "type": "object",
            "additionalProperties": json_schema_for(
                args[1] if len(args) > 1 else t.Any, components
            ),
        }

    if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
        return {"enum": [e.value for e in annotation], "title": annotation.__name__}

    if _is_pydantic(annotation) and hasattr(annotation, "model_json_schema"):
        schema = annotation.model_json_schema(ref_template="#/components/schemas/{model}")
        defs = schema.pop("$defs", {})
        if components is not None:
            components.update(defs)
            components[annotation.__name__] = schema
            return {"$ref": f"#/components/schemas/{annotation.__name__}"}
        return schema

    if is_dto(annotation):
        name = annotation.__name__
        if components is not None and name in components:
            return {"$ref": f"#/components/schemas/{name}"}
        hints = t.get_type_hints(annotation)
        properties: dict[str, t.Any] = {}
        required: list[str] = []
        if dataclasses.is_dataclass(annotation):
            if components is not None:
                components[name] = {}  # placeholder guards against recursion
            for field in dataclasses.fields(annotation):
                properties[field.name] = json_schema_for(hints.get(field.name, t.Any), components)
                if (
                    field.default is dataclasses.MISSING
                    and field.default_factory is dataclasses.MISSING
                ):
                    required.append(field.name)
        else:  # TypedDict
            if components is not None:
                components[name] = {}
            for key, hint in hints.items():
                properties[key] = json_schema_for(hint, components)
            required = list(getattr(annotation, "__required_keys__", ()))

        schema = {"type": "object", "title": name, "properties": properties}
        if required:
            schema["required"] = required
        if annotation.__doc__ and not annotation.__doc__.startswith(name + "("):
            schema["description"] = inspect.cleandoc(annotation.__doc__)
        if components is not None:
            components[name] = schema
            return {"$ref": f"#/components/schemas/{name}"}
        return schema

    return {}


def dump(value: t.Any) -> t.Any:
    """Recursively convert DTOs and scalars into JSON-ready primitives."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: dump(v) for k, v in dataclasses.asdict(value).items()}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: dump(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [dump(v) for v in value]
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (_uuid.UUID, decimal.Decimal)):
        return str(value)
    return value
