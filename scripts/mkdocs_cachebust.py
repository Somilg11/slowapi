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

from mkdocs.exceptions import PluginError


def _fingerprint(docs_dir: Path, entry: t.Any) -> t.Any:
    # Non-string entries are mkdocs' richer script objects; a remote URL or an
    # entry that already carries a query is the author's business, not ours.
    if not isinstance(entry, str):
        return entry
    if "?" in entry or "://" in entry or entry.startswith("//"):
        return entry

    path = docs_dir / entry
    if not path.is_file():
        # Refusing here is the whole point. mkdocs does not validate extra_css
        # or extra_javascript paths -- not even under --strict -- so a typo or a
        # renamed file produces a page that links a stylesheet returning 404,
        # builds green, and deploys unstyled. This hook is the only thing in the
        # pipeline positioned to notice, so it must not shrug.
        raise PluginError(
            f"{entry!r} is listed in the site configuration but does not exist "
            f"at {path}. mkdocs will happily build a page that links it and "
            f"returns 404; fix the path or remove the entry."
        )

    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
    return f"{entry}?h={digest}"


def on_config(config: t.MutableMapping[str, t.Any], **_: t.Any) -> t.Any:
    docs_dir = Path(config["docs_dir"])
    for key in ("extra_css", "extra_javascript"):
        entries = config.get(key)
        if entries:
            config[key] = [_fingerprint(docs_dir, entry) for entry in entries]
    return config
