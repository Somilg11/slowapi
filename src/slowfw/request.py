"""The protocol-neutral incoming request object.

A :class:`Request` is built from a *scope* dictionary that both the WSGI and
ASGI adapters produce, plus a :class:`BodyReader` that knows how to pull bytes
off whichever transport is in play.  Because every body accessor exists in both
a sync and an async flavour, the same handler code works under either server.
"""

from __future__ import annotations

import asyncio
import json
import typing as t
from http.cookies import SimpleCookie

from .concurrency import iterate_in_threadpool, run_coroutine_sync
from .datastructures import URL, Address, FormData, Headers, QueryParams, State
from .exceptions import BadRequest, PayloadTooLarge, UnsupportedMediaType
from .formparsers import parse_multipart, parse_options_header, parse_urlencoded

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import SlowAPI

__all__ = ["AsyncBodyReader", "BodyReader", "Request", "SyncBodyReader"]

#: Bodies larger than this are rejected unless the app overrides it.
DEFAULT_MAX_BODY_SIZE = 16 * 1024 * 1024


class BodyReader:
    """Transport-specific source of request body chunks.

    Subclasses implement whichever direction is native to their transport; the
    other direction is provided here by bridging through the event loop.
    """

    async def aiter(self) -> t.AsyncIterator[bytes]:  # pragma: no cover - abstract
        raise NotImplementedError
        yield b""  # pragma: no cover

    def iter_sync(self) -> t.Iterator[bytes]:  # pragma: no cover - abstract
        raise NotImplementedError


class SyncBodyReader(BodyReader):
    """Reads from a blocking file-like object such as ``environ['wsgi.input']``."""

    def __init__(
        self, stream: t.BinaryIO, content_length: int | None, chunk_size: int = 65_536
    ) -> None:
        self._stream = stream
        self._remaining = content_length
        self._chunk_size = chunk_size

    def iter_sync(self) -> t.Iterator[bytes]:
        if self._remaining is None:
            # Chunked transfer: the server has already de-chunked the stream,
            # so read until EOF.
            while True:
                chunk = self._stream.read(self._chunk_size)
                if not chunk:
                    return
                yield chunk
        while self._remaining > 0:
            chunk = self._stream.read(min(self._chunk_size, self._remaining))
            if not chunk:
                return
            self._remaining -= len(chunk)
            yield chunk

    async def aiter(self) -> t.AsyncIterator[bytes]:
        # On the loop-free fast path there is no loop to offload to -- and no
        # loop to block either, so reading inline is both necessary and correct.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            for chunk in self.iter_sync():
                yield chunk
            return
        async for chunk in iterate_in_threadpool(self.iter_sync()):
            yield chunk


class AsyncBodyReader(BodyReader):
    """Reads from an ASGI ``receive`` callable."""

    def __init__(
        self,
        receive: t.Callable[[], t.Awaitable[t.MutableMapping[str, t.Any]]],
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._receive = receive
        self._loop = loop
        self._done = False

    async def aiter(self) -> t.AsyncIterator[bytes]:
        while not self._done:
            message = await self._receive()
            if message["type"] == "http.disconnect":
                self._done = True
                raise ClientDisconnect()
            body = message.get("body", b"")
            if body:
                yield body
            if not message.get("more_body", False):
                self._done = True

    def iter_sync(self) -> t.Iterator[bytes]:
        # A `def` handler running in the ASGI threadpool still needs the body;
        # hop back onto the serving loop for each chunk.
        agen = self.aiter()

        async def _next() -> bytes | None:
            try:
                return await agen.__anext__()
            except StopAsyncIteration:
                return None

        while True:
            chunk = run_coroutine_sync(_next(), loop=self._loop)
            if chunk is None:
                return
            yield chunk


class ClientDisconnect(Exception):
    """Raised when the peer closes the connection mid-body."""


class Request:
    """An inbound HTTP request.

    Express-style aliases (:attr:`params`, :attr:`query`, :meth:`get`,
    :attr:`ip`) sit alongside the explicit accessors so that handlers ported
    from Node read naturally.
    """

    __slots__ = (
        "_body",
        "_cookies",
        "_form",
        "_headers",
        "_json",
        "_query",
        "_reader",
        "_state",
        "_url",
        "app",
        "max_body_size",
        "path_params",
        "scope",
    )

    def __init__(
        self,
        scope: t.MutableMapping[str, t.Any],
        reader: BodyReader | None = None,
        *,
        app: SlowAPI | None = None,
        max_body_size: int = DEFAULT_MAX_BODY_SIZE,
    ) -> None:
        self.scope = scope
        self._reader = reader
        self._body: bytes | None = None
        self._json: t.Any = _UNSET
        self._form: FormData | None = None
        self._cookies: dict[str, str] | None = None
        self._headers: Headers | None = None
        self._query: QueryParams | None = None
        self._url: URL | None = None
        self._state = State(scope.setdefault("state", {}))
        self.path_params: dict[str, t.Any] = scope.setdefault("path_params", {})
        self.app = app
        self.max_body_size = max_body_size

    # ------------------------------------------------------------------ meta

    @property
    def method(self) -> str:
        return self.scope["method"]

    @property
    def scheme(self) -> str:
        return self.scope.get("scheme", "http")

    @property
    def path(self) -> str:
        return self.scope["path"]

    @property
    def root_path(self) -> str:
        return self.scope.get("root_path", "")

    @property
    def http_version(self) -> str:
        return self.scope.get("http_version", "1.1")

    @property
    def headers(self) -> Headers:
        if self._headers is None:
            self._headers = Headers(self.scope.get("headers", []))
        return self._headers

    @property
    def query_params(self) -> QueryParams:
        if self._query is None:
            raw = self.scope.get("query_string", b"")
            if isinstance(raw, bytes):
                raw = raw.decode("latin-1")
            self._query = QueryParams(raw)
        return self._query

    #: Express alias for :attr:`query_params`.
    query = query_params

    @property
    def params(self) -> dict[str, t.Any]:
        """Express alias for :attr:`path_params`."""
        return self.path_params

    @property
    def url(self) -> URL:
        if self._url is None:
            host = self.headers.get("host") or "localhost"
            qs = self.scope.get("query_string", b"")
            if isinstance(qs, bytes):
                qs = qs.decode("latin-1")
            suffix = f"?{qs}" if qs else ""
            self._url = URL(f"{self.scheme}://{host}{self.root_path}{self.path}{suffix}")
        return self._url

    @property
    def cookies(self) -> dict[str, str]:
        if self._cookies is None:
            jar = SimpleCookie()
            raw = self.headers.get("cookie")
            if raw:
                jar.load(raw)
            self._cookies = {k: v.value for k, v in jar.items()}
        return self._cookies

    @property
    def client(self) -> Address | None:
        client = self.scope.get("client")
        if client is None:
            return None
        host, port = client
        return Address(host, port)

    @property
    def ip(self) -> str | None:
        """Best-effort client IP, honouring ``X-Forwarded-For`` when trusted.

        The proxy header is only consulted if a
        :class:`~slowfw.middleware.proxy.ProxyHeadersMiddleware` has marked
        the scope as coming from a trusted hop, so it cannot be spoofed by
        default.
        """
        if self.scope.get("_trusted_proxy"):
            forwarded = self.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
        client = self.client
        return client.host if client else None

    @property
    def state(self) -> State:
        return self._state

    @property
    def content_type(self) -> str:
        media_type, _ = parse_options_header(self.headers.get("content-type"))
        return media_type

    @property
    def charset(self) -> str:
        _, options = parse_options_header(self.headers.get("content-type"))
        return options.get("charset", "utf-8")

    @property
    def request_id(self) -> str | None:
        """Correlation id assigned by ``RequestIDMiddleware``, if installed."""
        return self.scope.get("request_id")

    def get(self, header: str, default: str | None = None) -> str | None:
        """Express alias for header lookup: ``req.get("content-type")``."""
        return self.headers.get(header, default)

    def is_(self, media_type: str) -> bool:
        """Express ``req.is()``: does the body match this media type?"""
        return self.content_type == media_type.lower()

    def accepts(self, media_type: str) -> bool:
        """Return whether the client's ``Accept`` header allows ``media_type``."""
        accept = self.headers.get("accept", "*/*")
        wanted = media_type.lower()
        family = wanted.split("/")[0] + "/*"
        offers = {part.split(";")[0].strip().lower() for part in accept.split(",")}
        return bool(offers & {wanted, family, "*/*"})

    # ------------------------------------------------------------------ body

    def _check_length(self) -> None:
        raw = self.headers.get("content-length")
        if raw and raw.isdigit() and int(raw) > self.max_body_size:
            raise PayloadTooLarge(f"Request body exceeds the {self.max_body_size} byte limit")

    async def stream(self) -> t.AsyncIterator[bytes]:
        """Yield body chunks as they arrive, without buffering the whole body."""
        if self._body is not None:
            yield self._body
            return
        if self._reader is None:
            return
        self._check_length()
        total = 0
        async for chunk in self._reader.aiter():
            total += len(chunk)
            if total > self.max_body_size:
                raise PayloadTooLarge(f"Request body exceeds the {self.max_body_size} byte limit")
            yield chunk

    def stream_sync(self) -> t.Iterator[bytes]:
        """Blocking counterpart of :meth:`stream`."""
        if self._body is not None:
            yield self._body
            return
        if self._reader is None:
            return
        self._check_length()
        total = 0
        for chunk in self._reader.iter_sync():
            total += len(chunk)
            if total > self.max_body_size:
                raise PayloadTooLarge(f"Request body exceeds the {self.max_body_size} byte limit")
            yield chunk

    async def body(self) -> bytes:
        """Read and cache the entire request body."""
        if self._body is None:
            chunks = [chunk async for chunk in self.stream()]
            self._body = b"".join(chunks)
        return self._body

    def body_sync(self) -> bytes:
        """Blocking counterpart of :meth:`body`."""
        if self._body is None:
            self._body = b"".join(self.stream_sync())
        return self._body

    async def text(self) -> str:
        return (await self.body()).decode(self.charset, errors="replace")

    def text_sync(self) -> str:
        return self.body_sync().decode(self.charset, errors="replace")

    def _decode_json(self, raw: bytes) -> t.Any:
        if self._json is not _UNSET:
            return self._json
        if not raw:
            self._json = None
            return None
        try:
            self._json = json.loads(raw.decode(self.charset))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BadRequest(f"Body is not valid JSON: {exc}") from exc
        return self._json

    async def json(self) -> t.Any:
        """Parse the body as JSON, raising :class:`BadRequest` if malformed."""
        return self._decode_json(await self.body())

    def json_sync(self) -> t.Any:
        return self._decode_json(self.body_sync())

    def _parse_form(self, raw: bytes) -> FormData:
        if self._form is not None:
            return self._form
        media_type, options = parse_options_header(self.headers.get("content-type"))
        charset = options.get("charset", "utf-8")
        if media_type == "application/x-www-form-urlencoded":
            self._form = parse_urlencoded(raw, charset)
        elif media_type == "multipart/form-data":
            self._form = parse_multipart(raw, options.get("boundary", ""), charset=charset)
        else:
            raise UnsupportedMediaType(
                "Expected application/x-www-form-urlencoded or multipart/form-data, "
                f"got {media_type or 'nothing'}"
            )
        return self._form

    async def form(self) -> FormData:
        """Parse a urlencoded or multipart body into a :class:`FormData`."""
        return self._parse_form(await self.body())

    def form_sync(self) -> FormData:
        return self._parse_form(self.body_sync())

    def __repr__(self) -> str:
        return f"<Request {self.method} {self.path}>"


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "<unset>"


_UNSET = _Unset()
