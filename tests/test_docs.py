"""Documentation checks that keep the RC4 single-source structure honest:
every relative link and anchor resolves, and README stays an overview
without installation commands."""

from __future__ import annotations

import re

import check_docs_links as cdl
from check_release_consistency import ROOT


def test_every_relative_link_and_anchor_resolves():
    assert cdl.check() == []


def test_github_slugs():
    assert cdl.github_slug("9. Upgrade from v0.1.0-rc.3") == "9-upgrade-from-v010-rc3"
    assert cdl.github_slug("4.3 Network HTTP and tunnels") == "43-network-http-and-tunnels"
    assert cdl.github_slug("`LocalJSONStore` is single-writer") == "localjsonstore-is-single-writer"


def test_readme_contains_no_installation_or_operation_commands():
    readme = (ROOT / "README.md").read_text()
    assert "```bash" not in readme and "```powershell" not in readme and "```sh" not in readme
    for command in (
        "git clone", "uv sync", "docker compose", "docker run", "mak4i init", "mak4i serve",
        "pip install", "claude mcp add", "cloudflared", "export MAK4I_", "MAK4I_HOST=",
    ):
        assert command not in readme, command


def test_install_guides_are_the_only_homes_of_install_commands():
    install = re.compile(r"git clone --branch|uv sync --extra dev|mak4i-enterprise install")
    owners = {"docs/LOCAL_SETUP.md", "docs/ENTERPRISE_SELF_HOSTED.md"}
    for md in cdl.markdown_files(ROOT):
        rel = str(md.relative_to(ROOT))
        if rel in owners or rel == "CHANGELOG.md":
            continue
        assert not install.search(md.read_text()), f"installation command duplicated in {rel}"
