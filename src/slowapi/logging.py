"""Structured logging that a log aggregator can actually parse.

Human-readable output in development, one JSON object per line in production.
The choice is made from :class:`~slowapi.config.Settings`, so it is one
environment variable rather than a code change.
"""

from __future__ import annotations

import json
import logging
import sys
import typing as t
from datetime import datetime, timezone

__all__ = ["ConsoleFormatter", "JSONFormatter", "configure_logging", "get_logger"]

#: Attributes :class:`logging.LogRecord` always carries; anything else on the
#: record came from ``extra=`` and belongs in the structured output.
_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "asctime",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    """Render each record as a single-line JSON object."""

    def __init__(self, *, service: str | None = None, version: str | None = None) -> None:
        super().__init__()
        self.service = service
        self.version = version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, t.Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        if self.service:
            payload["service"] = self.service
        if self.version:
            payload["version"] = self.version
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Compact, colourised output for a terminal."""

    COLOURS: t.ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"
    DIM = "\033[2m"

    def __init__(self, *, colour: bool | None = None) -> None:
        super().__init__()
        self.colour = sys.stderr.isatty() if colour is None else colour

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname.ljust(8)
        if self.colour:
            level = f"{self.COLOURS.get(record.levelname, '')}{level}{self.RESET}"
            stamp = f"{self.DIM}{stamp}{self.RESET}"

        extras = {
            k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")
        }
        suffix = " ".join(f"{k}={v}" for k, v in extras.items())
        if suffix and self.colour:
            suffix = f"{self.DIM}{suffix}{self.RESET}"

        line = f"{stamp} {level} {record.getMessage()}"
        if suffix:
            line += f"  {suffix}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(
    level: str = "INFO",
    *,
    json_output: bool = False,
    service: str | None = None,
    version: str | None = None,
    stream: t.TextIO | None = None,
) -> None:
    """Install a single handler on the ``slowapi`` logger tree.

    Idempotent: calling it twice replaces the handler instead of duplicating
    output, which matters under auto-reload.
    """
    logger = logging.getLogger("slowapi")
    logger.setLevel(level.upper())
    for existing in list(logger.handlers):
        logger.removeHandler(existing)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        JSONFormatter(service=service, version=version) if json_output else ConsoleFormatter()
    )
    logger.addHandler(handler)
    # The app owns its output; do not double-log through the root logger.
    logger.propagate = False


def get_logger(name: str = "slowapi") -> logging.Logger:
    """Return a namespaced logger, configuring a default handler if needed."""
    logger = logging.getLogger(name)
    root = logging.getLogger("slowapi")
    if not root.handlers:
        configure_logging()
    return logger
