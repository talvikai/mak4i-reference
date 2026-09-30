"""MAK4I reference implementation."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: `version` in pyproject.toml (PEP 440, e.g.
    # "0.1.0rc4" for the v0.1.0-rc.4 release tag). Every other place that
    # names the current release is checked against it by
    # scripts/check_release_consistency.py.
    __version__ = version("mak4i")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0+unknown"
