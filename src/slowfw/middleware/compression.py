"""Response compression.

Compression is skipped when the payload is small, already compressed, or of a
type that gains nothing (images, video, archives).  Compressing a 40-byte JSON
error makes it larger; the minimum-size check is not an optimisation, it is a
correctness detail people routinely forget.
"""

from __future__ import annotations

import gzip
import io
import typing as t

from ..request import Request
from ..response import Response

__all__ = ["GZipMiddleware"]

INCOMPRESSIBLE = (
    "image/",
    "video/",
    "audio/",
    "application/zip",
    "application/gzip",
    "application/x-brotli",
    "application/pdf",
    "font/woff",
)


class GZipMiddleware:
    """Gzip eligible responses when the client advertises support."""

    def __init__(self, minimum_size: int = 500, compress_level: int = 6) -> None:
        self.minimum_size = minimum_size
        self.compress_level = compress_level

    def _eligible(self, response: Response) -> bool:
        if response.is_streaming or "content-encoding" in response.headers:
            return False
        if len(response.body) < self.minimum_size:
            return False
        content_type = response.headers.get("content-type", "")
        return not any(content_type.startswith(prefix) for prefix in INCOMPRESSIBLE)

    async def dispatch(self, request: Request, response: Response, call_next: t.Any) -> t.Any:
        result = await call_next()
        target = result if isinstance(result, Response) else response

        target.vary("Accept-Encoding")
        if "gzip" not in (request.get("accept-encoding") or "").lower():
            return result
        if not self._eligible(target):
            return result

        buffer = io.BytesIO()
        with gzip.GzipFile(
            mode="wb", fileobj=buffer, compresslevel=self.compress_level, mtime=0
        ) as fp:
            fp.write(target.body)
        target.body = buffer.getvalue()
        target.headers["content-encoding"] = "gzip"
        target.headers["content-length"] = str(len(target.body))
        # A compressed body invalidates a strong ETag computed from the original.
        if "etag" in target.headers and not target.headers["etag"].startswith("W/"):
            target.headers["etag"] = "W/" + target.headers["etag"]
        return result
