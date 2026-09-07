"""A small but complete dependency-injection container.

This is the NestJS half of SlowAPI's personality, minus the TypeScript
metadata reflection that Python does not need -- ordinary type annotations are
the metadata.

Three provider scopes exist:

``singleton``
    Built once per application.  The default, and what you want for stateless
    services, clients, and connection pools.
``request``
    Built once per request and torn down when the response is flushed.
``transient``
    Built fresh at every injection site.

Providers are registered against a *token*, which is normally the class itself
but may be any hashable value -- strings and :class:`InjectionToken` instances
let you swap implementations without the consumer knowing.
"""

from __future__ import annotations

import inspect
import typing as t
from dataclasses import dataclass, field

from .concurrency import maybe_await
from .exceptions import ConfigurationError
from .logging import get_logger

__all__ = [
    "Container",
    "InjectionToken",
    "Provider",
    "Scope",
    "injectable",
    "is_injectable",
    "provider_scope",
]

_logger = get_logger("slowfw.di")

Scope = str  # one of "singleton" | "request" | "transient"
SINGLETON: Scope = "singleton"
REQUEST: Scope = "request"
TRANSIENT: Scope = "transient"
_SCOPES = frozenset({SINGLETON, REQUEST, TRANSIENT})

_INJECTABLE_ATTR = "__slowfw_injectable__"
_SCOPE_ATTR = "__slowfw_scope__"


class InjectionToken:
    """An opaque, hashable identity for a provider.

    Use it when the thing you inject is not a class -- configuration values,
    interfaces described only by a Protocol, third-party clients::

        DATABASE_URL = InjectionToken("DATABASE_URL")
        module(providers=[Provider(DATABASE_URL, use_value=os.environ["DSN"])])
    """

    __slots__ = ("description", "name")

    def __init__(self, name: str, description: str | None = None) -> None:
        self.name = name
        self.description = description

    def __repr__(self) -> str:
        return f"InjectionToken({self.name!r})"


def injectable(_cls: type | None = None, *, scope: Scope = SINGLETON) -> t.Any:
    """Mark a class as constructible by the container.

    The mark is not strictly required -- the container will happily build any
    class whose constructor it can satisfy -- but declaring it makes the intent
    explicit and lets the container reject accidental injection of, say, a DTO.
    """
    if scope not in _SCOPES:
        raise ConfigurationError(f"scope must be one of {sorted(_SCOPES)}, got {scope!r}")

    def wrap(cls: type) -> type:
        setattr(cls, _INJECTABLE_ATTR, True)
        setattr(cls, _SCOPE_ATTR, scope)
        return cls

    return wrap if _cls is None else wrap(_cls)


def is_injectable(obj: t.Any) -> bool:
    return bool(getattr(obj, _INJECTABLE_ATTR, False))


def provider_scope(obj: t.Any) -> Scope:
    return getattr(obj, _SCOPE_ATTR, SINGLETON)


@dataclass
class Provider:
    """Describes how the container should produce a value for ``token``.

    Exactly one of ``use_class``, ``use_value`` or ``use_factory`` applies; if
    none is given and the token is a class, the token builds itself.
    """

    token: t.Any
    use_class: type | None = None
    use_value: t.Any = None
    use_factory: t.Callable[..., t.Any] | None = None
    #: Tokens passed positionally to ``use_factory``.
    inject: tuple[t.Any, ...] = ()
    scope: Scope | None = None
    _has_value: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        supplied = [self.use_class is not None, self.use_factory is not None, self._has_value]
        if sum(supplied) > 1:
            raise ConfigurationError(
                f"Provider for {self.token!r} sets more than one of use_class/use_value/use_factory"
            )
        if self.scope is not None and self.scope not in _SCOPES:
            raise ConfigurationError(f"Unknown scope {self.scope!r} for {self.token!r}")

    @classmethod
    def value(cls, token: t.Any, value: t.Any) -> Provider:
        """Register a ready-made object (config, client, constant)."""
        return cls(token=token, use_value=value, _has_value=True, scope=SINGLETON)

    @classmethod
    def factory(
        cls,
        token: t.Any,
        fn: t.Callable[..., t.Any],
        *,
        inject: t.Sequence[t.Any] = (),
        scope: Scope = SINGLETON,
    ) -> Provider:
        """Register a callable (sync, async, or generator) as the producer."""
        return cls(token=token, use_factory=fn, inject=tuple(inject), scope=scope)

    @classmethod
    def klass(cls, token: t.Any, implementation: type, *, scope: Scope | None = None) -> Provider:
        """Bind an interface token to a concrete implementation class."""
        return cls(token=token, use_class=implementation, scope=scope)

    def effective_scope(self) -> Scope:
        if self.scope is not None:
            return self.scope
        target = self.use_class or (self.token if inspect.isclass(self.token) else None)
        return provider_scope(target) if target is not None else SINGLETON


