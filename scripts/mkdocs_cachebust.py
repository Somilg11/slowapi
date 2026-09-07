"""Give ``extra_css`` and ``extra_javascript`` a content hash.

Material fingerprints its own bundle (``main.<hash>.min.css``) so a theme
upgrade reaches people immediately. Files listed in ``extra_css`` get no such
treatment: they are emitted verbatim, so a browser that already holds one keeps
serving it until its cache expires. That is invisible while developing -- the
dev server sends no-cache -- and shows up only after a deploy, as "the site
still has the old theme".

This hook appends ``?h=<first 8 hex of sha256>`` to every local asset, so the
URL changes exactly when the file does and never otherwise.
"""

from __future__ import annotations

import hashlib
import typing as t
from pathlib import Path


def _fingerprint(docs_dir: Path, entry: t.Any) -> t.Any:
    # Non-string entries are mkdocs' richer script objects; a remote URL or an
    # entry that already carries a query is the author's business, not ours.
    if not isinstance(entry, str):
        return entry
    if "?" in entry or "://" in entry or entry.startswith("//"):
        return entry

    path = docs_dir / entry
    if not path.is_file():
        return entry

    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return f"{entry}?h={digest}"


def on_config(config: t.MutableMapping[str, t.Any], **_: t.Any) -> t.Any:
    docs_dir = Path(config["docs_dir"])
    for key in ("extra_css", "extra_javascript"):
        entries = config.get(key)
        if entries:
            config[key] = [_fingerprint(docs_dir, entry) for entry in entries]
    return config
