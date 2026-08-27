"""Guards: authorisation decisions that run before anything is injected.

A guard answers one question -- *may this request proceed?* -- and it answers
it before the body is parsed, before dependencies are constructed, and before
the handler runs.  That ordering is the point: rejecting an unauthorised
request should never cost a database connection.

Guards are ordinary callables or classes with ``can_activate``.  Classes are
built by the DI container, so a guard can inject services like anything else::

    @injectable()
    class RoleGuard:
        def __init__(self, users: UserService):
            self.users = users

        async def can_activate(self, ctx: ExecutionContext) -> bool:
            required = ctx.get("roles", [])
            if not required:
                return True
            user = await self.users.from_token(ctx.request.get("authorization"))
            return bool(user and set(required) & set(user.roles))
"""

from __future__ import annotations

import inspect
import typing as t

from .concurrency import maybe_await
from .exceptions import Forbidden, Unauthorized
from .execution import ExecutionContext, set_metadata_on

__all__ = [
    "AllowAll",
    "DenyAll",
    "Guard",
    "GuardResult",
    "RequireHeader",
    "public",
    "roles",
    "run_guards",
    "use_guards",
]

#: A guard may return a bool, or raise an HTTPException for a custom status.
GuardResult = bool | t.Awaitable[bool]


@t.runtime_checkable
class Guard(t.Protocol):
    """Structural type for guards."""

    def can_activate(self, ctx: ExecutionContext) -> GuardResult:  # pragma: no cover
        ...


def use_guards(*guards: t.Any) -> t.Callable[[t.Any], t.Any]:
    """Attach guards to a handler or an entire controller class.

    Class-level guards run before method-level ones.
    """

    def decorator(target: t.Any) -> t.Any:
        existing = tuple(getattr(target, "__slowapi_guards__", ()))
        target.__slowapi_guards__ = existing + guards
        return target

    return decorator


def roles(*names: str) -> t.Callable[[t.Any], t.Any]:
    """Declare the roles a handler requires.

    This only records metadata; a guard of your choosing reads it via
    ``ctx.get("roles")`` and decides what to do.  Keeping the policy out of the
    decorator is what lets one metadata vocabulary serve many auth backends.
    """

    def decorator(target: t.Any) -> t.Any:
        return set_metadata_on(target, "roles", list(names))

    return decorator


def public(target: t.Any) -> t.Any:
    """Mark a handler as not requiring authentication.

    Guards should honour it with ``if ctx.get("public"): return True``.
    """
    return set_metadata_on(target, "public", True)


def collect_guards(handler: t.Any, controller: type | None) -> tuple[t.Any, ...]:
    """Merge class-level and handler-level guards, class first."""
    class_guards = tuple(getattr(controller, "__slowapi_guards__", ())) if controller else ()
    handler_guards = tuple(getattr(handler, "__slowapi_guards__", ()))
    return class_guards + handler_guards


async def run_guards(guards: t.Sequence[t.Any], ctx: ExecutionContext) -> None:
    """Run every guard in order, raising 403 on the first refusal."""
    for guard in guards:
        instance = guard
        if inspect.isclass(guard):
            instance = await ctx.container.resolve(guard)
        check = getattr(instance, "can_activate", instance)
        allowed = await maybe_await(check(ctx))
        if not allowed:
            name = getattr(guard, "__name__", type(guard).__name__)
            raise Forbidden(f"Blocked by {name}")


class AllowAll:
    """A guard that lets everything through.  Useful as a default."""

    def can_activate(self, ctx: ExecutionContext) -> bool:
        return True


class DenyAll:
    """A guard that blocks everything.  Useful for disabling a route fast."""

    def can_activate(self, ctx: ExecutionContext) -> bool:
        return False


class RequireHeader:
    """Reject requests that do not carry a header, optionally with a value."""

    def __init__(self, name: str, value: str | None = None) -> None:
        self.name = name
        self.value = value

    def can_activate(self, ctx: ExecutionContext) -> bool:
        actual = ctx.request.get(self.name)
        if actual is None:
            raise Unauthorized(f"Missing required header {self.name!r}")
        return self.value is None or actual == self.value
