# Local setup & self-hosting

Everything needed to run MAK4I entirely on your own machine — the engine,
the CLI, and a local MCP server — with no cloud account and no
relationship to anyone else's data. The same steps scale up to
self-hosting your own shared instance.

If instead you want to connect an AI client to a hosted MAK4I deployment
(such as Talvik's), you need none of this — see "Hosted / server
deployments" in the top-level `README.md`.

## Prerequisites

- **Git**
- **uv** — <https://docs.astral.sh/uv/getting-started/installation/>
- **An MCP-capable AI client** (e.g. Claude Code) for Section 2

You do not install Python separately: the project pins Python 3.13
(`.python-version`) and `uv sync` downloads it if needed. MAK4I supports
Python 3.11+ (`requires-python = ">=3.11"` in `pyproject.toml`).

---

# Section 1 — Recommended Quick Start

```bash
git clone https://github.com/talvikai/mak4i-reference.git
cd mak4i-reference

uv sync --extra dev --no-editable

source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1

mak4i init
mak4i serve
```

### `uv sync --extra dev --no-editable`

`--no-editable` matters: `uv`'s editable install marks its generated
`.pth` file macOS-hidden, and CPython's `site.py` silently skips hidden
`.pth` files — which intermittently breaks `import mak4i` for the
installed console script. A plain-copy, non-editable install has no
`.pth` file at all.

Follow-on consequences:

1. **A bare `uv run <cmd>` re-syncs back to an editable install.** Prefer
   `source .venv/bin/activate` then call `mak4i` directly (the Quick
   Start above). If you do use `uv run`, keep passing `--no-editable` on
   syncs.
2. **New source files must be `git add`ed before they appear in the
   installed package** (hatchling's wheel file-selection is VCS-aware).
   After adding a module: `git add -A && uv sync --extra dev --no-editable
   --reinstall-package mak4i`.

Confirm the install:

```bash
uv run pytest       # all green; the GCS integration tests skip by design
```

### What `mak4i init` creates

One interactive command (organization name, your display name, first
project name — or pass `--org-name` / `--display-name` / `--project-name`
for a non-interactive run). It provisions, in order:

```
Organization
    ↓
Owner Principal          (role: owner)
    ↓
Project
    ↓
READ/WRITE Grant         (owner → project)
    ↓
Credential               (raw token, saved locally for `serve`)
```

**These are LOCAL resources belonging to your own MAK4I installation** —
a SQLite control plane and a JSON artifact store, both inside a gitignored
`.mak4i/` directory in the repo. Nothing is shared or uploaded; this has
no connection to the hosted Developer Preview.

`mak4i init` uses the same `ControlPlane` calls as the granular commands
in Section 3 — it adds no new behavior, just skips the copy-paste.

Run it again and it does **not** overwrite — it prints the existing
environment and exits. `mak4i init --force` provisions a fresh
organization/project/credential and repoints the local config; the
previous rows stay in the database (nothing is deleted).

### Where the local configuration is stored

| Path | Contents | Mode |
|---|---|---|
| `.mak4i/config.json` | organization / principal / project / credential **ids and names** + resolved backend settings | 0644 — no secret |
| `.mak4i/credentials.json` | `{"token": "mak4i_…"}` — the raw credential | **0600** |
| `.mak4i/control-plane.db` | local SQLite control plane | stores only the credential's SHA-256 **hash** |
| `.mak4i/artifacts/` | local `LocalJSONStore` | directory 0700 |

`.mak4i/` is in `.gitignore`. The raw token is stored in
`.mak4i/credentials.json` in plaintext **on purpose** — it is the price
of the `serve` convenience, and it is the only place the raw token exists
(the database keeps only its hash). Override the directory with
`MAK4I_HOME`.

### `mak4i serve`

Starts the existing MCP server (`mak4i.mcp_server`) against the
environment `init` created — no `MAK4I_TOKEN=…` prefix, no copied ids:

```
$ mak4i serve
MAK4I local server starting...
Organization: My Org
Project: Demo Project
Transport: stdio

MCP server ready.
```

- The banner is on **stderr**; stdout carries the MCP JSON-RPC stream.
- Default transport is `stdio` (single-user, one credential for the
  session). `MAK4I_TRANSPORT=streamable-http` (plus the vars in
  `docs/DEPLOYMENT.md`) switches to the HTTP transport.
- `.mak4i/` is **authoritative** for `serve`: a stale `MAK4I_TOKEN` or
  `MAK4I_CONTROL_PLANE_DB` exported in your shell is ignored (with a
  one-line heads-up on stderr, never the value), so `mak4i init &&
  mak4i serve` is deterministic. `python -m mak4i.mcp_server` is
  unaffected and reads the environment as given.
- Run `serve` before `init` and it fails clearly, telling you to run
  `init` — it never silently initializes.

---

# Section 2 — Connect an MCP client

`mak4i serve` speaks standard MCP over stdio. Any MCP-capable client that
can launch a stdio server works. With **Claude Code**, from the repo
directory (where `.mak4i/` lives) and with the venv active:

```bash
claude mcp add mak4i -- mak4i serve
claude mcp get mak4i          # expect: ✔ connected
```

`claude mcp add <name> -- <command…>` registers a stdio server that
Claude Code launches on demand. The MAK4I tools then appear in a Claude
Code session:

| Tool | Purpose |
|---|---|
| `mak4i_list_projects` | list the projects your credential can act on |
| `mak4i_search` | raw candidate lookup by type/tags/status |
| `mak4i_get_current` | resolved current applicable knowledge (conflict/integrity-checked) |
| `mak4i_create` | write new durable knowledge |
| `mak4i_supersede` | replace current knowledge, preserving lineage + history |
| `mak4i_history` | every version in a lineage, oldest first |

### Verify

1. `mak4i_list_projects` → your one project, `permissions:
   ["read","write"]`.
2. `mak4i_create(project=<id>, artifact_id="demo-1", artifact_type="decision",
   title="…", content="…")` → new artifact, `created_by` = your
   principal id.
3. `mak4i_get_current(project=<id>)` → that artifact, `conflicts: []`.
4. `mak4i_supersede(project=<id>, old_id="demo-1", content="…",
   reason="…")` → v2; then `mak4i_get_current` shows v2 and
   `mak4i_history(project=<id>, lineage_id="demo-1")` shows both.

Remove the connector when done: `claude mcp remove mak4i`.

---

# Section 3 — Manual / advanced setup

`mak4i init` is a convenience wrapper. The granular commands it wraps are
unchanged and remain the right tool for multiple organizations, extra
principals, scripting, or understanding the model. Each command's output
feeds the next.

```bash
# 1. Organization + its first owner principal (one command, two entities).
#    Prints {"organization": {...}, "owner": {...}} — copy both ids.
mak4i org create --name "My Org" --owner-display-name "Me"

# 2. A project in that org. --actor is the owner principal_id from step 1;
#    --organization-id is the organization_id from step 1. Prints project_id.
mak4i project create --actor <owner_principal_id> \
  --organization-id <organization_id> --name "My Project"

# 3. A read/write grant so the owner can act on the project.
mak4i grant create --actor <owner_principal_id> \
  --principal-id <owner_principal_id> --project-id <project_id> \
  --permissions read,write

# 4. A credential for the owner. Prints the raw token ONCE — copy it now.
mak4i credential issue --actor <owner_principal_id> \
  --principal-id <owner_principal_id>

# 5. Run the MCP server directly with that token (stdio).
MAK4I_TOKEN=<the raw token from step 4> python -m mak4i.mcp_server
```

`MAK4I_CONTROL_PLANE_DB` defaults to `sqlite:///./mak4i-control-plane.db`
(current directory). First run needs the schema — set
`MAK4I_CONTROL_PLANE_CREATE_TABLES=1` for a throwaway SQLite file, or run
`uv run alembic upgrade head` for one you keep. (`mak4i init` does this
for you and keeps its database inside `.mak4i/`.)

Other control-plane subcommands, same `--actor <owner_principal_id>`
pattern:

| Command | Purpose |
|---|---|
| `mak4i principal create --organization-id <id> --type human\|service\|agent --display-name <name> [--role member\|owner]` | add another principal (defaults to `member`) |
| `mak4i grant revoke --principal-id <id> --project-id <id>` | remove a grant |
| `mak4i credential revoke --credential-id <id>` | revoke a credential immediately, everywhere |

Every `create`/`revoke` above has a read-only counterpart — self-service
inspection without touching the database directly, owner-only and scoped
to your own organization (an owner of Org A gets `access denied`, never a
peek, on Org B's data):

| Command | Purpose |
|---|---|
| `mak4i org list` / `mak4i org show --organization-id <id>` | every organization in this deployment / one organization (trusted-operator scope, same as `org create` — there is no per-org owner check here since it predates any organization existing) |
| `mak4i project list --actor <owner_id> --organization-id <id>` / `mak4i project show --actor <owner_id> --project-id <id>` | projects in your organization |
| `mak4i principal list --actor <owner_id> --organization-id <id>` / `mak4i principal show --actor <owner_id> --principal-id <id>` | principals in your organization |
| `mak4i grant list --actor <owner_id> --principal-id <id>` | every grant held by one principal |
| `mak4i credential list --actor <owner_id> --principal-id <id>` | every credential issued to one principal — **never** prints the raw token or its hash, only `credential_id`/`display_name`/`status`/timestamps |

`mak4i credential issue` also prints ready-to-copy connect instructions
for both the hosted-HTTP and local-stdio cases right after the token
(pass `--no-connection-help` to suppress them if you're scripting and
just want the token).

### The artifact CLI (operator mode)

Artifact operations are also runnable directly, without a running server
or a credential, using `--principal <id>` — a trusted, **local-only**
mode audited distinctly (`auth_method: "operator_impersonation"`). Never
wired into the MCP server.

```bash
mak4i doctor      --principal <id> --project <project_id>
mak4i create      --principal <id> --project <project_id> \
  --artifact-id ... --artifact-type ... --title ... --content ...
mak4i supersede   --principal <id> --project <project_id> \
  --old-id ... --content ... --reason ...
mak4i search      --principal <id> --project <project_id> --tags ...
mak4i get-current  --principal <id> --project <project_id> --tags ...
mak4i history     --principal <id> --project <project_id> --lineage-id ...
```

(After `mak4i init`, the ids are in `.mak4i/config.json`.)

### Self-hosting a shared instance

Two environment changes, no code changes:

- **Control plane** — point `MAK4I_CONTROL_PLANE_DB` at any
  SQLAlchemy-supported database (e.g. a PostgreSQL URL) and migrate it
  with `uv run alembic upgrade head`.
- **Artifact store** — `MAK4I_STORE=gcs` with `MAK4I_GCS_BUCKET` and
  `MAK4I_GCP_PROJECT` for a private GCS bucket instead of `LocalJSONStore`
  (your bucket, your credentials — unrelated to Talvik's).

Then run with `MAK4I_TRANSPORT=streamable-http` behind HTTPS; the
credential in each request's `Authorization: Bearer` header is the
authorization boundary. See `docs/DEPLOYMENT.md` for the deployment
contract.

SQLite / PostgreSQL / GCS are reference choices, not MAK4I protocol
requirements — any `ControlPlaneStore` / `ArtifactStore` implementation
works.
