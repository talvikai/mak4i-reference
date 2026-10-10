# Changelog

All notable changes to MAK4I Reference. Releases are tagged `vX.Y.Z-rc.N`;
the Python package version is the PEP 440 equivalent (`X.Y.ZrcN`).

## v2.0.0-rc.1 — Developer Preview (prepared, not yet released)

Implements the MAK4I protocol v0.2 drafts (talvikai/mak4i-protocol#6):
MAK-0006 (identity and access control), MAK-0008 (MCP binding and
authorization), and Part A of MAK-0004 (subject conflicts) and MAK-0005
(governed project records). Developer Preview: for evaluation and
feedback, not for production use. The version jumps from 0.1 to 2.0
because of the breaking changes below; the protocol's own version (v0.2)
is independent.

### Added

- **Built-in OAuth 2.1 authorization server for MCP over HTTP** (MAK-0008
  §4–§8), off unless `MAK4I_OAUTH_ENABLED=1`: protected-resource and
  authorization-server metadata (path-prefix aware), `WWW-Authenticate`
  challenges, authorization code with PKCE S256 only, exact redirect
  matching with the RFC 8252 loopback rule, CLI-issued one-time sign-in
  codes (`mak4i oauth sign-in-code`) and a consent page, single-use codes
  (reuse revokes the authorization), opaque hashed tokens bound to the
  resource, refresh rotation with replay revocation and idle/absolute
  lifetimes, RFC 7009 revocation, RFC 9207 `iss`, client registration by
  pre-registration and client ID metadata documents (SSRF-protected), with
  dynamic registration optional and off by default.
- `mak4i oauth` administration: sign-in codes, `client register|list|disable`,
  `authorization list|revoke` (by authorization, principal or client).
- Permission `resolve` (MAK-0006 §5.1), used by conflict resolution.
- Migration `0002_oauth` (additive, reversible).
- **Authenticated agent identity** (MAK-0006 §3): agent principals carry
  a required, organization-scoped, immutable `agent_id`
  (`principal create --type agent --agent-id …`); humans and services
  have none. `mak4i_whoami` reports it from the stored principal.
- **Authenticated provenance** on every new record version (MAK-0006 §7):
  author principal, `principal_type`, `agent_id`, display name at write
  time, authentication method, credential or OAuth client, and the MCP
  client's self-reported name marked `verified: false`. Identity arguments
  in tool calls are ignored and never change authorship or access.
  Versions written earlier have no `provenance` and are not rewritten.
- `principal rename` and `principal deactivate` (deactivation also revokes
  the principal's OAuth authorizations).
- Migration `0003_agent_id`: adds `principals.agent_id`; existing agent
  principals get `agent-<12 hex of their id>`; reversible.
- **Deterministic conflict resolution** (MAK-0004 Part A): conflicts get
  a stable `conflict_id`, a state (`open` / `resolving` / `resolved`), every
  candidate with content, content hash and authenticated provenance, and an
  `identical_content` flag. New MCP tools `mak4i_list_conflicts`,
  `mak4i_get_conflict`, `mak4i_resolve_conflict` and CLI
  `mak4i conflict list|show|resolve`. Resolutions (`select_winner`, `merge`,
  `separate_subjects`) need the `resolve` permission, name the exact
  candidates they decide on (`conflict_changed` otherwise), are idempotent
  with an `idempotency_key`, record a durable resolution record (actor,
  reason, effects, resulting heads), and survive interruption (pending
  resolutions are completed at server start).
- Lifecycle status `withdrawn` (set only by a resolution), and
  `resolution_id` / `merged_from` on the versions a resolution creates or
  withdraws.
- Structured tool errors (MAK-0008 §9): failed tool results carry
  `structuredContent: {"error": {"code", "message", "retryable"}}` with
  codes such as `access_denied`, `validation_error`, `stale_head`,
  `subject_in_conflict`, `conflict_changed`, `conflict_not_open`.
- Migration `0004_resolution_records`; reversible.
- **Administration without a portal** (MAK-0006 §8): a durable
  administrative audit log (`admin_events`, migration `0005`) recording every
  administrative action — including denied attempts — with actor, how the
  actor authenticated, targets, outcome and time, never a secret;
  `mak4i audit list` (owners, own organization); `project archive`;
  `org suspend|reactivate` (instance operator); `mak4i admin inventory` and
  the bootstrap's `admin-info` for recovering organization, owner, project,
  principal and credential metadata without querying the database (#21).
- **Versioned, validated configuration** (MAK-0008 §12): the server
  validates every `MAK4I_*` setting at start and refuses to run with a full
  list of problems; `mak4i config check` prints the effective configuration
  (secrets only as references) following `docs/config.schema.json`
  (`mak4i config schema`). New settings: `MAK4I_DEPLOYMENT_MODE`,
  `MAK4I_TRUSTED_PROXIES` (forwarded headers trusted only from listed
  proxies), `MAK4I_LOG_LEVEL`, and `*_FILE` secret references for the
  control-plane database URL and the stdio token.
- Docs: stdio vs HTTP authentication, OAuth state across restarts (no
  signing keys), custom authentication adapter trust boundary, and an
  explicit single-replica statement.

### Upgrade

- Supported path: `v0.1.0-rc.5` → `v2.0.0-rc.1` (bootstrap `upgrade`, which
  backs up first). Migrations `0002`–`0005` are additive; existing agent
  principals get `agent-<12 hex>` ids. Local SQLite environments are
  upgraded in place. Rollback = restore the pre-upgrade backup with
  `v0.1.0-rc.5` (it can't read records written by this release).

### Known limitations

- Real-client OAuth interoperability (claude.ai / Claude Code and MCP
  Inspector over public HTTPS) is not yet verified for this release
  candidate; see the release notes before relying on a specific client.
- One server process per installation (see DEPLOYMENT.md → Replicas).
- Skills are planned/deferred and not part of this release.
- A general writer-initiated withdraw/retire operation is not provided
  (protocol issue #4); `withdrawn` is set only by conflict resolution.

### Changed

- **Breaking:** the `conflicts` entries of `mak4i_get_current` now follow
  the protocol conflict schema (`conflict_id`, `state`, `candidates`, …)
  instead of `lineage_ids` / `artifacts`.
- **Breaking:** conflict detection runs before the type/tags filters, so a
  tag filter can no longer return one side of a conflict as current.
- **Breaking:** `mak4i_supersede` with `release_subject_key=true` is
  refused (`subject_in_conflict`) while the lineage's subject is in an open
  conflict; resolve with `mak4i_resolve_conflict` instead.
- A supersede that loses a concurrent race is now audited
  (`SUPERSEDE_REJECTED`) and reported as `stale_head`.
- Subject keys are Unicode-NFC normalized (ASCII keys are unchanged) and
  limited to 512 characters.

- Effective permissions are the live grant intersected with the
  authentication ceiling (OAuth scopes); scopes never widen a grant, and a
  tool call whose token lacks the tool's scope gets HTTP 403
  `insufficient_scope` (MAK-0006 §5.3, MAK-0008 §4.2).
- Bearer authentication: the scheme is case-insensitive; duplicate
  `Authorization` headers and tokens in the query string are rejected
  (400); credential and OAuth tokens are told apart by prefix and never
  fall back to each other; MCP sessions are bound to the principal that
  opened them; `/.well-known/*` returns 404 instead of 401 when absent.
- A deactivated principal loses access on its very next request, whatever
  the authentication method; stdio sessions re-check their credential
  before every operation.
- `mak4i_whoami` adds `principal.principal_type` (canonical; `type` kept
  as an alias), `auth_method` and, for OAuth, `scopes`.

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
