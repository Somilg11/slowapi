#!/usr/bin/env python3
"""Verify that every relative Markdown link in the repository resolves.

Broken links in documentation are a slow leak: each one costs a reader a few
minutes and nobody ever files an issue about it. This runs in CI so they cannot
accumulate.

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


def markdown_files() -> list[Path]:
    files = sorted(ROOT.glob("*.md"))
    for directory in SEARCH_DIRS:
        files.extend(sorted((ROOT / directory).rglob("*.md")))
    return files


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

    if broken:
        print(f"{len(broken)} broken link(s) of {checked} checked:\n", file=sys.stderr)
        for entry in broken:
            print(f"  {entry}", file=sys.stderr)
        return 1

    print(f"All {checked} relative links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