def token_name(token: t.Any) -> str:
    return getattr(token, "name", None) or getattr(token, "__name__", None) or repr(token)


class Container:
    """Resolves tokens to instances, honouring scope and dependency order.

    Containers form a tree: the application owns the root, each module owns a
    child that can see only what its imports export, and every request gets a
    short-lived child holding request-scoped instances.
    """

    def __init__(self, parent: Container | None = None, name: str = "root") -> None:
        self.name = name
        self.parent = parent
        self._providers: dict[t.Any, Provider] = {}
        self._singletons: dict[t.Any, t.Any] = {}
        self._request_cache: dict[t.Any, t.Any] = {}
        self._teardowns: list[t.Any] = []
        #: Arbitrary cleanup callables run when the scope closes -- closing
        #: uploaded temp files, releasing a lock, returning a lease.
        self._closers: list[t.Callable[[], t.Any]] = []
        self._resolving: list[t.Any] = []

    # ---------------------------------------------------------- registration

    def register(self, provider: Provider | type | t.Any) -> Container:
        """Register a provider.  Bare classes are wrapped automatically."""
        if not isinstance(provider, Provider):
            if not inspect.isclass(provider):
                raise ConfigurationError(
                    f"Cannot register {provider!r}: pass a class or a Provider instance"
                )
            provider = Provider(token=provider, use_class=provider)
        self._providers[provider.token] = provider
        return self

    def register_all(self, providers: t.Iterable[t.Any]) -> Container:
        for provider in providers:
            self.register(provider)
        return self

    def has(self, token: t.Any) -> bool:
        if token in self._providers:
            return True
        return self.parent.has(token) if self.parent else False

    def _find(self, token: t.Any) -> tuple[Provider, Container] | None:
        if token in self._providers:
            return self._providers[token], self
        if self.parent is not None:
            return self.parent._find(token)
        return None

    # -------------------------------------------------------------- scoping

    def create_child(self, name: str = "child") -> Container:
        """Create a child container that can see this one's providers."""
        return Container(parent=self, name=name)

    def create_request_scope(self) -> Container:
        """Create the per-request container.  Cheap: no providers are copied."""
        return Container(parent=self, name=f"{self.name}:request")

    # ------------------------------------------------------------ resolution

    async def resolve(self, token: t.Any, *, _chain: tuple[t.Any, ...] = ()) -> t.Any:
        """Return an instance for ``token``, building dependencies as needed."""
        if token in _chain:
            cycle = " -> ".join(token_name(x) for x in (*_chain, token))
            raise ConfigurationError(f"Circular dependency detected: {cycle}")

        found = self._find(token)
        if found is None:
            if inspect.isclass(token):
                # Implicit self-binding keeps trivial cases boilerplate-free.
                found = (Provider(token=token, use_class=token), self)
            else:
                raise ConfigurationError(
                    f"No provider registered for {token_name(token)}. Add it to a "
                    "module's providers= list, or register it with app.container."
                )

        provider, owner = found
        scope = provider.effective_scope()

        if scope == SINGLETON and token in owner._singletons:
            return owner._singletons[token]
        if scope == REQUEST and token in self._request_cache:
            return self._request_cache[token]

        instance = await self._construct(provider, owner, (*_chain, token))

        if scope == SINGLETON:
            owner._singletons[token] = instance
        elif scope == REQUEST:
            self._request_cache[token] = instance
        return instance

    async def _construct(
        self, provider: Provider, owner: Container, chain: tuple[t.Any, ...]
    ) -> t.Any:
        if provider._has_value or (
            provider.use_class is None
            and provider.use_factory is None
            and provider.use_value is not None
        ):
            return provider.use_value

        if provider.use_factory is not None:
            if provider.inject:
                # Explicit tokens are passed positionally, in declared order.
                args = [await self.resolve(dep, _chain=chain) for dep in provider.inject]
                result = provider.use_factory(*args)
            else:
                # Otherwise the factory's own annotations name what it needs.
                kwargs = await self._resolve_callable_args(provider.use_factory, chain)
                result = provider.use_factory(**kwargs)
            return await self._finish(result)

        target = provider.use_class or provider.token
        if not inspect.isclass(target):
            raise ConfigurationError(
                f"Provider for {token_name(provider.token)} has no way to build a value"
            )
        kwargs = await self._resolve_callable_args(target.__init__, chain, skip_self=True)
        return target(**kwargs)

    async def _finish(self, result: t.Any) -> t.Any:
        """Await coroutines and drive generator factories to their first yield."""
        if inspect.isasyncgen(result):
            value = await result.__anext__()
            self._teardowns.append(result)
            return value
        if inspect.isgenerator(result):
            value = next(result)
            self._teardowns.append(result)
            return value
        return await maybe_await(result)

    async def _resolve_callable_args(
        self, func: t.Callable[..., t.Any], chain: tuple[t.Any, ...], *, skip_self: bool = False
    ) -> dict[str, t.Any]:
        from .params import Inject

        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):  # builtins such as object.__init__
            return {}
        try:
            hints = t.get_type_hints(func)
        except Exception:
            hints = {}

        kwargs: dict[str, t.Any] = {}
        for index, (name, parameter) in enumerate(signature.parameters.items()):
            if skip_self and index == 0 and name in ("self", "cls"):
                continue
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                continue

            token: t.Any = None
            if isinstance(parameter.default, Inject):
                token = parameter.default.token or hints.get(name)
            elif name in hints:
                token = hints[name]

            if token is None or token is inspect.Parameter.empty:
                if parameter.default is inspect.Parameter.empty:
                    raise ConfigurationError(
                        f"Cannot inject parameter {name!r} of {getattr(func, '__qualname__', func)!r}: "
                        "no type annotation and no default."
                    )
                continue

            if not self.has(token) and not (inspect.isclass(token) and is_injectable(token)):
                if parameter.default is not inspect.Parameter.empty and not isinstance(
                    parameter.default, Inject
                ):
                    continue  # a plain default is fine; do not force injection
            kwargs[name] = await self.resolve(token, _chain=chain)
        return kwargs

    # -------------------------------------------------------------- lifecycle

    async def startup(self) -> None:
        """Eagerly build every singleton and run ``on_module_init`` hooks."""
        for token, provider in list(self._providers.items()):
            if provider.effective_scope() == SINGLETON:
                instance = await self.resolve(token)
                hook = getattr(instance, "on_module_init", None)
                if hook is not None:
                    await maybe_await(hook())

    async def shutdown(self) -> None:
        """Run ``on_module_destroy`` hooks and finish generator providers."""
        for instance in list(self._singletons.values()):
            hook = getattr(instance, "on_module_destroy", None)
            if hook is not None:
                await maybe_await(hook())
        await self.close()
        self._singletons.clear()

    def on_close(self, callback: t.Callable[[], t.Any]) -> None:
        """Register a callable to run when this scope closes.

        Used for resources that are not providers -- the classic case being the
        temporary files behind a multipart upload, which must be closed once
        the response is built or the process leaks file descriptors.
        """
        self._closers.append(callback)

    async def close(self) -> None:
        """Drain teardown for generator-based providers, newest first."""
        while self._closers:
            callback = self._closers.pop()
            try:
                await maybe_await(callback())
            except Exception:
                _logger.warning("scope_cleanup_failed", exc_info=True)
        while self._teardowns:
            generator = self._teardowns.pop()
            try:
                if inspect.isasyncgen(generator):
                    await generator.__anext__()
                else:
                    next(generator)
            except (StopIteration, StopAsyncIteration):
                # The expected end of a generator provider: it yielded once and
                # its teardown has now run.
                continue
        self._request_cache.clear()

    def __repr__(self) -> str:
        return f"<Container {self.name} providers={len(self._providers)}>"
