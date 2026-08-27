"""Dependency-free parsers for ``multipart/form-data`` and urlencoded bodies.

Written against RFC 7578 with the pragmatic tolerances every real parser needs
(bare ``LF`` line endings, missing final boundary, quoted filenames containing
semicolons).  Files larger than ``spool_max_size`` spill to disk rather than
being held in memory.
"""

from __future__ import annotations

import re
import tempfile
import typing as t
from urllib.parse import parse_qsl

from .datastructures import FormData, Headers, UploadFile
from .exceptions import BadRequest

__all__ = ["parse_multipart", "parse_options_header", "parse_urlencoded"]

_OPTION_RE = re.compile(r';\s*(?P<key>[^\s;=]+)\s*=\s*(?:"(?P<quoted>[^"]*)"|(?P<bare>[^;]*))')


def parse_options_header(value: str | None) -> tuple[str, dict[str, str]]:
    """Split ``text/html; charset=utf-8`` into ``("text/html", {...})``."""
    if not value:
        return "", {}
    head, _, rest = value.partition(";")
    options: dict[str, str] = {}
    for match in _OPTION_RE.finditer(";" + rest):
        raw = match.group("quoted")
        if raw is None:
            raw = (match.group("bare") or "").strip()
        options[match.group("key").lower()] = raw
    return head.strip().lower(), options


def parse_urlencoded(body: bytes, charset: str = "utf-8") -> FormData:
    """Parse an ``application/x-www-form-urlencoded`` body."""
    try:
        text = body.decode(charset)
    except UnicodeDecodeError as exc:
        raise BadRequest(f"Form body is not valid {charset}") from exc
    return FormData(parse_qsl(text, keep_blank_values=True))


def _split_headers(block: bytes) -> Headers:
    items: list[tuple[bytes, bytes]] = []
    for line in block.split(b"\r\n"):
        if not line or b":" not in line:
            continue
        name, _, value = line.partition(b":")
        items.append((name.strip(), value.strip()))
    return Headers(items)


def parse_multipart(
    body: bytes,
    boundary: str,
    *,
    charset: str = "utf-8",
    spool_max_size: int = 1024 * 1024,
    max_parts: int = 1000,
) -> FormData:
    """Parse a buffered ``multipart/form-data`` body into a :class:`FormData`.

    :param spool_max_size:
        Per-file byte threshold above which the payload is written to a
        temporary file instead of being kept in memory.
    :param max_parts:
        Hard cap on the number of parts, guarding against a body crafted to
        allocate an unbounded number of objects.
    """
    if not boundary:
        raise BadRequest("multipart/form-data body is missing its boundary")

    marker = b"--" + boundary.encode("latin-1")
    # Normalise bare LF to CRLF so lenient clients still parse.
    body = re.sub(rb"(?<!\r)\n", b"\r\n", body)

    segments = body.split(marker)
    if len(segments) < 2:
        raise BadRequest("Malformed multipart body: boundary not found")

    items: list[tuple[str, t.Any]] = []
    for segment in segments[1:]:
        if segment.startswith(b"--"):  # closing boundary
            break
        segment = segment.lstrip(b"\r\n")
        head, sep, payload = segment.partition(b"\r\n\r\n")
        if not sep:
            continue
        payload = payload[:-2] if payload.endswith(b"\r\n") else payload

        headers = _split_headers(head)
        _, options = parse_options_header(headers.get("content-disposition"))
        name = options.get("name")
        if name is None:
            continue

        filename = options.get("filename")
        if filename is None:
            try:
                items.append((name, payload.decode(charset)))
            except UnicodeDecodeError as exc:
                raise BadRequest(f"Field {name!r} is not valid {charset}") from exc
        else:
            spooled = tempfile.SpooledTemporaryFile(max_size=spool_max_size)
            spooled.write(payload)
            spooled.seek(0)
            items.append(
                (
                    name,
                    UploadFile(
                        spooled,  # type: ignore[arg-type]
                        filename=filename,
                        content_type=headers.get("content-type", "application/octet-stream"),
                        headers=headers,
                        size=len(payload),
                    ),
                )
            )
        if len(items) > max_parts:
            raise BadRequest(f"Multipart body exceeds {max_parts} parts")

    return FormData(items)
