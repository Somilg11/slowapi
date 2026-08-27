"""Single source of truth for the package version."""

from __future__ import annotations

__version__ = "0.1.0"

#: Parsed ``(major, minor, patch)`` tuple, handy for feature gates.
VERSION = tuple(int(part) for part in __version__.split(".")[:3])
