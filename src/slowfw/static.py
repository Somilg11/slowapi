"""Serving files from disk, safely.

Static file handling is where path traversal bugs live.  Every candidate path
is resolved and checked against the root before anything is opened, symlinks
included -- ``..%2f..%2fetc%2fpasswd`` and a symlink pointing outside the root
are both refused.
"""

from __future__ import annotations

import os
from urllib.parse import unquote

from .exceptions import ConfigurationError, NotFound
from .request import Request
from .response import FileResponse, Response

__all__ = ["StaticFiles"]


class StaticFiles:
    """A handler that serves ``directory`` under a mounted path.

    ::

        app.mount_static("/assets", "public", max_age=31536000, immutable=True)
    """

    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        index_file: str | None = "index.html",
        max_age: int = 3600,
        immutable: bool = False,
        follow_symlinks: bool = False,
        html_fallback: str | None = None,
    ) -> None:
        self.directory = os.path.realpath(os.fspath(directory))
        if not os.path.isdir(self.directory):
            raise ConfigurationError(f"Static directory does not exist: {self.directory}")
        self.index_file = index_file
        self.max_age = max_age
        self.immutable = immutable
        self.follow_symlinks = follow_symlinks
        #: Single-page-app fallback: serve this file for unmatched paths.
        self.html_fallback = html_fallback

    def resolve(self, relative: str) -> str | None:
        """Map a URL path to a real file, or ``None`` if it is not servable."""
        cleaned = unquote(relative).lstrip("/")
        if "\x00" in cleaned:
            return None

        candidate = os.path.realpath(os.path.join(self.directory, cleaned))
        if not self._within_root(candidate):
            return None

        if os.path.isdir(candidate) and self.index_file:
            candidate = os.path.join(candidate, self.index_file)
            if not self._within_root(os.path.realpath(candidate)):
                return None

        if os.path.isfile(candidate):
            if not self.follow_symlinks and os.path.islink(os.path.join(self.directory, cleaned)):
                return None
            return candidate
        return None

    def _within_root(self, path: str) -> bool:
        try:
            return os.path.commonpath([path, self.directory]) == self.directory
        except ValueError:  # different drives on Windows
            return False

    def __call__(self, request: Request, response: Response) -> Response:
        relative = request.path_params.get("path", "")
        path = self.resolve(relative)

        if path is None and self.html_fallback:
            path = self.resolve(self.html_fallback)
        if path is None:
            raise NotFound(f"No such file: {relative!r}")

        file_response = FileResponse(
            path,
            range_header=request.get("range"),
            max_age=self.max_age,
        )
        if self.immutable:
            file_response.cache(self.max_age, immutable=True)

        # Honour conditional requests so repeat visits cost one 304.
        if request.get("if-none-match") == file_response.headers.get("etag"):
            not_modified = Response(None, status_code=304)
            not_modified.headers["etag"] = file_response.headers["etag"]
            not_modified.headers["cache-control"] = file_response.headers.get("cache-control", "")
            return not_modified
        return file_response
