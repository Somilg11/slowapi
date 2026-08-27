"""Immutable, protocol-neutral containers for HTTP primitives.

Nothing in this module knows about WSGI or ASGI.  Both adapters normalise their
native representations into these types, which is what makes the rest of the
framework protocol agnostic.
"""

from __future__ import annotations

import typing as t
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

__all__ = [
    "URL",
    "Address",
    "FormData",
    "Headers",
    "MultiDict",
    "MutableHeaders",
    "QueryParams",
    "State",
    "UploadFile",
]

_KeyValues = t.Union[
    "MultiDict",
    t.Mapping[str, t.Any],
    t.Sequence[tuple[str, t.Any]],
    str,
    None,
]


class MultiDict(t.Mapping[str, t.Any]):
    """An immutable mapping where one key may hold several values.

    Indexing returns the *last* value for a key, matching the behaviour of
    every mainstream web framework, while :meth:`getlist` exposes all of them.
    """

    __slots__ = ("_dict", "_list")

    def __init__(self, value: _KeyValues = None) -> None:
        if value is None:
            items: list[tuple[str, t.Any]] = []
        elif isinstance(value, MultiDict):
            items = list(value._list)
        elif isinstance(value, str):
            items = list(parse_qsl(value, keep_blank_values=True))
        elif hasattr(value, "items"):
            items = list(t.cast(t.Mapping[str, t.Any], value).items())
        else:
            items = [(str(k), v) for k, v in value]
        self._list: list[tuple[str, t.Any]] = items
        self._dict: dict[str, t.Any] = dict(items)

    def __getitem__(self, key: str) -> t.Any:
        return self._dict[key]

    def __iter__(self) -> t.Iterator[str]:
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def __contains__(self, key: object) -> bool:
        return key in self._dict

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MultiDict):
            return NotImplemented
        return sorted(self._list) == sorted(other._list)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._list!r})"

    def getlist(self, key: str) -> list[t.Any]:
        """Return every value recorded for ``key``, in insertion order."""
        return [v for k, v in self._list if k == key]

    def multi_items(self) -> list[tuple[str, t.Any]]:
        """Return every ``(key, value)`` pair, including duplicate keys."""
        return list(self._list)


class QueryParams(MultiDict):
    """Parsed URL query string, e.g. ``?tag=a&tag=b``."""

    def __str__(self) -> str:
        return urlencode(self._list, doseq=True)


class FormData(MultiDict):
    """Parsed form body; values are ``str`` or :class:`UploadFile`."""

    async def close(self) -> None:
        for _, value in self._list:
            if isinstance(value, UploadFile):
                await value.close()


class UploadFile:
    """A file uploaded through ``multipart/form-data``.

    Small files stay in memory; anything past ``spool_max_size`` is rolled over
    to a temporary file on disk by :class:`~tempfile.SpooledTemporaryFile`.
    """

    __slots__ = ("_file", "content_type", "filename", "headers", "size")

    def __init__(
        self,
        file: t.BinaryIO,
        *,
        filename: str | None = None,
        content_type: str = "application/octet-stream",
        headers: Headers | None = None,
        size: int = 0,
    ) -> None:
        self.filename = filename
        self.content_type = content_type
        self.headers = headers or Headers()
        self.size = size
        self._file = file

    async def read(self, size: int = -1) -> bytes:
        from .concurrency import run_in_threadpool

        return await run_in_threadpool(self._file.read, size)

    def read_sync(self, size: int = -1) -> bytes:
        return self._file.read(size)

    async def seek(self, offset: int) -> None:
        from .concurrency import run_in_threadpool

        await run_in_threadpool(self._file.seek, offset)

    async def close(self) -> None:
        from .concurrency import run_in_threadpool

        await run_in_threadpool(self._file.close)

    def __repr__(self) -> str:
        return f"UploadFile(filename={self.filename!r}, size={self.size})"


#: Headers arrive as latin-1 bytes from ASGI and as text from WSGI; both are
#: accepted so neither adapter has to convert before constructing.
RawHeaders = t.Sequence[tuple[bytes, bytes]] | t.Sequence[tuple[str, str]] | t.Mapping[str, str]


