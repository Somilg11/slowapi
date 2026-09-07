"""Pipes: transform or validate a single argument before the handler sees it.

SlowAPI already coerces arguments from their annotations, so pipes exist for
the cases annotations cannot express -- trimming whitespace, normalising an
email, enforcing an app-specific rule -- and for reusing one transformation
across many handlers.

Pipes run *after* type coercion, so ``value`` already has its declared type.
"""

from __future__ import annotations

import inspect
import typing as t

from .concurrency import maybe_await
from .exceptions import BadRequest
from .execution import ArgumentMetadata, ExecutionContext

__all__ = [
    "ClampPipe",
    "DefaultValuePipe",
    "LowercasePipe",
    "NotEmptyPipe",
    "ParseIntPipe",
    "Pipe",
    "TrimPipe",
    "run_pipes",
    "use_pipes",
]


class Pipe:
    """Base class for pipes.  Override :meth:`transform`."""

    def transform(self, value: t.Any, meta: ArgumentMetadata) -> t.Any:
        return value


def use_pipes(*pipes: t.Any) -> t.Callable[[t.Any], t.Any]:
    """Attach pipes to a handler or controller class."""

    def decorator(target: t.Any) -> t.Any:
        existing = tuple(getattr(target, "__slowfw_pipes__", ()))
        target.__slowfw_pipes__ = existing + pipes
        return target

    return decorator


def collect_pipes(handler: t.Any, controller: type | None) -> tuple[t.Any, ...]:
    class_level = tuple(getattr(controller, "__slowfw_pipes__", ())) if controller else ()
    return class_level + tuple(getattr(handler, "__slowfw_pipes__", ()))


async def run_pipes(
    pipes: t.Sequence[t.Any],
    value: t.Any,
    meta: ArgumentMetadata,
    ctx: ExecutionContext | None = None,
) -> t.Any:
    """Feed ``value`` through every pipe in order."""
    for pipe in pipes:
        instance = pipe
        if inspect.isclass(pipe):
            instance = await ctx.container.resolve(pipe) if ctx else pipe()
        hook = getattr(instance, "transform", instance)
        value = await maybe_await(hook(value, meta))
    return value


class DefaultValuePipe(Pipe):
    """Substitute a default when the incoming value is ``None``."""

    def __init__(self, default: t.Any) -> None:
        self.default = default

    def transform(self, value: t.Any, meta: ArgumentMetadata) -> t.Any:
        return self.default if value is None else value


class TrimPipe(Pipe):
    """Strip surrounding whitespace from strings, recursing into lists."""

    def transform(self, value: t.Any, meta: ArgumentMetadata) -> t.Any:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return [v.strip() if isinstance(v, str) else v for v in value]
        return value


class LowercasePipe(Pipe):
    def transform(self, value: t.Any, meta: ArgumentMetadata) -> t.Any:
        return value.lower() if isinstance(value, str) else value


class ParseIntPipe(Pipe):
    """Force an integer, with a clear 400 instead of a 422 type error."""

    def transform(self, value: t.Any, meta: ArgumentMetadata) -> t.Any:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise BadRequest(f"{meta.name!r} must be an integer") from None


class ClampPipe(Pipe):
    """Silently clamp a number into a range, rather than rejecting it.

    Useful for pagination limits where a caller sending ``?limit=100000``
    should get the maximum page rather than an error.
    """

    def __init__(self, minimum: float | None = None, maximum: float | None = None) -> None:
        self.minimum = minimum
        self.maximum = maximum

    def transform(self, value: t.Any, meta: ArgumentMetadata) -> t.Any:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return value
        if self.minimum is not None:
            value = max(self.minimum, value)
        if self.maximum is not None:
            value = min(self.maximum, value)
        return value


class NotEmptyPipe(Pipe):
    """Reject ``None`` and zero-length values.

    Length-based on purpose, which means ``"   "`` passes.  Pair it with
    :class:`TrimPipe` -- ``@use_pipes(TrimPipe(), NotEmptyPipe())`` -- when
    whitespace should not count as content.
    """

    def transform(self, value: t.Any, meta: ArgumentMetadata) -> t.Any:
        if value is None or (hasattr(value, "__len__") and len(value) == 0):
            raise BadRequest(f"{meta.name!r} must not be empty")
        return value
