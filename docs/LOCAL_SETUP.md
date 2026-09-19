# Local setup & self-hosting

Everything needed to run MAK4I entirely on your own machine — the engine,
the CLI, and a local MCP server — with no cloud account and no
relationship to anyone else's data. The same steps scale up to
self-hosting your own shared instance.

If instead you want to connect an AI client to MAK4I Platform (Talvik's
separate, hosted product), you need none of this — see "Enterprise
Self-Hosted" in the top-level `README.md` for how MAK4I Reference and
MAK4I Platform relate.

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
  session). `--transport http` (or `MAK4I_TRANSPORT=http` /
  `streamable-http`) switches to Streamable HTTP — see
  "Local Streamable HTTP testing" below.
- `.mak4i/` is **authoritative** for `serve`: a stale `MAK4I_TOKEN` or
  `MAK4I_CONTROL_PLANE_DB` exported in your shell is ignored (with a
  one-line heads-up on stderr, never the value), so `mak4i init &&
  mak4i serve` is deterministic. `python -m mak4i.mcp_server` is
  unaffected and reads the environment as given.
- Run `serve` before `init` and it fails clearly, telling you to run
  `init` — it never silently initializes.

### Local Streamable HTTP testing

**`stdio` is not a network endpoint.** The examples above have no port
and no URL because there is nothing listening — the client launches
`mak4i serve` itself and talks over stdin/stdout. To get an actual
`http://…/mcp` URL you can `curl` or point a remote-style MCP client at,
start the *other* transport:

```bash
mak4i serve --transport http
```

```
MAK4I local server starting...
Organization: My Org
Project: Demo Project

Transport: Streamable HTTP
Host: 127.0.0.1
Port: 8080
MCP endpoint: http://127.0.0.1:8080/mcp

MCP server ready.
```

This is the **same** `MAK4IEngine`, the **same** six tools, and the
**same** credential-based authentication as stdio — only the transport
differs. `--host`/`--port` (or `MAK4I_HOST`/`MAK4I_PORT`) override the
defaults; `--port` beats `$MAK4I_PORT` beats `$PORT` beats the built-in
default, so an existing `$PORT` in your shell (common on PaaS-style
setups) still works unless you override it. `'http'` and
`'streamable-http'` are accepted interchangeably everywhere this reads —
`streamable-http` is kept for compatibility with existing container
configuration (`docs/DEPLOYMENT.md`).

Three plain endpoints, once it's running:

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | liveness — process is up |
| `GET /ready` | none | readiness — the control-plane backend is reachable |
| `POST /mcp` | `Authorization: Bearer <credential>` | the MCP Streamable HTTP endpoint |

```bash
curl http://127.0.0.1:8080/health   # -> ok
curl http://127.0.0.1:8080/ready    # -> ready
```

`/mcp` requires the same bearer credential `.mak4i/credentials.json`
holds — a real MCP client (initialize → tools/list → tools/call) is the
practical way to exercise it; raw `curl` needs the full JSON-RPC
handshake, which is what an MCP client does for you.

Where a client supports it, prefer reading the credential from an
environment variable or a file over typing it directly as a command-line
argument or into a shell prompt — command-line arguments and interactive
input are both liable to end up recorded in your shell history. Reading
it out of `.mak4i/credentials.json` (mode `0600`) programmatically, as
the examples above do, avoids that.

**A cloud-hosted AI client (Claude.ai, Cowork, Gemini, …) cannot reach
`127.0.0.1` on your machine** — that address means "this machine" to
whatever's asking, and their infrastructure isn't this machine. Binding
to loopback here is intentional: it's the safe default for a developer
testing the protocol locally, not a way to expose the server remotely.
If you need a cloud client to reach a local server temporarily, expose it
through an HTTPS tunnel — see "Exposing a local server to a cloud client"
below. This is a development convenience only, never a MAK4I dependency,
and never how an Enterprise Self-Hosted deployment works
(`docs/DEPLOYMENT.md`: a real container behind a real ingress).

Stop the server with **Ctrl-C** either way (stdio or HTTP) — it shuts
down the session manager and exits.

### Credential persistence

A MAK4I credential is a row in the control-plane database
(`.mak4i/control-plane.db` locally), not something held in the server
process's memory. **Stopping and restarting `mak4i serve` does not
invalidate an existing credential** — the same token keeps authenticating
successfully as long as it hasn't been explicitly revoked or its
`--expires-at` has not passed, and you point the restarted server at the
same `.mak4i/` (or `MAK4I_CONTROL_PLANE_DB`). Verified directly: issue a
credential, authenticate with it, stop the server, restart it, and
authenticate again with the same, unmodified credential — it keeps
working with no re-issuing step.

### Exposing a local server to a cloud client

A cloud-hosted AI client cannot reach a server bound to `127.0.0.1`, so
local testing with one requires two things together:

```bash
mak4i serve --transport http --host 0.0.0.0
cloudflared tunnel --url http://127.0.0.1:8080
```

Two details matter here, both confirmed by direct testing, not just in
theory:

