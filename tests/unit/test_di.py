"""The dependency-injection container."""

from __future__ import annotations

import pytest

from slowfw.di import Container, InjectionToken, Provider, injectable
from slowfw.exceptions import ConfigurationError


def run(coro):
    """Drive a coroutine without pulling in an async test plugin."""
    from slowfw.concurrency import run_coroutine_sync, shutdown_loop_thread

    try:
        return run_coroutine_sync(coro)
    finally:
        shutdown_loop_thread()


@injectable()
class Engine:
    def __init__(self):
        self.started = 0

    def on_module_init(self):
        self.started += 1


@injectable()
class Car:
    def __init__(self, engine: Engine):
        self.engine = engine


# Deliberately mutually dependent, to prove the cycle detector reports rather
# than recursing until the stack runs out.
@injectable()
class Chicken:
    def __init__(self, egg: Egg):
        self.egg = egg


@injectable()
class Egg:
    def __init__(self, chicken: Chicken):
        self.chicken = chicken


class TestResolution:
    def test_a_class_is_built_with_its_dependencies(self):
        container = Container()
        container.register(Car).register(Engine)
        car = run(container.resolve(Car))
        assert isinstance(car.engine, Engine)

    def test_singletons_are_shared(self):
        container = Container().register(Engine)
        assert run(container.resolve(Engine)) is run(container.resolve(Engine))

    def test_transient_providers_are_rebuilt_each_time(self):
        container = Container().register(Provider(Engine, use_class=Engine, scope="transient"))
        assert run(container.resolve(Engine)) is not run(container.resolve(Engine))

    def test_value_providers_return_the_object_unchanged(self):
        token = InjectionToken("CONFIG")
        settings = {"a": 1}
        container = Container().register(Provider.value(token, settings))
        assert run(container.resolve(token)) is settings

    def test_factory_providers_receive_their_declared_tokens(self):
        token = InjectionToken("DSN")
        container = Container()
        container.register(Provider.value(token, "postgres://"))
        container.register(Provider.factory(str, lambda dsn: dsn.upper(), inject=[token]))
        assert run(container.resolve(str)) == "POSTGRES://"

    def test_an_interface_token_can_be_bound_to_an_implementation(self):
        class Store:
            pass

        class MemoryStore(Store):
            pass

        container = Container().register(Provider.klass(Store, MemoryStore))
        assert isinstance(run(container.resolve(Store)), MemoryStore)

    def test_an_unknown_non_class_token_names_the_fix(self):
        with pytest.raises(ConfigurationError, match="No provider registered"):
            run(Container().resolve(InjectionToken("MISSING")))

    def test_a_cycle_is_reported_with_the_path(self):
        container = Container().register(Chicken).register(Egg)
        with pytest.raises(ConfigurationError, match="Circular dependency"):
            run(container.resolve(Chicken))


class TestScopes:
    def test_request_scope_is_shared_within_one_request_only(self):
        container = Container().register(Provider(Engine, use_class=Engine, scope="request"))
        first = container.create_request_scope()
        second = container.create_request_scope()
        assert run(first.resolve(Engine)) is run(first.resolve(Engine))
        assert run(first.resolve(Engine)) is not run(second.resolve(Engine))

    def test_generator_providers_are_torn_down_when_the_scope_closes(self):
        events: list[str] = []
        token = InjectionToken("SESSION")

        def factory():
            events.append("open")
            yield "session"
            events.append("close")

        container = Container().register(Provider.factory(token, factory, scope="request"))
        scope = container.create_request_scope()

        async def use():
            assert await scope.resolve(token) == "session"
            await scope.close()

        run(use())
        assert events == ["open", "close"]


class TestLifecycle:
    def test_startup_builds_singletons_and_calls_the_init_hook(self):
        container = Container().register(Engine)
        run(container.startup())
        assert run(container.resolve(Engine)).started == 1
