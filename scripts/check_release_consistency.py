"""Fail if anything that names the *current* release disagrees with
pyproject.toml, the one authoritative version source.

    uv run python scripts/check_release_consistency.py           # check
    uv run python scripts/check_release_consistency.py --report  # also list
                                                                  # every other
                                                                  # rc reference

What is checked (all derived from `[project].version`, e.g. `0.1.0rc4`,
whose release tag is `v0.1.0-rc.4`):

- `uv.lock`'s entry for the `mak4i` package has the same version;
- the Dockerfile's OCI `org.opencontainers.image.version` label is the
  tag without its `v`;
- the Enterprise bootstrap's `MAK4I_RELEASE` constant is the tag;
- README.md states the current release;
- every clone / checkout / tag-fetch command in the active documentation
  (README, SECURITY, the Local and Enterprise guides, the deployment
  overview, and deploy/) names the current tag. Historical and upgrade
  references to *earlier* releases in prose are allowed (they're listed by
  `--report`); a reference to a release *newer* than the current one is
  always an error.

CHANGELOG.md and docs/MVP_ARCHITECTURE.md are history and are never
checked for the current version.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTIVE_DOCS = (
    "README.md",
    "SECURITY.md",
    "docs/LOCAL_SETUP.md",
    "docs/ENTERPRISE_SELF_HOSTED.md",
    "docs/DEPLOYMENT.md",
)
ACTIVE_DIRS = ("deploy",)
# Guides that must each carry at least one install command for the current tag.
INSTALL_GUIDES = ("docs/LOCAL_SETUP.md", "docs/ENTERPRISE_SELF_HOSTED.md")

_TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)-rc\.(\d+)")
# Commands that select a release: `git clone --branch vX`, `git checkout vX`,
# `git fetch ... tag vX`, `--version vX` (bootstrap), `MAK4I_RELEASE=vX`.
_COMMAND_RE = re.compile(
    r"(?:--branch|checkout|\btag|--to-version|--version)\s+(v\d+\.\d+\.\d+-rc\.\d+)"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def pep440_to_tag(version: str) -> str:
    """`0.1.0rc4` -> `v0.1.0-rc.4`."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)rc(\d+)", version)
    if not match:
        raise ValueError(f"unsupported project version {version!r} (expected X.Y.ZrcN)")
    return f"v{match.group(1)}-rc.{match.group(2)}"


def _tag_key(tag: str) -> tuple[int, ...]:
    match = _TAG_RE.fullmatch(tag)
    assert match, tag
    return tuple(int(g) for g in match.groups())


def project_version(root: Path) -> str:
    return tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]


def _active_files(root: Path) -> list[Path]:
    files = [root / p for p in ACTIVE_DOCS if (root / p).is_file()]
    for directory in ACTIVE_DIRS:
        base = root / directory
        if base.is_dir():
            files.extend(p for p in sorted(base.rglob("*")) if p.is_file())
    return files


def check(root: Path = ROOT) -> tuple[list[Finding], list[Finding]]:
    """Return `(errors, other_references)`."""
    version = project_version(root)
    tag = pep440_to_tag(version)
    errors: list[Finding] = []
    others: list[Finding] = []

    lock = (root / "uv.lock").read_text()
    lock_match = re.search(r'\[\[package\]\]\nname = "mak4i"\nversion = "([^"]+)"', lock)
    if not lock_match or lock_match.group(1) != version:
        found = lock_match.group(1) if lock_match else "missing"
        errors.append(Finding("uv.lock", 0, f"mak4i version {found!r} != {version!r} (run `uv lock`)"))

    dockerfile = (root / "Dockerfile").read_text()
    label = re.search(r'org\.opencontainers\.image\.version="([^"]+)"', dockerfile)
    if not label or label.group(1) != tag.removeprefix("v"):
        found = label.group(1) if label else "missing"
        errors.append(Finding("Dockerfile", 0, f"image version label {found!r} != {tag.removeprefix('v')!r}"))

    bootstrap = root / "deploy/bootstrap/mak4i-enterprise"
    if bootstrap.is_file():
        release = re.search(r'^readonly MAK4I_RELEASE="([^"]+)"', bootstrap.read_text(), re.M)
        if not release or release.group(1) != tag:
            found = release.group(1) if release else "missing"
            errors.append(Finding(str(bootstrap.relative_to(root)), 0, f"MAK4I_RELEASE {found!r} != {tag!r}"))
    else:
        errors.append(Finding("deploy/bootstrap/mak4i-enterprise", 0, "bootstrap script missing"))

    readme = (root / "README.md").read_text()
    if f"Current release: [`{tag}`]" not in readme:
        errors.append(Finding("README.md", 0, f"does not state `Current release: [`{tag}`]`"))

    for guide in INSTALL_GUIDES:
        text = (root / guide).read_text() if (root / guide).is_file() else ""
        if f"--branch {tag}" not in text:
            errors.append(Finding(guide, 0, f"no `--branch {tag}` install command"))

    for path in _active_files(root):
        rel = str(path.relative_to(root))
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, start=1):
            for command_tag in _COMMAND_RE.findall(line):
                if command_tag != tag and not _is_upgrade_source(line, command_tag):
                    errors.append(Finding(rel, number, f"command selects {command_tag}, expected {tag}"))
            for match in _TAG_RE.finditer(line):
                found = match.group(0)
                if _tag_key(found) > _tag_key(tag):
                    errors.append(Finding(rel, number, f"references future release {found}"))
                elif found != tag:
                    others.append(Finding(rel, number, f"{found}: {line.strip()[:110]}"))
    return errors, others


def _is_upgrade_source(line: str, command_tag: str) -> bool:
    """`--from-version v0.1.0-rc.3`-style upgrade sources are legitimate."""
    return f"--from-version {command_tag}" in line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true", help="also list references to earlier releases")
    args = parser.parse_args(argv)

    version = project_version(ROOT)
    errors, others = check(ROOT)
    print(f"current release: {pep440_to_tag(version)} (pyproject version {version})")
    if args.report:
        print(f"\nreferences to other releases in active files ({len(others)}):")
        for finding in others:
            print(f"  {finding}")
    if errors:
        print(f"\nFAIL: {len(errors)} inconsistency(ies):", file=sys.stderr)
        for finding in errors:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("OK: package metadata, image label, bootstrap and active docs agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
