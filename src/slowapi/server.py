"""Development server selection.

Picks the best server available rather than forcing a choice: uvicorn if it is
installed (ASGI, fastest), gunicorn next (WSGI, production-shaped), and the
standard library's ``wsgiref`` as a guaranteed floor so ``app.run()`` works in
a fresh virtualenv with nothing but SlowAPI installed.
"""

from __future__ import annotations

import importlib.util
import typing as t

from .logging import get_logger

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import SlowAPI

__all__ = ["detect_server", "run"]

logger = get_logger("slowapi.server")


def _installed(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def detect_server(preference: str = "auto") -> str:
    """Resolve ``auto`` to a concrete server name."""
    if preference != "auto":
        return preference
    if _installed("uvicorn"):
        return "uvicorn"
    if _installed("gunicorn"):
        return "gunicorn"
    return "wsgiref"


def run(
    app: SlowAPI,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    server: str = "auto",
    reload: bool = False,
    workers: int = 1,
    log_level: str = "info",
    app_path: str | None = None,
) -> None:
    """Start a server for ``app``.

    :param app_path:
        ``"module:attribute"``.  Required for ``--reload`` and multi-worker
        modes, because both re-import the application in a fresh process.
    """
    choice = detect_server(server)
    logger.info("serving", extra={"server": choice, "url": f"http://{host}:{port}"})

    if choice == "uvicorn":
        import uvicorn

        target: t.Any = app_path or app.asgi_app
        if (reload or workers > 1) and app_path is None:
            logger.warning(
                "reload and workers need an import string; pass app_path='module:app'. "
                "Continuing with a single in-process worker."
            )
            reload, workers = False, 1
        uvicorn.run(
            target, host=host, port=port, reload=reload, workers=workers, log_level=log_level
        )
        return

    if choice == "gunicorn":
        from gunicorn.app.base import BaseApplication

        class _Runner(BaseApplication):
            def load_config(self) -> None:
                self.cfg.set("bind", f"{host}:{port}")
                self.cfg.set("workers", workers)
                self.cfg.set("reload", reload)
                self.cfg.set("loglevel", log_level)

            def load(self) -> t.Any:
                return app.wsgi_app

        _Runner().run()
        return

    if choice == "wsgiref":
        from wsgiref.simple_server import make_server

        logger.warning(
            "wsgiref is single-threaded and for development only. "
            "Install uvicorn or gunicorn for anything else."
        )
        with make_server(host, port, app.wsgi_app) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                logger.info("stopped")
        return

    raise ValueError(f"Unknown server {choice!r}; expected uvicorn, gunicorn or wsgiref")
