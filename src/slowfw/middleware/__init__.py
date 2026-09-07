"""Built-in middleware.

Everything here follows the Express ``(req, res, next)`` contract via a
``dispatch`` method, so third-party middleware written for SlowAPI needs no
registration ceremony -- pass the instance and it works.
"""

from __future__ import annotations

from .base import AsyncExecutor, Executor, Middleware, SyncExecutor, adapt, build_chain
from .compression import GZipMiddleware
from .cors import CORSMiddleware
from .errors import ErrorMiddleware, ExceptionHandlers
from .logging import AccessLogMiddleware, RequestIDMiddleware
from .proxy import ProxyHeadersMiddleware
from .ratelimit import MemoryRateLimitStore, RateLimitMiddleware, RateLimitStore
from .security import SecurityHeadersMiddleware, TrustedHostMiddleware
from .session import Session, SessionMiddleware
from .timeout import TimeoutMiddleware

__all__ = [
    "AccessLogMiddleware",
    "AsyncExecutor",
    "CORSMiddleware",
    "ErrorMiddleware",
    "ExceptionHandlers",
    "Executor",
    "GZipMiddleware",
    "MemoryRateLimitStore",
    "Middleware",
    "ProxyHeadersMiddleware",
    "RateLimitMiddleware",
    "RateLimitStore",
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "Session",
    "SessionMiddleware",
    "SyncExecutor",
    "TimeoutMiddleware",
    "TrustedHostMiddleware",
    "adapt",
    "build_chain",
]
