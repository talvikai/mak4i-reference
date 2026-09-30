"""Verify every relative Markdown link and #anchor in the repository.

    uv run python scripts/check_docs_links.py

Checks each `[text](target)` in tracked-style Markdown files (README,
SECURITY, CHANGELOG, docs/): the target file must exist, and an `#anchor`
must match a heading in the target, using GitHub's heading-slug rules.
External links (http, https, mailto) aren't fetched.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("README.md", "SECURITY.md", "CHANGELOG.md", "docs")

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def github_slug(heading: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # [text](url) -> text
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    result = set()
    in_fence = False
    for line in path.read_text().splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        slug = github_slug(match.group(2))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if count == 0 else f"{slug}-{count}")
    return result


def markdown_files(root: Path) -> list[Path]:
    out = []
    for entry in FILES:
        path = root / entry
        if path.is_dir():
            out.extend(sorted(path.rglob("*.md")))
        elif path.is_file():
            out.append(path)
    return out


def check(root: Path = ROOT) -> list[str]:
    problems = []
    cache: dict[Path, set[str]] = {}
    for md in markdown_files(root):
        in_fence = False
        for number, line in enumerate(md.read_text().splitlines(), start=1):
            if _FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for target in _LINK.findall(line):
                if re.match(r"^(https?:|mailto:)", target):
                    continue
                file_part, _, anchor = target.partition("#")
                dest = (md.parent / file_part).resolve() if file_part else md
                where = f"{md.relative_to(root)}:{number}"
                if not dest.exists():
                    problems.append(f"{where}: missing file {target}")
                    continue
                if anchor and dest.suffix == ".md":
                    if dest not in cache:
                        cache[dest] = anchors(dest)
                    if anchor not in cache[dest]:
                        problems.append(f"{where}: no heading for #{anchor} in {dest.relative_to(root)}")
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"FAIL: {len(problems)} broken link(s)/anchor(s)", file=sys.stderr)
        return 1
    print(f"OK: all relative links and anchors resolve ({len(markdown_files(ROOT))} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
