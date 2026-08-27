"""``python -m slowapi`` -- run, inspect, and document an application.

Commands:

``run``       start a development or production server
``routes``    print the route table
``openapi``   dump the generated schema to stdout or a file
``secret``    generate a cryptographically strong ``SECRET_KEY``
``new``       scaffold a project that is ready to deploy
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import secrets
import sys
import typing as t

from ._version import __version__


def _load_app(target: str) -> t.Any:
    """Import ``module:attribute`` and return the application object."""
    if ":" not in target:
        target = f"{target}:app"
    module_name, _, attribute = target.partition(":")
    sys.path.insert(0, os.getcwd())
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise SystemExit(f"Cannot import {module_name!r}: {exc}") from None
    try:
        return getattr(module, attribute)
    except AttributeError:
        raise SystemExit(f"{module_name!r} has no attribute {attribute!r}") from None


def _cmd_run(args: argparse.Namespace) -> int:
    from .logging import configure_logging
    from .server import run

    configure_logging(args.log_level.upper(), json_output=args.json_logs)
    app = _load_app(args.app)
    run(
        app,
        host=args.host,
        port=args.port,
        server=args.server,
        reload=args.reload,
        workers=args.workers,
        log_level=args.log_level,
        app_path=args.app if ":" in args.app else f"{args.app}:app",
    )
    return 0


def _cmd_routes(args: argparse.Namespace) -> int:
    app = _load_app(args.app)
    routes = sorted(app.routes, key=lambda r: (r.path, r.method))
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "method": r.method,
                        "path": r.path,
                        "name": r.name,
                        "tags": list(r.tags),
                        "deprecated": r.deprecated,
                    }
                    for r in routes
                ],
                indent=2,
            )
        )
        return 0

    width = max((len(r.path) for r in routes), default=4)
    print(f"{'METHOD':<8} {'PATH':<{width}}  NAME")
    print("-" * (10 + width + 20))
    for route in routes:
        flag = "  (deprecated)" if route.deprecated else ""
        print(f"{route.method:<8} {route.path:<{width}}  {route.name}{flag}")
    print(f"\n{len(routes)} route(s)")
    return 0


def _cmd_openapi(args: argparse.Namespace) -> int:
    app = _load_app(args.app)
    document = json.dumps(app.openapi(), indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fp:
            fp.write(document + "\n")
        print(f"wrote {args.output}")
    else:
        print(document)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """Analyse every route without starting a server.

    Belongs in CI next to the linter: it catches an unresolvable dependency or
    a contradictory parameter marker at build time, where the cost is a red
    check, rather than at deploy time, where the cost is a rollback.
    """
    from .exceptions import ConfigurationError

    app = _load_app(args.app)
    try:
        app.check()
    except ConfigurationError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    print(f"OK    {len(app.router.routes)} route(s) validated")
    return 0


def _cmd_secret(args: argparse.Namespace) -> int:
    print(secrets.token_urlsafe(args.bytes))
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    from .scaffold import scaffold

    scaffold(args.name, template=args.template, force=args.force)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slowapi", description=__doc__)
    parser.add_argument("--version", action="version", version=f"slowapi {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="start a server")
    run_parser.add_argument("app", help="module:attribute, e.g. main:app")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=8000)
    run_parser.add_argument(
        "--server", default="auto", choices=["auto", "uvicorn", "gunicorn", "wsgiref"]
    )
    run_parser.add_argument("--reload", action="store_true")
    run_parser.add_argument("--workers", type=int, default=1)
    run_parser.add_argument("--log-level", default="info")
    run_parser.add_argument("--json-logs", action="store_true")
    run_parser.set_defaults(func=_cmd_run)

    routes_parser = sub.add_parser("routes", help="list the route table")
    routes_parser.add_argument("app")
    routes_parser.add_argument("--json", action="store_true")
    routes_parser.set_defaults(func=_cmd_routes)

    openapi_parser = sub.add_parser("openapi", help="print the OpenAPI document")
    openapi_parser.add_argument("app")
    openapi_parser.add_argument("-o", "--output")
    openapi_parser.set_defaults(func=_cmd_openapi)

    check_parser = sub.add_parser("check", help="validate every route without serving")
    check_parser.add_argument("app")
    check_parser.set_defaults(func=_cmd_check)

    secret_parser = sub.add_parser("secret", help="generate a SECRET_KEY")
    secret_parser.add_argument("--bytes", type=int, default=48)
    secret_parser.set_defaults(func=_cmd_secret)

    new_parser = sub.add_parser("new", help="scaffold a new project")
    new_parser.add_argument("name")
    new_parser.add_argument("--template", default="api", choices=["api", "modular", "fullstack"])
    new_parser.add_argument("--force", action="store_true")
    new_parser.set_defaults(func=_cmd_new)

    return parser


def main(argv: t.Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
