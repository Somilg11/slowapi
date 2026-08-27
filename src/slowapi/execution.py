"""The execution context handed to guards, interceptors and pipes.

One object carries everything a cross-cutting concern could need: the request,
the response being built, the matched route, the request-scoped container, and
the merged metadata from class- and method-level decorators.
"""

from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import SlowAPI
    from .di import Container
    from .request import Request
    from .response import Response
    from .routing import Route

__all__ = ["ArgumentMetadata", "ExecutionContext", "get_metadata", "set_metadata_on"]

METADATA_ATTR = "__slowapi_metadata__"


def set_metadata_on(target: t.Any, key: str, value: t.Any) -> t.Any:
    """Attach a metadata key to a handler function or controller class."""
    existing = dict(getattr(target, METADATA_ATTR, {}))
    existing[key] = value
    setattr(target, METADATA_ATTR, existing)
    return target


def get_metadata(target: t.Any) -> dict[str, t.Any]:
    return dict(getattr(target, METADATA_ATTR, {}))


class ArgumentMetadata(t.NamedTuple):
    """Describes the argument a pipe is transforming."""

    #: ``"query"``, ``"path"``, ``"header"``, ``"cookie"``, ``"body"``, ``"form"``.
    location: str
    name: str
    annotation: t.Any


class ExecutionContext:
    """Everything a guard, interceptor, or pipe needs to make a decision."""

    __slots__ = ("_meta", "app", "container", "request", "response", "route")

    def __init__(
        self,
        request: Request,
        response: Response,
        route: Route,
        container: Container,
        app: SlowAPI,
    ) -> None:
        self.request = request
        self.response = response
        self.route = route
        self.container = container
        self.app = app
        self._meta: dict[str, t.Any] | None = None

    @property
    def handler(self) -> t.Callable[..., t.Any]:
        """The function that will ultimately run."""
        return self.route.handler

    @property
    def controller(self) -> type | None:
        """The controller class the handler belongs to, if any."""
        return getattr(self.route.handler, "__slowapi_controller__", None)

    @property
    def metadata(self) -> dict[str, t.Any]:
        """Class metadata merged with handler metadata (handler wins)."""
        if self._meta is None:
            merged: dict[str, t.Any] = {}
            controller = self.controller
            if controller is not None:
                merged.update(get_metadata(controller))
            merged.update(get_metadata(self.route.handler))
            self._meta = merged
        return self._meta

    def get(self, key: str, default: t.Any = None) -> t.Any:
        """Read one metadata key set by ``@set_metadata`` or its aliases."""
        return self.metadata.get(key, default)

    async def resolve(self, token: t.Any) -> t.Any:
        """Pull a provider out of the request-scoped container."""
        return await self.container.resolve(token)

    @property
    def state(self) -> t.Any:
        """Shortcut for ``ctx.request.state``."""
        return self.request.state

    def __repr__(self) -> str:
        return f"<ExecutionContext {self.route.method} {self.route.path}>"