1. **`--host 0.0.0.0` is required, not optional, once you're tunneling.**
   The default loopback bind also enables DNS-rebinding protection,
   which allowlists only `Host` headers naming `127.0.0.1`/`localhost`.
   A tunnel forwards your request with *its own* public hostname as
   `Host` — which fails that allowlist and comes back as
   `421 Misdirected Request`, even though the tunnel and server are both
   working correctly. `--host 0.0.0.0` is the non-loopback case, where
   this protection is (correctly) not applied — credential
   authentication remains the real access-control boundary either way.
2. **The tunnel's target must be `127.0.0.1`, not `0.0.0.0`, and must be
   a bare origin with no path.** `--host` controls what the *server*
   listens on; `cloudflared`'s `--url` is a separate, *destination*
   address it connects to — `127.0.0.1:<port>` is always the correct
   target there, regardless of what the server bound to (a server bound
   to `0.0.0.0` still answers on `127.0.0.1`). Appending a path (e.g.
   `http://127.0.0.1:8080/mcp` instead of `http://127.0.0.1:8080`) is
   incorrect: `--url` is the origin every incoming path gets proxied
   onto, so a path there gets appended a second time on top of whatever
   path the actual request already has, breaking routing.

Quick Tunnel (`cloudflared tunnel --url ...` with no other setup) is a
free, account-less Cloudflare feature appropriate for a short
development/testing session — **not** a MAK4I dependency and **not** a
production deployment architecture:

- it hands you a temporary `*.trycloudflare.com` hostname, printed to
  the terminal when it starts;
- **that hostname can change every time the tunnel is restarted** —
  confirmed directly: stopping and starting a new Quick Tunnel assigns a
  different hostname each time;
- if it changes, update the URL configured in your AI client — nothing
  about the MAK4I side changes. **Tunnel restart, and a tunnel hostname
  change, are both independent of MAK4I credential lifecycle** —
  confirmed by authenticating with the same, unmodified credential
  through two different tunnel hostnames in succession; both succeeded
  identically. A MAK4I credential remains valid until it is explicitly
  revoked or expires, never as a side effect of where the server happens
  to be reachable;
- for anything beyond a short test, prefer a stable HTTPS hostname
  instead of repeatedly relying on Quick Tunnel: a named/persistent
  tunnel, a reverse proxy, or an externally reachable Enterprise
  Self-Hosted deployment (`docs/DEPLOYMENT.md`) all work, and MAK4I has
  no dependency on any particular one of them.

**Troubleshooting:**

- *"MAK4I has not been initialized locally."* — run `mak4i init` first.
- *`curl: (7) Failed to connect`* — the server isn't running, or you're
  using the wrong port; check the banner's `Port:` line.
- *`401 {"error":"unauthorized"}` from `/mcp`* — missing, wrong, expired,
  or revoked credential. `.mak4i/credentials.json` has the current one —
  but if you have more than one local `.mak4i/` instance (see below),
  check you're reading the one the *running* server actually uses.
  `mak4i init --force` mints a fresh credential if needed.
- *`421 Misdirected Request`* while tunneling* — see "Exposing a local
  server to a cloud client" above: you're bound to `127.0.0.1` while a
  tunnel is forwarding a non-loopback `Host` header. Restart with
  `--host 0.0.0.0`.
- *A cloud AI client can't determine how the server "signs in"* — some
  clients probe for OAuth support before falling back to a manual
  bearer-token configuration step. MAK4I doesn't implement OAuth by
  design (see `docs/DEPLOYMENT.md`'s security considerations) — this is
  expected, not an error; continue to that client's manual/custom-header
  auth configuration and enter `Authorization: Bearer <credential>`
  there.
- *A working credential suddenly returns `401` after moving directories
  or changing terminals* — `mak4i serve` resolves `.mak4i/` (or
  `MAK4I_HOME`) relative to its **current working directory** at
  startup. Running `mak4i serve` from two different directories creates
  two independent local instances with two different credentials, which
  looks identical in the startup banner (same organization/project name
  if you happened to `mak4i init` both the same way) but authenticates
  against a different database. Check the running process's working
  directory if a credential you're sure is correct keeps failing.
- *A cloud AI client says it can't reach your endpoint at all* — see the
  loopback note above; you need `--host 0.0.0.0` plus a tunnel (for
  temporary testing) or a real ingress (for an Enterprise Self-Hosted
  deployment), not a loopback bind.

---

# Section 2 — Connect an MCP client

`mak4i serve` speaks standard MCP over stdio by default. Any MCP-capable
client that can launch a stdio server works. With **Claude Code**, from
the repo directory (where `.mak4i/` lives) and with the venv active:

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

Then run with `mak4i serve --transport http --host 0.0.0.0` (or the
equivalent `MAK4I_TRANSPORT`/`MAK4I_HOST`/`MAK4I_PORT` environment
variables — a container needs no CLI flags at all) behind HTTPS; the
credential in each request's `Authorization: Bearer` header is the
authorization boundary. See `docs/DEPLOYMENT.md` for the full deployment
contract, including `/health`/`/ready` and TLS termination.

SQLite / PostgreSQL / GCS are reference choices, not MAK4I protocol
requirements — any `ControlPlaneStore` / `ArtifactStore` implementation
works.
