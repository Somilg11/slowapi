"""Outgoing responses.

Two styles are supported and can be mixed freely in the same application:

**Express style** -- mutate the injected ``res`` object and return nothing::

    @app.get("/hi")
    def hi(req, res):
        res.status(201).json({"ok": True})

**FastAPI style** -- return a value and let the framework serialise it::

    @app.get("/hi")
    def hi() -> dict:
        return {"ok": True}

Both end up as a :class:`Response`, which each adapter then writes out using
its native protocol.
"""

from __future__ import annotations

import hashlib
import http.cookies
import json
import mimetypes
import os
import stat
import typing as t
from datetime import datetime, timezone
from email.utils import format_datetime, formatdate
from http import HTTPStatus
from urllib.parse import quote

from .datastructures import MutableHeaders
from .exceptions import ConfigurationError

__all__ = [
    "FileResponse",
    "HTMLResponse",
    "JSONResponse",
    "NoContentResponse",
    "PlainTextResponse",
    "RedirectResponse",
    "Response",
    "StreamingResponse",
]

#: Statuses that must never carry a body, per RFC 9110.
BODYLESS_STATUSES = frozenset({100, 101, 102, 103, 204, 304})


def _default_json(value: t.Any) -> t.Any:
    """Serialise the types applications reach for most often."""
    import dataclasses
    import decimal
    import uuid
    from datetime import date, time
    from enum import Enum
    from pathlib import PurePath

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, PurePath)):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return list(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "model_dump"):  # pydantic v2
        return value.model_dump(mode="json")
    if hasattr(value, "dict") and callable(value.dict):  # pydantic v1
        return value.dict()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serialisable")


