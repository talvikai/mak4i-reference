"""MAK4I reference implementation."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: `version` in pyproject.toml (PEP 440, e.g.
    # "0.1.0rc3" for the v0.1.0-rc.3 release tag).
    __version__ = version("mak4i")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0+unknown"
