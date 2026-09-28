# Changelog

All notable changes to MAK4I Reference. Releases are tagged `vX.Y.Z-rc.N`;
the Python package version is the PEP 440 equivalent (`X.Y.ZrcN`).

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
