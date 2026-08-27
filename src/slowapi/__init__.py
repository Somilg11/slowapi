"""SlowAPI — one handler, two protocols.

SlowAPI is a Python web framework that borrows deliberately:

* **Express** gives it the handler and middleware ergonomics -- ``(req, res)``,
  ``res.status(201).json(...)``, ``(req, res, next)`` onion middleware.
* **FastAPI** gives it type-driven injection, validation, and an OpenAPI
  document generated from the code that actually runs.
* **NestJS** gives it structure at scale -- modules, controllers, a real DI
  container, guards, interceptors, pipes, and declarative serialisation.

What it adds that none of them have: the *same application object* is both a
WSGI application and an ASGI application, and the *same handler* runs under
either, whether it is ``def`` or ``async def``.  Deployment stops being a
framework decision.

::

    from slowapi import SlowAPI

    app = SlowAPI()

    @app.get("/users/{id}")
    def get_user(id: int, res):
        res.json({"id": id})

    # gunicorn app:app     -> WSGI, no event loop involved
    # uvicorn  app:app     -> ASGI, same code

See ``docs/`` for the guide and ``docs/growth.md`` for where this is going.
"""

from __future__ import annotations

from ._version import VERSION, __version__
from .app import SlowAPI
from .background import BackgroundTasks
from .config import Settings, from_env, load_dotenv
from .datastructures import URL, Headers, QueryParams, State, UploadFile
from .decorators import (
    Delete,
    Get,
    Head,
    Options,
    Patch,
    Post,
    Put,
    controller,
    http_code,
    module,
    never_suspends,
    route,
    set_metadata,
    version,
)
from .di import Container, InjectionToken, Provider, injectable
from .exceptions import (
    BadRequest,
    ConfigurationError,
    Forbidden,
    HTTPException,
    MethodNotAllowed,
    NotFound,
    PayloadTooLarge,
    SlowAPIError,
    TooManyRequests,
    Unauthorized,
    UnsupportedMediaType,
    ValidationError,
)
from .execution import ExecutionContext
from .guards import public, roles, use_guards
from .health import HealthCheck
from .interceptors import Interceptor, use_interceptors
from .params import (
    Body,
    Cookie,
    Ctx,
    Depends,
    File,
    Form,
    Header,
    Inject,
    Path,
    Query,
    Req,
    Res,
)
from .pipes import Pipe, use_pipes
from .request import Request
from .response import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    NoContentResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from .routing import Router
from .serialization import expose, hidden, serialize, serialize_with
from .static import StaticFiles

__all__ = [
    "URL",
    "VERSION",
    "BackgroundTasks",
    "BadRequest",
    "Body",
    "ConfigurationError",
    "Container",
    "Cookie",
    "Ctx",
    "Delete",
    "Depends",
    "ExecutionContext",
    "File",
    "FileResponse",
    "Forbidden",
    "Form",
    "Get",
    "HTMLResponse",
    "HTTPException",
    "Head",
    "Header",
    "Headers",
    "HealthCheck",
    "Inject",
    "InjectionToken",
    "Interceptor",
    "JSONResponse",
    "MethodNotAllowed",
    "NoContentResponse",
    "NotFound",
    "Options",
    "Patch",
    "Path",
    "PayloadTooLarge",
    "Pipe",
    "PlainTextResponse",
    "Post",
    "Provider",
    "Put",
    "Query",
    "QueryParams",
    "RedirectResponse",
    "Req",
    "Request",
    "Res",
    "Response",
    "Router",
    "Settings",
    "SlowAPI",
    "SlowAPIError",
    "State",
    "StaticFiles",
    "StreamingResponse",
    "TooManyRequests",
    "Unauthorized",
    "UnsupportedMediaType",
    "UploadFile",
    "ValidationError",
    "__version__",
    "controller",
    "expose",
    "from_env",
    "hidden",
    "http_code",
    "injectable",
    "load_dotenv",
    "module",
    "never_suspends",
    "public",
    "roles",
    "route",
    "serialize",
    "serialize_with",
    "set_metadata",
    "use_guards",
    "use_interceptors",
    "use_pipes",
    "version",
]
