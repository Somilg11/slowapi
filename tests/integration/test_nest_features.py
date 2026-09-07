"""Modules, controllers, DI, guards, interceptors, pipes, serialisation."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from slowfw import (
    ExecutionContext,
    Get,
    Inject,
    InjectionToken,
    Post,
    Provider,
    SlowAPI,
    controller,
    expose,
    hidden,
    http_code,
    injectable,
    module,
    public,
    roles,
    serialize_with,
    use_guards,
    use_interceptors,
    use_pipes,
)
from slowfw.exceptions import ConfigurationError
from slowfw.interceptors import EnvelopeInterceptor, Interceptor
from slowfw.pipes import ClampPipe, TrimPipe

SETTINGS = InjectionToken("SETTINGS")


@dataclass
class User:
    id: int
    email: str = field(metadata=expose(groups=("admin",)))
    password_hash: str = field(default="", metadata=hidden())
    joined: str = field(default="2024-01-01", metadata=expose(alias="joinedAt"))


@injectable()
class Clock:
    def now(self) -> str:
        return "fixed"


@injectable()
class UserService:
    def __init__(self, clock: Clock, settings: dict = Inject(SETTINGS)):
        self.clock = clock
        self.settings = settings
        self.rows = [User(1, "a@example.com", "hash1"), User(2, "b@example.com", "hash2")]

    def all(self) -> list[User]:
        return self.rows

    def find(self, user_id: int) -> User | None:
        return next((u for u in self.rows if u.id == user_id), None)


class RoleGuard:
    """Reads the metadata that ``@roles`` and ``@public`` record."""

    def can_activate(self, ctx: ExecutionContext) -> bool:
        if ctx.get("public"):
            return True
        required = ctx.get("roles", [])
        return not required or ctx.request.get("x-role") in required


class Marker(Interceptor):
    async def intercept(self, ctx, call_next):
        ctx.response.set("X-Marker", ctx.route.name)
        return await call_next()


@controller("/users", tags=["users"])
@use_guards(RoleGuard)
@use_interceptors(Marker())
class UserController:
    def __init__(self, service: UserService):
        self.service = service

    @Get("")
    @public
    def index(self) -> list[User]:
        return self.service.all()

    @Get("/admin")
    @roles("admin")
    @serialize_with(groups=("admin",))
    def admin_view(self) -> list[User]:
        return self.service.all()

    @Get("/:id")
    @public
    def show(self, id: int) -> User | None:
        return self.service.find(id)

    @Post("")
    @public
    @http_code(202)
    @use_pipes(TrimPipe())
    def create(self, email: str, limit: int = 5) -> dict:
        return {"email": email, "limit": limit, "clock": self.service.clock.now()}

    @Get("/settings/env")
    @public
    def env(self) -> dict:
        return self.service.settings


@module(
    controllers=[UserController],
    providers=[UserService, Clock, Provider.value(SETTINGS, {"env": "test"})],
    exports=[UserService],
)
class UserModule:
    pass


@pytest.fixture
def client(make_client):
    return make_client(SlowAPI(title="nest", modules=[UserModule], docs_url=None, redoc_url=None))


class TestControllersAndModules:
    def test_controller_prefix_and_method_paths(self, client):
        assert client.get("/users").status_code == 200
        assert client.get("/users/1").json()["id"] == 1

    def test_constructor_injection_reaches_nested_providers(self, client):
        assert client.post("/users", json={"email": "x@y.z"}).json()["clock"] == "fixed"

    def test_token_providers_deliver_plain_values(self, client):
        assert client.get("/users/settings/env").json() == {"env": "test"}

    def test_exporting_something_you_do_not_provide_is_refused(self):
        with pytest.raises(ConfigurationError, match="does not provide"):

            @module(providers=[], exports=[Clock])
            class Broken:
                pass

    def test_a_controller_without_routes_is_refused(self):
        @controller("/empty")
        class Empty:
            pass

        with pytest.raises(ConfigurationError, match="has no routes"):
            SlowAPI(controllers=[Empty], openapi_url=None)


class TestGuards:
    def test_public_routes_bypass_the_guard(self, client):
        assert client.get("/users").status_code == 200

    def test_a_route_requiring_a_role_is_blocked_without_it(self, client):
        assert client.get("/users/admin").status_code == 403

    def test_the_role_lets_it_through(self, client):
        assert client.get("/users/admin", headers={"x-role": "admin"}).status_code == 200


class TestInterceptors:
    def test_class_level_interceptors_run_for_every_route(self, client):
        assert client.get("/users").headers["x-marker"] == "UserController.index"

    def test_an_envelope_interceptor_wraps_the_payload(self, make_client):
        app = SlowAPI(openapi_url=None, docs_url=None, redoc_url=None)

        @app.get("/x")
        @use_interceptors(EnvelopeInterceptor())
        def x() -> dict:
            return {"a": 1}

        body = make_client(app).get("/x").json()
        assert body["data"] == {"a": 1}
        assert body["meta"]["path"] == "/x"


class TestPipes:
    def test_a_pipe_transforms_the_resolved_argument(self, client):
        assert client.post("/users", json={"email": "  spaced@x.io  "}).json()["email"] == (
            "spaced@x.io"
        )

    def test_status_code_decorator_is_honoured(self, client):
        assert client.post("/users", json={"email": "a@b.c"}).status_code == 202

    def test_clamp_pipe_narrows_instead_of_rejecting(self, make_client):
        app = SlowAPI(openapi_url=None, docs_url=None, redoc_url=None)

        @app.get("/page")
        @use_pipes(ClampPipe(minimum=1, maximum=50))
        def page(limit: int = 10) -> dict:
            return {"limit": limit}

        assert make_client(app).get("/page", params={"limit": 9999}).json()["limit"] == 50


class TestSerialisation:
    def test_hidden_fields_never_leave_the_process(self, client):
        payload = client.get("/users").json()
        assert "password_hash" not in payload[0]

    def test_group_gated_fields_are_omitted_by_default(self, client):
        assert "email" not in client.get("/users").json()[0]

    def test_group_gated_fields_appear_for_an_entitled_caller(self, client):
        rows = client.get("/users/admin", headers={"x-role": "admin"}).json()
        assert rows[0]["email"] == "a@example.com"

    def test_aliases_rename_keys_on_the_way_out(self, client):
        assert "joinedAt" in client.get("/users").json()[0]