class Headers(t.Mapping[str, str]):
    """Case-insensitive, multi-value HTTP headers (read-only)."""

    __slots__ = ("_list",)

    def __init__(
        self,
        raw: RawHeaders | None = None,
    ) -> None:
        items: list[tuple[str, str]] = []
        if raw is None:
            pass
        elif hasattr(raw, "items"):
            items = [(str(k).lower(), str(v)) for k, v in raw.items()]
        else:
            for key, value in raw:
                k = key.decode("latin-1") if isinstance(key, bytes) else str(key)
                v = value.decode("latin-1") if isinstance(value, bytes) else str(value)
                items.append((k.lower(), v))
        self._list = items

    def __getitem__(self, key: str) -> str:
        key = key.lower()
        for k, v in self._list:
            if k == key:
                return v
        raise KeyError(key)

    def __iter__(self) -> t.Iterator[str]:
        seen: set[str] = set()
        for k, _ in self._list:
            if k not in seen:
                seen.add(k)
                yield k

    def __len__(self) -> int:
        return len({k for k, _ in self._list})

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key.lower() in {k for k, _ in self._list}

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._list!r})"

    def getlist(self, key: str) -> list[str]:
        key = key.lower()
        return [v for k, v in self._list if k == key]

    def raw_items(self) -> list[tuple[str, str]]:
        return list(self._list)

    def mutablecopy(self) -> MutableHeaders:
        return MutableHeaders(self._list)


class MutableHeaders(Headers):
    """Headers you can edit, used for outgoing responses."""

    __slots__ = ()

    def __setitem__(self, key: str, value: str) -> None:
        """Replace every existing value for ``key`` with ``value``."""
        key = key.lower()
        self._list = [(k, v) for k, v in self._list if k != key]
        self._list.append((key, str(value)))

    def __delitem__(self, key: str) -> None:
        key = key.lower()
        self._list = [(k, v) for k, v in self._list if k != key]

    def add(self, key: str, value: str) -> None:
        """Append a value without removing existing ones (``Set-Cookie``)."""
        self._list.append((key.lower(), str(value)))

    def setdefault(self, key: str, value: str) -> str:
        if key.lower() in self:
            return self[key]
        self[key] = value
        return value

    def update(self, other: t.Mapping[str, str]) -> None:
        for k, v in other.items():
            self[k] = v

    def encode(self) -> list[tuple[bytes, bytes]]:
        """Render to the ASGI wire format."""
        return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in self._list]


class URL:
    """A parsed request URL with cheap component replacement."""

    __slots__ = ("_parts", "_url")

    def __init__(self, url: str) -> None:
        self._url = url
        self._parts = urlsplit(url)

    @property
    def scheme(self) -> str:
        return self._parts.scheme

    @property
    def netloc(self) -> str:
        return self._parts.netloc

    @property
    def hostname(self) -> str | None:
        return self._parts.hostname

    @property
    def port(self) -> int | None:
        return self._parts.port

    @property
    def path(self) -> str:
        return self._parts.path or "/"

    @property
    def query(self) -> str:
        return self._parts.query

    @property
    def fragment(self) -> str:
        return self._parts.fragment

    def replace(self, **kwargs: t.Any) -> URL:
        return URL(self._parts._replace(**kwargs).geturl())

    def include_query_params(self, **params: t.Any) -> URL:
        merged = MultiDict(self.query).multi_items() + list(params.items())
        return self.replace(query=urlencode(merged, doseq=True))

    def __str__(self) -> str:
        return self._url

    def __eq__(self, other: object) -> bool:
        return str(self) == str(other)

    def __hash__(self) -> int:
        return hash(self._url)

    def __repr__(self) -> str:
        return f"URL({self._url!r})"


class Address(t.NamedTuple):
    """A remote or local peer address."""

    host: str
    port: int | None = None


class State:
    """A namespace bag for per-request or per-application scratch data.

    Attribute access is proxied onto a plain dict so ``request.state.user = x``
    works without polluting the request object's own attribute space.
    """

    __slots__ = ("_state",)

    def __init__(self, initial: t.Mapping[str, t.Any] | None = None) -> None:
        object.__setattr__(self, "_state", dict(initial or {}))

    def __getattr__(self, key: str) -> t.Any:
        try:
            return self._state[key]
        except KeyError:
            raise AttributeError(f"State has no attribute {key!r}") from None

    def __setattr__(self, key: str, value: t.Any) -> None:
        self._state[key] = value

    def __delattr__(self, key: str) -> None:
        del self._state[key]

    def __contains__(self, key: str) -> bool:
        return key in self._state

    def get(self, key: str, default: t.Any = None) -> t.Any:
        return self._state.get(key, default)

    def as_dict(self) -> dict[str, t.Any]:
        return dict(self._state)

    def __repr__(self) -> str:
        return f"State({self._state!r})"


def url_quote(value: str) -> str:
    return quote(value, safe="/")


def url_unquote(value: str) -> str:
    return unquote(value)
