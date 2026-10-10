# Changelog

All notable changes to MAK4I Reference. Releases are tagged `vX.Y.Z-rc.N`;
the Python package version is the PEP 440 equivalent (`X.Y.ZrcN`).

## v0.1.0-rc.5 — Developer Preview (2026-10-10)

Security and maintenance release candidate. Developer Preview: for
evaluation and feedback, not for production use. Everyone on
`v0.1.0-rc.4` should upgrade.

### Security

- **urllib3 2.7.0 → 2.8.0** (PYSEC-2026-4175, PYSEC-2026-4176,
  PYSEC-2026-4177) ([#23](https://github.com/talvikai/mak4i-reference/pull/23)).

### Fixed

- **Bootstrap backups taken in the same second no longer collide.** A
  second backup (for example an `upgrade` retried immediately) failed with
  `... already exists`; the directory name now gets a `-1`, `-2`, …
  suffix ([#23](https://github.com/talvikai/mak4i-reference/pull/23)).
- **Bootstrap `restore` and `upgrade --use-backup` accept backups made by
  the previous release** (`v0.1.0-rc.4`), so a backup taken just before
  upgrading stays usable. Older backups are still refused.

### Changed

- Runtime image: **Python 3.13 → 3.14** (`python:3.14.7-slim`)
  ([#13](https://github.com/talvikai/mak4i-reference/pull/13)) and uv
  0.13.0 ([#22](https://github.com/talvikai/mak4i-reference/pull/22)).
- Dependencies: `mcp` 2.3.0
  ([#17](https://github.com/talvikai/mak4i-reference/pull/17)),
  starlette 1.7.0, uvicorn 0.54.0, alembic 1.20.0, google-auth 2.60.0
  ([#19](https://github.com/talvikai/mak4i-reference/pull/19),
  [#15](https://github.com/talvikai/mak4i-reference/pull/15),
  [#14](https://github.com/talvikai/mak4i-reference/pull/14),
  [#16](https://github.com/talvikai/mak4i-reference/pull/16)).

### Compatibility

- **No data migration.** RC5 reads and writes the same database schema
  and artifact format as RC4, and tool results are unchanged.
- The supported upgrade path is `v0.1.0-rc.4` → `v0.1.0-rc.5` (bootstrap
  `upgrade`, or the manual procedure). A `v0.1.0-rc.3` installation
  upgrades to RC4 first. Rolling back to RC4 keeps working data.

## v0.1.0-rc.4 — Developer Preview (2026-09-30)

Hardening release candidate. Developer Preview: for evaluation and
feedback, not for production use.

### Added

- **Enterprise bootstrap** (`deploy/bootstrap/mak4i-enterprise`), now the
  recommended way to install and operate Enterprise Self-Hosted on a Linux
  VM. Commands: `preflight`, `install`, `status`, `restart`, `upgrade`,
  `backup`, `restore` and `uninstall`.
  - **Preflight** checks the host, Docker, DNS against the VM's public IP,
    ports, outbound HTTPS and existing data, and reports PASS, WARN or FAIL.
  - **Install** is idempotent, with a generated mode-600 `.env` whose
    secrets are never printed. It verifies health, readiness, the schema
    and the HTTPS certificate.
  - **Upgrade** takes a verified backup first.
  - **Uninstall** preserves data by default; destroying data needs a
    confirmation.
  - Stable exit codes, `--dry-run` and `--non-interactive`.
  - It never changes cloud resources, DNS, firewalls or IAM.
- **`mak4i_whoami`**: identifies the MAK4I connection (name, environment,
  endpoint, version), the organization and the principal
  ([#8](https://github.com/talvikai/mak4i-reference/issues/8)).
- `MAK4I_INSTANCE_NAME` and `MAK4I_ENVIRONMENT` name an installation for
  AI clients. The defaults are `MAK4I local (<organization>)` / `local`
  for `mak4i serve`, and `MAK4I Enterprise` / `enterprise` for Compose.
- `MAK4I_TLS_ISSUER=internal`: Caddy's own CA, for private or air-gapped
  networks. The default is `acme` (Let's Encrypt).
- Scheduled dependency and container-image scanning, CI, and Dependabot
  ([#10](https://github.com/talvikai/mak4i-reference/issues/10)).
- `scripts/check_release_consistency.py` and `scripts/check_docs_links.py`,
  both run by the test suite.

### Changed

- **Cross-connection write safety**
  ([#9](https://github.com/talvikai/mak4i-reference/issues/9)):
  - Access denials name the connection and environment, and say the
    denial is final for this connection and must not be retried on another
    one without the user's explicit confirmation.
  - Other write failures say which connection failed and that nothing was
    written.
  - `mak4i_create` / `mak4i_supersede` descriptions and the MCP server
    instructions carry the same rule.
  - `mak4i_list_projects` adds `organization_name`, `connection` and
    `environment`.
  - Denials still never echo the requested project.
- **Container images are pinned** to reviewed versions and digests:
  Python 3.13.15-slim, PostgreSQL 16.15, Caddy 2.11.4, and uv 0.11.32
  copied from its official image. The unused system `pip` is removed from
  the MAK4I image, which now has no fixable HIGH or CRITICAL finding.
  Remediation thresholds are in `SECURITY.md`.
- **Documentation consolidated:**
  - `docs/LOCAL_SETUP.md` and `docs/ENTERPRISE_SELF_HOSTED.md` are the only
    installation and operation guides.
  - README is an overview with no commands.
  - `docs/DEPLOYMENT.md` covers architecture and the runtime contract only.
  - Enterprise requirements now include DNS, firewall and certificate
    guidance from a verified Google Cloud deployment.
- `mak4i serve` with a missing `.mak4i/credentials.json` explains how to
  issue a replacement credential for the existing owner (it no longer
  suggests `mak4i init --force`).

### Fixed

- The Local and Enterprise uninstall documentation distinguishes
  preserving data from permanently deleting it. The macOS/Linux and
  PowerShell commands are listed separately.

### Compatibility

- **No data migration.** RC4 reads and writes the same database schema
  and artifact format as RC3.
- The supported upgrade path is `v0.1.0-rc.3` → `v0.1.0-rc.4` (bootstrap
  `upgrade`, or the manual procedure). Rolling back to RC3 keeps working
  data.
- Tool results are unchanged apart from the added `mak4i_list_projects`
  fields and the wording of error messages. Clients that match on the old
  `access denied` text still find it at the start of the message.

### Known limitations

- A server can't control what an AI client does on *other* MCP
  connections. RC4 identifies each connection and states the no-fallback
  rule everywhere a model reads it, but can't enforce it.
- Real Let's Encrypt issuance with the bootstrap and a clean VM on a
  public cloud are part of the release acceptance run, not the automated
  tests. See the release notes for what was verified.
- The Windows PowerShell commands (Local only) have been syntax-checked
  but not yet run on Windows. The Enterprise bootstrap is Linux-only.
- Terraform (or other infrastructure-as-code) modules and Kubernetes/Helm
  packaging aren't provided.
- ChatGPT's connector UI can't send a bearer-token header.

## v0.1.0-rc.3 — Developer Preview (2026-09-28)

Release candidate. Developer Preview: for evaluation and feedback, not for
production use.

### Fixed

- **Independent artifacts sharing classification tags no longer conflict**
  ([#5](https://github.com/talvikai/mak4i-reference/issues/5)). Previously,
  any two current artifacts of the same `artifact_type` sharing at least two
  tags were reported as a conflict. For example, four separate requirements
  documents tagged alike couldn't be read as current. Tags are now
  classification and search metadata only: they never establish lineage
  identity or conflict membership.
- **Genuine competing claims are now explicit.** Artifacts can carry an
  optional `subject_key`, a stable machine-readable key naming the one
  logical subject they decide (e.g. `session-cache`). Two current artifacts
  from different lineages conflict only when they share `artifact_type` and
  the same `subject_key`, within one organization and project.
- **Explicit supersession continues to preserve predecessor and successor
  history.** A normal supersede keeps a lineage's `subject_key`. Changing it,
  or giving a key to a lineage that has none, is rejected and writes nothing.
- **Conflicts are resolved with an explicit subject release.**
  `supersede(..., release_subject_key=true)` on the losing lineage creates a
  new version that no longer claims the subject. It's recorded as
  `released_subject_key` on that version and in the audit trail; the winning
  lineage is untouched, and history still shows the earlier claim.
- **Windows PowerShell uninstall and cleanup instructions now use valid
  PowerShell commands**
  ([#6](https://github.com/talvikai/mak4i-reference/issues/6)), with separate
  macOS/Linux and PowerShell blocks throughout.
- **Local reinstall, data reset and complete removal are documented
  separately** (`docs/LOCAL_SETUP.md`, Section 4): stop, recreate `.venv`,
  upgrade keeping data, remove MCP registrations, remove the repository,
  and reset local data.
- **Enterprise Docker shutdown and destructive volume deletion are clearly
  distinguished** (`docs/ENTERPRISE_SELF_HOSTED.md`, §11): stop, remove the
  application while keeping data, complete purge (after a verified backup),
  and removing images.
- The Local Quick Start code block no longer contains prose that a shell
  would try to execute.
- Enterprise Self-Hosted backups and restores no longer use shell
  redirection, which corrupts binary backups in Windows PowerShell. A
  restore now re-owns copied artifact files so the unprivileged server can
  write to them.
- The Enterprise Self-Hosted upgrade procedure works for clones of a release
  tag (it previously used `git pull`, which fails on a detached tag
  checkout).

### Added

- `subject_key` on `mak4i_create` / `mak4i create --subject-key`, and
  `release_subject_key` on `mak4i_supersede` /
  `mak4i supersede --release-subject-key`. Conflicts in `get_current` results
  now include `subject_key`.
- `mak4i --version`.

### Documentation

- README and installation guides now reference `v0.1.0-rc.3`. The README
  routes to the authoritative Local and Enterprise guides instead of
  duplicating an install guide.
- Local, Enterprise, macOS/Linux and Windows instructions have been audited.
- Destructive commands state exactly what they delete (organizations,
  principals, projects, grants, credentials, artifacts with lineage and
  history, the control-plane database, `.mak4i/`, `.env`, Docker volumes,
  MCP client configuration).
- MCP client removal guidance was verified against the Claude Code, Codex
  CLI, Grok CLI and Gemini CLI command-line help. Claude.ai / Cowork
  connectors are removed in their UI.
- `docs/MVP_ARCHITECTURE.md` §18.1 specifies lineage identity, subject
  authority, classification tags, and explicit subject release.

### Compatibility

- `subject_key` and `released_subject_key` are optional and additive.
  Artifacts written by v0.1.0-rc.2 load and work unchanged, and never take
  part in a cross-lineage conflict.
- Stores omit the new fields while unset, so artifacts that don't use them
  stay readable by v0.1.0-rc.2. An artifact that carries a `subject_key`
  requires v0.1.0-rc.3 or later.
- **Behavior change:** artifacts that previously conflicted only because they
  shared tags now all resolve as current. To keep two lineages competing,
  give both the same `subject_key`.

### Known limitations

- Developer Preview: APIs, CLI behavior, configuration, protocol details and
  deployment patterns may change. Not recommended for production use.
  Enterprise Self-Hosted remains part of the Developer Preview.
- The Windows PowerShell commands were syntax-checked and their file
  operations were executed with PowerShell 7.5 on macOS. They have **not yet
  been run on Windows**: Windows file locking, the
  `.venv\Scripts\Activate.ps1` path, and Windows PowerShell 5.1 remain
  untested.
- A lineage can release its `subject_key` but can't reclaim or change it in
  this release (create a new lineage instead). There's no generic
  withdrawn/retired lineage state
  ([talvikai/mak4i-protocol#4](https://github.com/talvikai/mak4i-protocol/issues/4)).
- Conflicts are detected only through an explicit shared `subject_key`; no
  content-level (semantic) incompatibility is inferred. MAK-0004 §1 (conflict
  definition) is still an open draft decision in the protocol.
- ChatGPT's connector UI can't send a bearer-token header, so it can't
  connect to MAK4I as-is.

## v0.1.0-rc.2 — Developer Preview (2026-09-25)

Documentation-focused release candidate improving the Local Quick Start:
it identifies which server mode to use for local stdio clients, local HTTP
clients, and cloud-hosted clients through an HTTPS tunnel. No change to the
context model or transport behavior.

## v0.1.0-rc.1 — Developer Preview (2026-09-25)

First release candidate: local MAK4I environments; project-scoped durable
AI context; organizations, principals, projects, grants and credentials;
bearer-token authentication and project-level authorization; artifact
creation, retrieval, lineage and supersession; MCP over stdio and
Streamable HTTP; local SQLite control plane and local artifact storage; an
early Enterprise Self-Hosted deployment path for evaluation.
