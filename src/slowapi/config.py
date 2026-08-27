"""Typed settings read from the environment, with a ``.env`` loader.

Twelve-factor configuration without a dependency: declare a dataclass, and
values are pulled from the environment, coerced to the declared type, and
validated once at startup rather than the first time a request touches them.
"""

from __future__ import annotations

import dataclasses
import os
import typing as t

from .exceptions import ConfigurationError
from .validation import FieldError, coerce

__all__ = ["Settings", "from_env", "load_dotenv"]


def load_dotenv(path: str = ".env", *, override: bool = False) -> dict[str, str]:
    """Load ``KEY=value`` pairs from a file into :data:`os.environ`.

    Real environment variables win by default, so a deployed container is never
    overridden by a stray ``.env`` copied into the image.
    """
    loaded: dict[str, str] = {}
    if not os.path.isfile(path):
        return loaded
    with open(path, encoding="utf-8") as fp:
        for raw in fp:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip("\"'")
            loaded[key] = value
            if override or key not in os.environ:
                os.environ[key] = value
    return loaded


def _is_sequence(annotation: t.Any) -> bool:
    """True for ``list[...]``, ``set[...]``, ``tuple[...]`` and their bare forms."""
    origin = t.get_origin(annotation) or annotation
    return origin in (list, set, tuple, frozenset)


T = t.TypeVar("T")


def from_env(model: type[T], *, prefix: str = "", environ: t.Mapping[str, str] | None = None) -> T:
    """Build a settings dataclass from environment variables.

    Field ``database_url`` reads ``PREFIX_DATABASE_URL``.  Missing fields fall
    back to their declared default; missing fields without a default raise.
    """
    if not dataclasses.is_dataclass(model):
        raise ConfigurationError(f"{model!r} must be a dataclass")

    source = os.environ if environ is None else environ
    hints = t.get_type_hints(model)
    kwargs: dict[str, t.Any] = {}
    problems: list[str] = []

    for field in dataclasses.fields(model):
        key = (prefix + field.name).upper()
        if key not in source:
            continue
        annotation = hints.get(field.name, str)
        raw: t.Any = source[key]
        if _is_sequence(annotation):
            # An environment variable is one string, so a list has to be spelled
            # ``A,B,C``.  Without this, ``CORS_ORIGINS=a.com,b.com`` becomes the
            # single nonsense origin "a.com,b.com" and every real request is
            # rejected -- quietly, and only in the environment that has it set.
            raw = [part.strip() for part in raw.split(",") if part.strip()]
        try:
            kwargs[field.name] = coerce(raw, annotation, (key,))
        except FieldError as exc:
            problems.append(f"{key}: {exc.message}")

    if problems:
        raise ConfigurationError("Invalid environment configuration:\n  " + "\n  ".join(problems))
    try:
        return model(**kwargs)
    except TypeError as exc:
        raise ConfigurationError(
            f"Missing required configuration for {model.__name__}: {exc}"
        ) from exc


@dataclasses.dataclass
class Settings:
    """Framework-level settings.  Extend it for your own application config."""

    #: Free-form name used in logs and the OpenAPI title.
    app_name: str = "slowapi-app"
    environment: str = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    #: Server to run under: ``auto``, ``uvicorn``, ``gunicorn``, or ``wsgiref``.
    server: str = "auto"
    workers: int = 1
    reload: bool = False
    log_level: str = "INFO"
    #: JSON logs off in development, on everywhere else.
    log_json: bool | None = None
    secret_key: str = ""
    max_body_size: int = 16 * 1024 * 1024
    #: Comma-separated in the environment, a list here.
    cors_origins: list[str] = dataclasses.field(default_factory=list)
    trusted_hosts: list[str] = dataclasses.field(default_factory=lambda: ["*"])
    docs_enabled: bool | None = None

    @classmethod
    def load(cls, *, prefix: str = "", dotenv: str | None = ".env") -> Settings:
        """Load settings, reading ``.env`` first when present."""
        if dotenv:
            load_dotenv(dotenv)
        return from_env(cls, prefix=prefix)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    @property
    def use_json_logs(self) -> bool:
        return self.is_production if self.log_json is None else self.log_json

    @property
    def show_docs(self) -> bool:
        return (not self.is_production) if self.docs_enabled is None else self.docs_enabled

    def require_secret(self) -> str:
        """Return the secret key, refusing to run without one in production."""
        if not self.secret_key:
            if self.is_production:
                raise ConfigurationError(
                    "SECRET_KEY must be set in production. Generate one with "
                    "`python -m slowapi secret`."
                )
            return "insecure-development-key-do-not-deploy"
        return self.secret_key