class Response:
    """A mutable HTTP response.

    Every mutator returns ``self`` so calls chain the way they do in Express.
    """

    media_type: str | None = None
    charset: str = "utf-8"

    def __init__(
        self,
        content: t.Any = None,
        status_code: int = 200,
        headers: t.Mapping[str, str] | None = None,
        media_type: str | None = None,
        *,
        background: t.Callable[[], t.Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.media_type = media_type or self.media_type
        self.headers = MutableHeaders(headers or {})
        #: Called after the response has been flushed to the client.
        self.background = background
        #: Template engine, injected by the application when configured.
        self._templates: t.Any = None
        self._body: bytes = b""
        self._stream: t.Any = None
        self._started = False
        #: ``(deferred, weak)`` -- see :meth:`etag`.
        self._auto_etag: tuple[bool, bool] = (False, False)
        if content is not None:
            self.body = self._render_bytes(content)
        self._finalise_headers()

    # --------------------------------------------------------------- payload

    @property
    def body(self) -> bytes:
        return self._body

    @body.setter
    def body(self, value: bytes) -> None:
        self._body = value
        self._stream = None

    def _render_bytes(self, content: t.Any) -> bytes:
        """Turn ``content`` into bytes.  Subclasses override this."""
        if content is None:
            return b""
        if isinstance(content, bytes):
            return content
        if isinstance(content, str):
            return content.encode(self.charset)
        return str(content).encode(self.charset)

    def _finalise_headers(self) -> None:
        if self.media_type and "content-type" not in self.headers:
            ctype = self.media_type
            if ctype.startswith("text/") or ctype in (
                "application/json",
                "application/javascript",
            ):
                ctype = f"{ctype}; charset={self.charset}"
            self.headers["content-type"] = ctype
        if self.status_code in BODYLESS_STATUSES:
            self._body = b""
            del self.headers["content-length"]
            del self.headers["content-type"]
        elif self._stream is None:
            # Recomputed every time, not set-once: `res.text(...)` after
            # construction must not leave a stale length behind.
            self.headers["content-length"] = str(len(self._body))

    # -------------------------------------------------------- Express-style

    def status(self, code: int) -> Response:
        """Set the status code.  ``res.status(404).send("nope")``."""
        self.status_code = int(code)
        return self

    def set(self, name: str, value: str) -> Response:
        """Set a response header (Express ``res.set``)."""
        self.headers[name] = value
        return self

    #: Express alias for :meth:`set`.
    header = set

    def append(self, name: str, value: str) -> Response:
        """Append a header without replacing existing values."""
        self.headers.add(name, value)
        return self

    def type(self, media_type: str) -> Response:
        """Set ``Content-Type``, expanding bare extensions like ``"html"``."""
        if "/" not in media_type:
            media_type = mimetypes.types_map.get(f".{media_type.lstrip('.')}", media_type)
        self.media_type = media_type
        del self.headers["content-type"]
        self._finalise_headers()
        return self

    def send(self, content: t.Any = "", status_code: int | None = None) -> Response:
        """Send ``content``, guessing a sensible content type.

        ``dict``/``list`` become JSON, ``str``/``bytes`` are sent as-is.  This
        mirrors Express's ``res.send`` overloading.
        """
        if status_code is not None:
            self.status_code = int(status_code)
        if isinstance(content, (dict, list, tuple)):
            return self.json(content)
        if isinstance(content, bytes):
            self.body = content
            self.media_type = self.media_type or "application/octet-stream"
        else:
            self.body = str(content).encode(self.charset)
            self.media_type = self.media_type or "text/html"
        self._finalise_headers()
        return self

    def json(self, data: t.Any, status_code: int | None = None) -> Response:
        """Serialise ``data`` as JSON and set the content type."""
        if status_code is not None:
            self.status_code = int(status_code)
        self.media_type = "application/json"
        del self.headers["content-type"]
        self.body = json.dumps(
            data, default=_default_json, ensure_ascii=False, separators=(",", ":")
        ).encode(self.charset)
        self._finalise_headers()
        return self

    def text(self, data: str, status_code: int | None = None) -> Response:
        """Send ``data`` as ``text/plain``."""
        if status_code is not None:
            self.status_code = int(status_code)
        self.media_type = "text/plain"
        del self.headers["content-type"]
        self.body = data.encode(self.charset)
        self._finalise_headers()
        return self

    def html(self, markup: str, status_code: int | None = None) -> Response:
        """Send ``markup`` as ``text/html``."""
        if status_code is not None:
            self.status_code = int(status_code)
        self.media_type = "text/html"
        del self.headers["content-type"]
        self.body = markup.encode(self.charset)
        self._finalise_headers()
        return self

    def redirect(self, location: str, status_code: int = 302) -> Response:
        """Issue a redirect.  Use 308 to preserve the method and body."""
        self.status_code = status_code
        self.headers["location"] = quote(str(location), safe=":/%#?=@[]!$&'()*+,;")
        self.body = b""
        self._finalise_headers()
        return self

    def end(self) -> Response:
        """Finish with no body (Express ``res.end()``)."""
        self.body = b""
        self._finalise_headers()
        return self

    def cookie(
        self,
        key: str,
        value: str = "",
        *,
        max_age: int | None = None,
        expires: datetime | int | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = True,
        samesite: str | None = "lax",
    ) -> Response:
        """Attach a ``Set-Cookie`` header.

        Defaults are the secure ones: ``HttpOnly`` on and ``SameSite=Lax``.
        """
        jar: http.cookies.BaseCookie = http.cookies.SimpleCookie()
        jar[key] = value
        morsel = jar[key]
        if max_age is not None:
            morsel["max-age"] = str(max_age)
        if expires is not None:
            morsel["expires"] = (
                format_datetime(expires, usegmt=True)
                if isinstance(expires, datetime)
                else formatdate(float(expires), usegmt=True)
            )
        if path:
            morsel["path"] = path
        if domain:
            morsel["domain"] = domain
        if secure:
            morsel["secure"] = True
        if httponly:
            morsel["httponly"] = True
        if samesite is not None:
            allowed = {"strict", "lax", "none"}
            if samesite.lower() not in allowed:
                raise ConfigurationError(f"samesite must be one of {sorted(allowed)}")
            morsel["samesite"] = samesite
            if samesite.lower() == "none" and not secure:
                raise ConfigurationError("SameSite=None requires secure=True")
        self.headers.add("set-cookie", morsel.OutputString())
        return self

    def clear_cookie(self, key: str, *, path: str = "/", domain: str | None = None) -> Response:
        """Expire a cookie immediately."""
        return self.cookie(key, "", max_age=0, path=path, domain=domain, expires=0)

    def vary(self, *headers: str) -> Response:
        """Add entries to the ``Vary`` header without duplicating them."""
        current = {
            part.strip().lower()
            for part in (self.headers.get("vary") or "").split(",")
            if part.strip()
        }
        current.update(h.lower() for h in headers)
        self.headers["vary"] = ", ".join(sorted(current))
        return self

    def cache(self, seconds: int, *, public: bool = True, immutable: bool = False) -> Response:
        """Set a ``Cache-Control`` policy in one call."""
        parts = ["public" if public else "private", f"max-age={int(seconds)}"]
        if immutable:
            parts.append("immutable")
        self.headers["cache-control"] = ", ".join(parts)
        return self

    def no_cache(self) -> Response:
        self.headers["cache-control"] = "no-store, no-cache, must-revalidate"
        return self

    def etag(self, value: str | None = None, *, weak: bool = False) -> Response:
        """Set an ``ETag``, computing it from the body when not supplied.

        A computed tag is deferred to send time rather than taken now, because
        the chain reads naturally in either order::

            res.etag().json(payload)
            res.json(payload).etag()

        Hashing eagerly would make the first line hash an empty body and emit a
        tag that matches every other empty response -- a cache poisoning bug
        that looks like a working ETag.
        """
        if value is None:
            self._auto_etag = (True, weak)
            return self
        self._auto_etag = (False, weak)
        self.headers["etag"] = f'{"W/" if weak else ""}"{value}"'
        return self

    def _compute_deferred_etag(self) -> None:
        """Fill in an ``ETag`` requested before the body existed."""
        deferred, weak = getattr(self, "_auto_etag", (False, False))
        if not deferred or self.is_streaming:
            return
        digest = hashlib.sha256(self._body).hexdigest()[:32]
        self.headers["etag"] = f'{"W/" if weak else ""}"{digest}"'
        self._auto_etag = (False, weak)

    def attachment(self, filename: str | None = None) -> Response:
        """Mark the response as a download."""
        if filename:
            safe = quote(filename)
            self.headers["content-disposition"] = (
                f"attachment; filename=\"{filename}\"; filename*=UTF-8''{safe}"
            )
        else:
            self.headers["content-disposition"] = "attachment"
        return self

    def render(
        self,
        template_name: str,
        context: t.Mapping[str, t.Any] | None = None,
        *,
        status_code: int | None = None,
    ) -> Response:
        """Render a template into this response.

        The engine is attached to the response by the application; without
        ``app.configure_templates(...)`` this raises a
        :class:`~slowfw.exceptions.ConfigurationError` explaining the fix.
        """
        engine = getattr(self, "_templates", None)
        if engine is None:
            raise ConfigurationError(
                "Templating is not configured. Pass template_dir=... to SlowAPI() "
                "or call app.configure_templates('templates/')."
            )
        markup = engine.render(template_name, dict(context or {}))
        return self.html(markup, status_code)

    # ------------------------------------------------------------ streaming

    @property
    def is_streaming(self) -> bool:
        return self._stream is not None

    def stream(
        self,
        source: t.Iterable[bytes] | t.AsyncIterable[bytes],
        *,
        media_type: str | None = None,
    ) -> Response:
        """Stream an (async) iterable of byte chunks to the client."""
        self._stream = source
        self._body = b""
        if media_type:
            self.media_type = media_type
            del self.headers["content-type"]
        del self.headers["content-length"]
        self._finalise_headers()
        return self

    def iter_chunks_sync(self) -> t.Iterator[bytes]:
        """Yield the outgoing body as bytes for a blocking transport."""
        if self._stream is None:
            if self._body:
                yield self._body
            return
        if hasattr(self._stream, "__aiter__"):
            from .concurrency import run_coroutine_sync

            agen = self._stream.__aiter__()

            async def _next() -> bytes | None:
                try:
                    return await agen.__anext__()
                except StopAsyncIteration:
                    return None

            while True:
                chunk = run_coroutine_sync(_next())
                if chunk is None:
                    return
                yield chunk if isinstance(chunk, bytes) else str(chunk).encode(self.charset)
        else:
            for chunk in self._stream:
                yield chunk if isinstance(chunk, bytes) else str(chunk).encode(self.charset)

    async def iter_chunks(self) -> t.AsyncIterator[bytes]:
        """Yield the outgoing body as bytes for an async transport."""
        if self._stream is None:
            if self._body:
                yield self._body
            return
        if hasattr(self._stream, "__aiter__"):
            async for chunk in self._stream:
                yield chunk if isinstance(chunk, bytes) else str(chunk).encode(self.charset)
        else:
            from .concurrency import iterate_in_threadpool

            async for chunk in iterate_in_threadpool(self._stream):
                yield chunk if isinstance(chunk, bytes) else str(chunk).encode(self.charset)

    # ----------------------------------------------------------------- misc

    @property
    def status_line(self) -> str:
        """``"200 OK"``, the form WSGI's ``start_response`` expects."""
        try:
            phrase = HTTPStatus(self.status_code).phrase
        except ValueError:
            phrase = "Unknown"
        return f"{self.status_code} {phrase}"

    def raw_headers(self) -> list[tuple[str, str]]:
        self._compute_deferred_etag()
        return self.headers.raw_items()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.status_code} {len(self._body)}b>"


class JSONResponse(Response):
    """A response whose body is JSON-encoded ``content``."""

    media_type = "application/json"

    def _render_bytes(self, content: t.Any) -> bytes:
        return json.dumps(
            content, default=_default_json, ensure_ascii=False, separators=(",", ":")
        ).encode(self.charset)


class HTMLResponse(Response):
    media_type = "text/html"


class PlainTextResponse(Response):
    media_type = "text/plain"


class NoContentResponse(Response):
    def __init__(self, headers: t.Mapping[str, str] | None = None) -> None:
        super().__init__(None, status_code=204, headers=headers)


class RedirectResponse(Response):
    def __init__(
        self, url: str, status_code: int = 307, headers: t.Mapping[str, str] | None = None
    ) -> None:
        super().__init__(None, status_code=status_code, headers=headers)
        self.headers["location"] = quote(str(url), safe=":/%#?=@[]!$&'()*+,;")


class StreamingResponse(Response):
    """Stream chunks from a sync or async iterable."""

    def __init__(
        self,
        content: t.Iterable[bytes] | t.AsyncIterable[bytes],
        status_code: int = 200,
        headers: t.Mapping[str, str] | None = None,
        media_type: str | None = None,
    ) -> None:
        super().__init__(None, status_code=status_code, headers=headers, media_type=media_type)
        self.stream(content)


class FileResponse(Response):
    """Send a file from disk with ETag, Last-Modified and Range support.

    Byte-range handling is what makes ``<video>`` seeking and resumable
    downloads work; it is applied automatically when the request carries a
    ``Range`` header.
    """

    chunk_size = 64 * 1024

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        status_code: int = 200,
        headers: t.Mapping[str, str] | None = None,
        media_type: str | None = None,
        filename: str | None = None,
        range_header: str | None = None,
        max_age: int | None = None,
    ) -> None:
        self.path = os.fspath(path)
        try:
            self.stat_result = os.stat(self.path)
        except FileNotFoundError as exc:
            from .exceptions import NotFound

            raise NotFound(f"No such file: {self.path}") from exc
        if not stat.S_ISREG(self.stat_result.st_mode):
            from .exceptions import NotFound

            raise NotFound(f"Not a regular file: {self.path}")

        guessed = media_type or mimetypes.guess_type(self.path)[0] or "application/octet-stream"
        super().__init__(None, status_code=status_code, headers=headers, media_type=guessed)

        size = self.stat_result.st_size
        mtime = datetime.fromtimestamp(self.stat_result.st_mtime, tz=timezone.utc)
        self.headers["last-modified"] = format_datetime(mtime, usegmt=True)
        self.headers["etag"] = f'"{self.stat_result.st_mtime_ns:x}-{size:x}"'
        self.headers["accept-ranges"] = "bytes"
        if max_age is not None:
            self.cache(max_age)
        if filename:
            self.attachment(filename)

        start, end = 0, size - 1
        if range_header:
            parsed = self._parse_range(range_header, size)
            if parsed is None:
                self.status_code = 416
                self.headers["content-range"] = f"bytes */{size}"
                self.headers["content-length"] = "0"
                return
            start, end = parsed
            self.status_code = 206
            self.headers["content-range"] = f"bytes {start}-{end}/{size}"

        length = max(0, end - start + 1)
        self.headers["content-length"] = str(length)
        self._stream = self._read(start, length)

    @staticmethod
    def _parse_range(header: str, size: int) -> tuple[int, int] | None:
        """Parse a single-range ``Range: bytes=...`` header."""
        if not header.startswith("bytes="):
            return None
        spec = header[6:].split(",")[0].strip()
        first, _, last = spec.partition("-")
        try:
            if not first:  # suffix range: last N bytes
                length = int(last)
                if length <= 0:
                    return None
                return max(0, size - length), size - 1
            start = int(first)
            end = int(last) if last else size - 1
        except ValueError:
            return None
        if start >= size or start > end:
            return None
        return start, min(end, size - 1)

    def _read(self, offset: int, length: int) -> t.Iterator[bytes]:
        with open(self.path, "rb") as fp:
            fp.seek(offset)
            remaining = length
            while remaining > 0:
                chunk = fp.read(min(self.chunk_size, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk
