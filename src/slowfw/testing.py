"""An in-process test client that exercises both protocols.

The point is not convenience -- though calling the app directly is far faster
than a socket -- it is *coverage*.  ``TestClient(app, protocol="wsgi")`` and
``TestClient(app, protocol="asgi")`` drive the same assertions through two
completely different adapters, which is how the dual-protocol promise stays
true rather than aspirational.
"""

from __future__ import annotations

import io
import json as jsonlib
import typing as t
from urllib.parse import urlencode, urlsplit

from .concurrency import run_coroutine_sync
from .datastructures import Headers

if t.TYPE_CHECKING:  # pragma: no cover
    from .app import SlowAPI

__all__ = ["TestClient", "TestResponse"]


#: What ``TestClient.request(files=...)`` accepts.
FileSpec = bytes | t.IO[bytes] | tuple[str, t.Any] | tuple[str, t.Any, str]
FileArg = t.Mapping[str, FileSpec] | t.Sequence[tuple[str, FileSpec]]


def encode_multipart(
    data: t.Mapping[str, t.Any] | bytes | str | None, files: FileArg
) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body from fields and uploads.

    Written out by hand rather than delegated: the test client is the only
    thing in the project allowed to construct a request, and taking a
    dependency here to test a zero-dependency framework would be a strange
    trade.  The wire format is small enough to own.
    """
    boundary = "----slowapitestboundary7MA4YWxkTrZu0gW"
    parts: list[bytes] = []

    if isinstance(data, t.Mapping):
        for field, value in data.items():
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                parts.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n'
                    f"{item}\r\n".encode()
                )

    entries = files.items() if isinstance(files, t.Mapping) else files
    for field, spec in entries:
        filename, payload, content_type = _normalise_file(field, spec)
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        )
        parts.append(header.encode() + payload + b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _normalise_file(field: str, spec: t.Any) -> tuple[str, bytes, str]:
    """Accept the several shapes people reasonably expect to work."""
    filename, handle, content_type = field, spec, "application/octet-stream"
    if isinstance(spec, tuple):
        if len(spec) == 3:
            filename, handle, content_type = spec
        elif len(spec) == 2:
            filename, handle = spec
        else:  # pragma: no cover - a malformed tuple is a test bug
            raise TypeError(f"files[{field!r}] must be a 2- or 3-tuple, got {len(spec)} items")
    payload = handle if isinstance(handle, bytes) else handle.read()
    if isinstance(payload, str):  # pragma: no cover - a text handle still works
        payload = payload.encode()
    return str(filename), payload, content_type


class TestResponse:
    """The result of a test request."""

    #: Stops pytest from trying to collect this class as a test suite.
    __test__ = False

    __slots__ = ("content", "headers", "request_path", "status_code")

    def __init__(
        self, status_code: int, headers: t.Sequence[tuple[str, str]], content: bytes, path: str
    ) -> None:
        self.status_code = status_code
        self.headers = Headers(headers)
        self.content = content
        self.request_path = path

    @property
    def body(self) -> bytes:
        """The decoded body.

        A real HTTP client transparently un-gzips, so a test that only wanted
        to assert on a JSON payload should not have to know that compression
        middleware is installed.  ``content`` still holds the bytes on the wire
        for tests that are specifically about the encoding.
        """
        encoding = self.headers.get("content-encoding", "").lower()
        if "gzip" not in encoding or not self.content:
            return self.content
        import gzip

        try:
            return gzip.decompress(self.content)
        except OSError:  # pragma: no cover - a truncated body is the test's point
            return self.content

    @property
    def text(self) -> str:
        charset = "utf-8"
        content_type = self.headers.get("content-type", "")
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()
        return self.body.decode(charset, errors="replace")

    def json(self) -> t.Any:
        return jsonlib.loads(self.body or b"null")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def raise_for_status(self) -> TestResponse:
        if not self.ok:
            raise AssertionError(
                f"{self.request_path} returned {self.status_code}: {self.text[:500]}"
            )
        return self

    @property
    def cookies(self) -> dict[str, str]:
        from http.cookies import SimpleCookie

        jar: dict[str, str] = {}
        for raw in self.headers.getlist("set-cookie"):
            parsed = SimpleCookie()
            parsed.load(raw)
            jar.update({k: v.value for k, v in parsed.items()})
        return jar

    def __repr__(self) -> str:
        return f"<TestResponse {self.status_code} {len(self.content)}b>"


class TestClient:
    """Drive an application in-process over WSGI or ASGI."""

    #: Stops pytest from trying to collect this class as a test suite.
    __test__ = False

    def __init__(
        self,
        app: SlowAPI,
        *,
        protocol: str = "wsgi",
        base_url: str = "http://testserver",
        headers: t.Mapping[str, str] | None = None,
        follow_cookies: bool = True,
        client: tuple[str, int] = ("127.0.0.1", 50000),
    ) -> None:
        if protocol not in ("wsgi", "asgi"):
            raise ValueError("protocol must be 'wsgi' or 'asgi'")
        self.app = app
        self.protocol = protocol
        self.base_url = base_url.rstrip("/")
        self.default_headers = dict(headers or {})
        self.follow_cookies = follow_cookies
        #: The peer address the app sees.  Set it to test anything that depends
        #: on who is connecting -- proxy trust, IP allow-lists, rate-limit keys.
        self.client = client
        #: Cookies collected from previous responses, sent on the next request.
        self.cookies: dict[str, str] = {}

    # ------------------------------------------------------------- requests

    def request(
        self,
        method: str,
        path: str,
        *,
        params: t.Mapping[str, t.Any] | None = None,
        json: t.Any = None,
        data: t.Mapping[str, t.Any] | bytes | str | None = None,
        files: FileArg | None = None,
        headers: t.Mapping[str, str] | None = None,
        cookies: t.Mapping[str, str] | None = None,
    ) -> TestResponse:
        """Send one request and return the response.

        :param files:
            Uploads to send as ``multipart/form-data``, alongside any ``data``.
            Accepts ``{field: (filename, fileobj, content_type)}`` or a list of
            ``(field, (filename, fileobj, content_type))`` pairs when one field
            carries several files.  ``fileobj`` may also be raw ``bytes``.
        """
        url = urlsplit(path if "://" in path else self.base_url + path)
        query = url.query
        if params:
            extra = urlencode(params, doseq=True)
            query = f"{query}&{extra}" if query else extra

        body = b""
        merged: dict[str, str] = {**self.default_headers, **(headers or {})}
        if files is not None:
            body, content_type = encode_multipart(data, files)
            merged.setdefault("content-type", content_type)
        elif json is not None:
            body = jsonlib.dumps(json).encode()
            merged.setdefault("content-type", "application/json")
        elif isinstance(data, (bytes, str)):
            body = data.encode() if isinstance(data, str) else data
        elif data is not None:
            body = urlencode(data, doseq=True).encode()
            merged.setdefault("content-type", "application/x-www-form-urlencoded")

        jar = {**(self.cookies if self.follow_cookies else {}), **(cookies or {})}
        if jar:
            merged["cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        if body:
            merged["content-length"] = str(len(body))
        merged.setdefault("host", urlsplit(self.base_url).netloc or "testserver")

        response = (
            self._send_wsgi(method, url.path or "/", query, body, merged)
            if self.protocol == "wsgi"
            else self._send_asgi(method, url.path or "/", query, body, merged)
        )
        if self.follow_cookies:
            self.cookies.update(response.cookies)
        return response

    def get(self, path: str, **kw: t.Any) -> TestResponse:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: t.Any) -> TestResponse:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: t.Any) -> TestResponse:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw: t.Any) -> TestResponse:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw: t.Any) -> TestResponse:
        return self.request("DELETE", path, **kw)

    def head(self, path: str, **kw: t.Any) -> TestResponse:
        return self.request("HEAD", path, **kw)

    def options(self, path: str, **kw: t.Any) -> TestResponse:
        return self.request("OPTIONS", path, **kw)

    # ------------------------------------------------------------ transports

    def _send_wsgi(
        self, method: str, path: str, query: str, body: bytes, headers: t.Mapping[str, str]
    ) -> TestResponse:
        environ: dict[str, t.Any] = {
            "REQUEST_METHOD": method.upper(),
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "SERVER_NAME": "testserver",
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "REMOTE_ADDR": self.client[0],
            "REMOTE_PORT": str(self.client[1]),
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": urlsplit(self.base_url).scheme or "http",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        for name, value in headers.items():
            key = name.upper().replace("-", "_")
            environ[key if key in ("CONTENT_TYPE", "CONTENT_LENGTH") else f"HTTP_{key}"] = value

        captured: dict[str, t.Any] = {}

        def start_response(
            status: str, response_headers: list[tuple[str, str]], exc_info: t.Any = None
        ) -> t.Any:
            captured["status"] = int(status.split(" ", 1)[0])
            captured["headers"] = response_headers
            return lambda chunk: None

        iterable = self.app.wsgi_app(environ, start_response)
        try:
            content = b"".join(iterable)
        finally:
            close = getattr(iterable, "close", None)
            if close is not None:
                close()
        return TestResponse(captured["status"], captured["headers"], content, path)

    def _send_asgi(
        self, method: str, path: str, query: str, body: bytes, headers: t.Mapping[str, str]
    ) -> TestResponse:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": urlsplit(self.base_url).scheme or "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": self.client,
            "server": ("testserver", 80),
        }

        messages: list[dict[str, t.Any]] = []
        pending = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive() -> dict[str, t.Any]:
            return pending.pop(0) if pending else {"type": "http.disconnect"}

        async def send(message: t.MutableMapping[str, t.Any]) -> None:
            messages.append(dict(message))

        run_coroutine_sync(self.app.asgi_app(scope, receive, send))

        start = next(m for m in messages if m["type"] == "http.response.start")
        content = b"".join(
            m.get("body", b"") for m in messages if m["type"] == "http.response.body"
        )
        header_pairs = [
            (k.decode("latin-1"), v.decode("latin-1")) for k, v in start.get("headers", [])
        ]
        return TestResponse(start["status"], header_pairs, content, path)

    # ------------------------------------------------------------- lifespan

    def __enter__(self) -> TestClient:
        run_coroutine_sync(self.app.startup())
        return self

    def __exit__(self, *exc: t.Any) -> None:
        run_coroutine_sync(self.app.shutdown())
