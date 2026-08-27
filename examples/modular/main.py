"""NestJS-shaped SlowAPI: modules, controllers, DI, guards, interceptors.

This is the same service as ``rest-api/`` reorganised for a team. The routes
are identical; what changes is that each feature owns its providers, its
authorisation policy, and its boundary.

    python -m slowapi run main:app
"""

from __future__ import annotations

import itertools
import typing as t
from dataclasses import dataclass, field

from slowapi import (
    Delete,
    ExecutionContext,
    Get,
    Inject,
    InjectionToken,
    NotFound,
    Post,
    Provider,
    Query,
    SlowAPI,
    Unauthorized,
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
)
from slowapi.interceptors import Interceptor, TimingInterceptor
from slowapi.middleware import AccessLogMiddleware, SecurityHeadersMiddleware

# Tokens let a module publish a value without exporting a class.
SETTINGS = InjectionToken("SETTINGS", "Runtime configuration")


# ============================================================ shared module


@dataclass
class Settings:
    environment: str = "development"
    page_size: int = 20


@injectable()
class Auditor:
    """A singleton: one instance for the process lifetime."""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def record(self, action: str) -> None:
        self.entries.append(action)

    def on_module_init(self) -> None:
        self.record("auditor:started")

    def on_module_destroy(self) -> None:
        self.record("auditor:stopped")


@module(providers=[Auditor, Provider.value(SETTINGS, Settings())], exports=[Auditor])
class CoreModule:
    """Cross-cutting services every feature may import."""


# ============================================================== users module


@dataclass
class User:
    id: int
    name: str
    email: str = field(metadata=expose(groups=("admin",)))
    password_hash: str = field(default="", metadata=hidden())
    role: str = "member"


@injectable()
class UserService:
    def __init__(self, auditor: Auditor, settings: Settings = Inject(SETTINGS)) -> None:
        self.auditor = auditor
        self.settings = settings
        self._ids = itertools.count(1)
        self._rows: dict[int, User] = {}
        for name, email, role in [
            ("Ada", "ada@example.com", "admin"),
            ("Grace", "grace@example.com", "member"),
        ]:
            self.create(name, email, role)

    def create(self, name: str, email: str, role: str = "member") -> User:
        user = User(id=next(self._ids), name=name, email=email, role=role)
        self._rows[user.id] = user
        self.auditor.record(f"user:create:{user.id}")
        return user

    def all(self, limit: int) -> list[User]:
        return list(self._rows.values())[:limit]

    def find(self, user_id: int) -> User:
        try:
            return self._rows[user_id]
        except KeyError:
            raise NotFound(f"No user with id {user_id}") from None

    def delete(self, user_id: int) -> None:
        self.find(user_id)
        del self._rows[user_id]
        self.auditor.record(f"user:delete:{user_id}")


@injectable()
class RoleGuard:
    """Guards are injectable, so policy can consult real services.

    ``@public`` and ``@roles(...)`` only record metadata; this class decides
    what that metadata means. One vocabulary, many possible policies.
    """

    def __init__(self, users: UserService) -> None:
        self.users = users

    def can_activate(self, ctx: ExecutionContext) -> bool:
        if ctx.get("public"):
            return True
        required = ctx.get("roles", [])
        if not required:
            return True
        # A real app would decode a JWT here.
        token = ctx.request.get("authorization", "")
        if not token.startswith("Bearer "):
            raise Unauthorized("Send an Authorization: Bearer <role> header")
        return token.removeprefix("Bearer ").strip() in required


class AuditInterceptor(Interceptor):
    """Interceptors see the returned object, not just bytes."""

    def __init__(self, auditor: Auditor) -> None:
        self.auditor = auditor

    async def intercept(self, ctx: ExecutionContext, call_next: t.Any) -> t.Any:
        result = await call_next()
        self.auditor.record(f"{ctx.request.method} {ctx.request.path}")
        return result


@controller("/users", tags=["users"])
@use_guards(RoleGuard)
@use_interceptors(TimingInterceptor())
class UserController:
    def __init__(self, users: UserService) -> None:
        self.users = users

    @Get("")
    @public
    def index(self, limit: int = Query(20, ge=1, le=100)) -> list[User]:
        """Public listing. Emails are group-gated, so they are omitted."""
        return self.users.all(limit)

    @Get("/directory")
    @roles("admin")
    @serialize_with(groups=("admin",))
    def directory(self) -> list[User]:
        """Admin listing. Same objects, more fields, one decorator apart."""
        return self.users.all(100)

    @Get("/:id")
    @public
    def show(self, id: int) -> User:
        return self.users.find(id)

    @Post("")
    @roles("admin")
    @http_code(201)
    def create(self, name: str, email: str) -> User:
        return self.users.create(name, email)

    @Delete("/:id")
    @roles("admin")
    def remove(self, id: int, res) -> None:
        self.users.delete(id)
        res.status(204).end()


@module(
    imports=[CoreModule],
    controllers=[UserController],
    providers=[UserService, RoleGuard],
    exports=[UserService],
)
class UserModule:
    """Everything about users, and nothing else."""


# ============================================================== application

app = SlowAPI(
    title="Modular API",
    version="1.0.0",
    description="Modules, controllers, guards and interceptors.",
    modules=[CoreModule, UserModule],
)
app.use(SecurityHeadersMiddleware(), AccessLogMiddleware())


@app.get("/audit", tags=["ops"])
def audit_log(auditor: Auditor) -> dict:
    """A function handler can inject module providers by annotation alone."""
    return {"entries": auditor.entries}


if __name__ == "__main__":
    app.run(port=8000)
