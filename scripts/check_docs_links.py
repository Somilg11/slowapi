#!/usr/bin/env python3
"""Verify that documentation references resolve -- links and site assets.

Broken links are a slow leak: each one costs a reader a few minutes and nobody
ever files an issue about it. A broken *asset* reference is worse, because it is
silent -- mkdocs does not validate ``extra_css`` or ``extra_javascript`` paths,
not even under ``--strict``, so a renamed stylesheet produces a page that links
a 404, builds green, and deploys unstyled. That has happened once already.

Both run in CI on every commit, so neither can accumulate.

    python scripts/check_docs_links.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")
SEARCH_DIRS = ("docs", "examples", "benchmarks", ".github")
MKDOCS = ROOT / "mkdocs.yml"
ASSET_KEYS = ("extra_css", "extra_javascript")
REMOTE = ("http://", "https://", "//")


def markdown_files() -> list[Path]:
    files = sorted(ROOT.glob("*.md"))
    for directory in SEARCH_DIRS:
        files.extend(sorted((ROOT / directory).rglob("*.md")))
    return files


def site_assets() -> list[str]:
    """Read the ``extra_css`` / ``extra_javascript`` entries from mkdocs.yml.

    Parsed by hand rather than with PyYAML so this stays runnable in the plain
    dev environment, which has no documentation dependencies installed.
    """
    if not MKDOCS.is_file():
        return []
    entries: list[str] = []
    collecting = False
    for raw in MKDOCS.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.rstrip().rstrip(":") in ASSET_KEYS:
            collecting = True
            continue
        if collecting:
            stripped = raw.strip()
            if stripped.startswith("- ") and raw[:1] in " \t":
                entries.append(stripped[2:].strip().strip("'\""))
            else:
                collecting = False
    return entries


def check_site_assets() -> list[str]:
    missing = []
    for entry in site_assets():
        if entry.startswith(REMOTE):
            continue
        if not (ROOT / "docs" / entry.split("?")[0]).is_file():
            missing.append(f"mkdocs.yml -> {entry}")
    return missing


def main() -> int:
    broken: list[str] = []
    checked = 0

    for path in markdown_files():
        for match in LINK_RE.finditer(path.read_text(encoding="utf-8")):
            target = match.group(1).split()[0]
            if target.startswith(SKIP_PREFIXES):
                continue
            checked += 1
            # Strip any anchor; we verify files exist, not heading slugs.
            resolved = (path.parent / target.split("#")[0]).resolve()
            if target.split("#")[0] and not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")

    missing_assets = check_site_assets()
    if missing_assets:
        print(f"{len(missing_assets)} site asset(s) referenced but absent:\n", file=sys.stderr)
        for entry in missing_assets:
            print(f"  {entry}", file=sys.stderr)
        print(
            "\nmkdocs does not validate these paths, so the build would have "
            "succeeded and the page would have linked a 404.",
            file=sys.stderr,
        )
        return 1

    if broken:
        print(f"{len(broken)} broken link(s) of {checked} checked:\n", file=sys.stderr)
        for entry in broken:
            print(f"  {entry}", file=sys.stderr)
        return 1

    assets = len(site_assets())
    print(f"All {checked} relative links resolve; {assets} site asset(s) present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
