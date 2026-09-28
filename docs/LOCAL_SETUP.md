# Local setup & self-hosting

Everything needed to run MAK4I entirely on your own machine — the engine,
the CLI, and a local MCP server — with no cloud account and no
relationship to anyone else's data. To run a shared instance that other
people and remote AI clients connect to, see
[`ENTERPRISE_SELF_HOSTED.md`](ENTERPRISE_SELF_HOSTED.md) instead.

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

macOS / Linux:

```bash
git clone --branch v0.1.0-rc.3 --depth 1 https://github.com/talvikai/mak4i-reference.git
cd mak4i-reference
uv sync --extra dev --no-editable
source .venv/bin/activate
mak4i init
```

Windows PowerShell:

```powershell
git clone --branch v0.1.0-rc.3 --depth 1 https://github.com/talvikai/mak4i-reference.git
Set-Location mak4i-reference
uv sync --extra dev --no-editable
.\.venv\Scripts\Activate.ps1
mak4i init
```

Then start the server in the mode you need. You can stop it (Ctrl+C) and
switch modes at any time; every mode serves the same `.mak4i/` environment
(same organization, project, credential, and stored context).

| You're connecting | Run |
|---|---|
| a local MCP client that launches MAK4I itself (stdio) | `mak4i serve` |
| local MCP clients that connect by URL | `mak4i serve --transport http` |
| a cloud-hosted AI client (e.g. Claude.ai) through an HTTPS tunnel | `mak4i serve --transport http --host 0.0.0.0`, then see "Exposing a local server to a cloud client" |

`mak4i init` finishes by offering two ways to serve the environment it
created. See "After `mak4i init`: choose stdio or Streamable HTTP" below.
To stop, reinstall, upgrade, reset or remove MAK4I, see
[Section 4](#section-4--stop-reinstall-reset-and-uninstall).

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

### After `mak4i init`: choose stdio or Streamable HTTP

A successful `init` ends with two ways to start the local MCP server:

| | Option 1 — stdio (default) | Option 2 — Streamable HTTP |
|---|---|---|
| Command | `mak4i serve` | `mak4i serve --transport http` |
| When to use it | The simple local mode: an MCP client launches MAK4I as its own process and talks to it over stdin/stdout. | One running local server that **multiple local MCP clients** share, each connecting by URL. |
| How a client connects | `claude mcp add mak4i -- mak4i serve` (the client starts the server itself) | MCP `http://127.0.0.1:<port>/mcp` with header `Authorization: Bearer <token>` (the token is in `.mak4i/credentials.json`). Also `/health` and `/ready` on the same port. `init` prints these URLs with your actual port. |
| Network | none (not a listening endpoint) | loopback only (`127.0.0.1`), on your Local HTTP port (chosen at `init`, default **9090**) |

**Both options use the same initialized `.mak4i/` environment:** the same
organization, owner principal, project, credential, SQLite control plane,
and artifact store. Knowledge written through one is visible through the
other. They are **transport choices**, not different storage or
deployment modes. Switching between them needs no re-initialization.
Stop one server and start the other.

`mak4i serve` with no flag stays **stdio**. Details for each: "`mak4i
serve`" and "Local Streamable HTTP testing" below. (A shared server for
*remote* clients and other people is a different thing, Enterprise
Self-Hosted. See the end of Section 3.)

### Choosing the Local HTTP port

`mak4i init` asks for the port Option 2 listens on. Press Enter for the
default, **9090**:

```
Organization name: WD_Tech_Soln
Your display name: WD Technology Solutions
First project name: Milo
Local HTTP port [9090]: 9095
```

Non-interactively, pass `--http-port` (omit it for 9090):

```bash
mak4i init --org-name WD_Tech_Soln --display-name "WD Technology Solutions" \
  --project-name Milo --http-port 9095
```

The value must be a whole number from 1 to 65535. An invalid value is
rejected with a clear message: interactively you're asked again; with
`--http-port`, `init` stops before creating anything.

**The port is remembered.** It's saved as `http_port` in
`.mak4i/config.json`, so every later `mak4i serve --transport http`
listens there with no flag. With the example above, that's
`http://127.0.0.1:9095/mcp`. It has no effect on stdio.

**Overriding it for one run:**

| To | Do |
|---|---|
| use another port for this run only | `mak4i serve --transport http --port 9100`. The saved port is **not** changed; the next plain `mak4i serve --transport http` uses 9095 again. |
| override it from the environment | `MAK4I_PORT=9200 mak4i serve --transport http` |
| change it permanently | edit `"http_port"` in `.mak4i/config.json` |

Precedence for `mak4i serve --transport http`: `--port` → `MAK4I_PORT` →
the saved `http_port` → 9090. The generic `PORT` variable (a
container-platform convention) is not used by `mak4i serve`; if it's set
in your shell, `serve` notes that it's ignoring it.

A config created before this setting existed has no `http_port`. It keeps
working as-is and uses 9090; there's no need to re-run `init`.

**If the port is already in use**, `serve` stops without starting and
without choosing a different port. MCP clients are configured with a
fixed URL, so a silently changed port would break them:

```
ERROR: MAK4I could not start.

127.0.0.1:9095 is already in use.

Stop the process using that port or choose another port:

  mak4i serve --transport http --port 9096
```

### Where the local configuration is stored

| Path | Contents | Mode |
|---|---|---|
| `.mak4i/config.json` | organization / principal / project / credential **ids and names** + resolved backend settings + the Local HTTP port (`http_port`) | 0644 — no secret |
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
Port: 9090
MCP endpoint: http://127.0.0.1:9090/mcp
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:9090 (Press CTRL+C to quit)

MCP server ready.
```

(Shown with the default port; yours is whatever `init` saved.) "MCP
server ready." appears only once the server has actually bound the port
and started.

This is the **same** `MAK4IEngine`, the **same** six tools, and the
**same** credential-based authentication as stdio — only the transport
differs. `--host` (or `MAK4I_HOST`) overrides the loopback default; the
port comes from "Choosing the Local HTTP port" above. `'http'` and
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
curl http://127.0.0.1:9090/health   # -> ok      (use your Local HTTP port)
curl http://127.0.0.1:9090/ready    # -> ready
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
cloudflared tunnel --url http://127.0.0.1:9090   # your Local HTTP port
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
   `http://127.0.0.1:9090/mcp` instead of `http://127.0.0.1:9090`) is
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
client that can launch a stdio server works. (To share one running local
server between several clients by URL instead, use Option 2 in "After
`mak4i init`: choose stdio or Streamable HTTP".) With **Claude Code**, from
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
| `mak4i_create` | write new durable knowledge (optional `subject_key` names the subject it decides) |
| `mak4i_supersede` | replace current knowledge, preserving lineage + history (`release_subject_key=true` gives up the lineage's subject claim) |
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

A shared instance that several people and remote AI clients connect to is
**Enterprise Self-Hosted**, and it's set up differently from this local
path:

- **One Linux VM:** follow
  [`ENTERPRISE_SELF_HOSTED.md`](ENTERPRISE_SELF_HOSTED.md). It uses
  Docker Compose with PostgreSQL, automatic migrations, persistent
  volumes (`LocalJSONStore` works fine there), and optional automatic
  HTTPS.
- **Any other platform** (managed PostgreSQL, object storage, your own
  container platform): [`DEPLOYMENT.md`](DEPLOYMENT.md) has the runtime
  contract and production deployment options.

Don't build a shared instance from `mak4i init` / `mak4i serve`. Both are
bound to this machine's local `.mak4i/` environment: `init` always
provisions a local SQLite control plane, and `serve` uses that local
environment even if `MAK4I_CONTROL_PLANE_DB` is exported. A shared
deployment runs the server directly (`python -m mak4i.mcp_server`, the
container's default command) and is bootstrapped with the granular
commands from this section, pointed at its own database.

SQLite / PostgreSQL / GCS are reference choices, not MAK4I protocol
requirements — any `ControlPlaneStore` / `ArtifactStore` implementation
works.

---

# Section 4 — Stop, reinstall, reset and uninstall

These are separate operations; pick the one you need. Run them from the
repository directory (`mak4i-reference`) unless a step says otherwise.
Commands are given for macOS/Linux and for Windows PowerShell separately.
Never paste the macOS/Linux commands into PowerShell.

**Where your local data lives.** Everything `mak4i init` created is inside
`.mak4i/` in the repository directory (or the directory named by
`MAK4I_HOME`, if you set it):

| Path | What it is |
|---|---|
| `.mak4i/control-plane.db` | the local control-plane database: organizations, principals, projects, grants, credential hashes |
| `.mak4i/artifacts/` | every artifact, including superseded versions (lineage and history) |
| `.mak4i/config.json`, `.mak4i/credentials.json` | local config and the raw local credential |

If you ever used the granular commands in Section 3 *without* `mak4i init`,
their data is in `mak4i-control-plane.db` and `artifacts/local/` in the
directory you ran them from. Local MAK4I uses no `.env` file and no Docker
volumes.

**Windows: release file locks first.** Windows won't delete files that are
in use. Before deleting `.venv`, `.mak4i` or the repository:

1. Stop `mak4i serve` (Ctrl+C in its window).
2. Quit every MCP client that launches MAK4I itself over stdio (e.g. Claude
   Code started with `claude mcp add mak4i -- mak4i serve`), since it keeps
   its own `mak4i serve` process running.
3. Close other terminals that activated this `.venv` or are running `uv`.
   To see which Python/uv processes are still running:
   `Get-Process python, uv -ErrorAction SilentlyContinue`.
4. Run `deactivate` in the current window if the environment is active.
5. To delete the repository itself, move to its parent directory first
   (step 5 below).

### 1. Stop MAK4I (deletes nothing)

Press **Ctrl+C** in the window running `mak4i serve`. For a stdio client,
quitting the client stops its `mak4i serve`. All data in `.mak4i/` is kept;
start again with `mak4i serve` at any time.

### 2. Remove and recreate only `.venv`

Deletes **only** the Python environment (`.venv/`). It does not touch
`.mak4i/`: organizations, principals, projects, grants, credentials,
artifacts and history are unaffected, and MCP client registrations keep
working.

macOS / Linux:

```bash
deactivate 2>/dev/null || true
rm -rf .venv
uv sync --extra dev --no-editable
source .venv/bin/activate
```

Windows PowerShell (after releasing file locks, above):

```powershell
if (Get-Command deactivate -ErrorAction SilentlyContinue) { deactivate }
Remove-Item -Recurse -Force .\.venv
uv sync --extra dev --no-editable
.\.venv\Scripts\Activate.ps1
```

### 3. Reinstall or upgrade while keeping your data

`.mak4i/` is not part of the repository (it's gitignored), so switching the
checkout to a newer release keeps all your data. For a clone made with
`--branch <tag> --depth 1`, fetch the new release tag explicitly. Stop MAK4I
first (step 1).

macOS / Linux:

```bash
git fetch --depth 1 origin tag v0.1.0-rc.3
git checkout v0.1.0-rc.3
uv sync --extra dev --no-editable
source .venv/bin/activate
mak4i --version
```

Windows PowerShell:

```powershell
git fetch --depth 1 origin tag v0.1.0-rc.3
git checkout v0.1.0-rc.3
uv sync --extra dev --no-editable
.\.venv\Scripts\Activate.ps1
mak4i --version
```

If `uv sync` reports files in use on Windows, release the file locks and
recreate `.venv` (step 2). Your `.mak4i/` data and MCP client registrations
keep working after an upgrade.

Downgrading is not always possible: an artifact written with a
`subject_key` (new in v0.1.0-rc.3) can't be read by v0.1.0-rc.2. Artifacts
without one remain readable by both.

### 4. Remove the MCP registration from each client

This only changes the client's own configuration; no MAK4I data is
deleted. Use the name you registered (these examples use `mak4i`). The
commands are the same in every shell.

macOS / Linux:

```bash
claude mcp remove mak4i                               # Claude Code (add -s local|user|project to target one scope)
codex mcp remove mak4i                                # Codex CLI
grok mcp remove mak4i                                 # Grok CLI (add -s <scope> to target one scope)
npx -y @google/gemini-cli mcp remove -s user mak4i    # Gemini CLI, if added with -s user as in docs/DEMO.md
```

Windows PowerShell:

```powershell
claude mcp remove mak4i
codex mcp remove mak4i
grok mcp remove mak4i
npx -y @google/gemini-cli mcp remove -s user mak4i
```

Gemini CLI removes from the **project** scope unless you pass `-s user`, so
use the same scope you added with. Claude.ai and Cowork have no removal
command: remove the connector in **Customize → Connectors**.

### 5. Remove the cloned repository

**Destructive.** Deletes the whole `mak4i-reference` directory, including
`.venv/` and, **by default, your local MAK4I data in `.mak4i/`**:
organizations, principals, projects, grants, credentials, the control-plane
database, and all artifacts with their lineage and history. If you want to
keep the data, back up `.mak4i/` first (step 6). Remove the MCP
registrations first (step 4): they point at a server that will no longer
exist. On Windows, release file locks first.

Run this from the directory that **contains** `mak4i-reference`, not from
inside it.

macOS / Linux:

```bash
cd ..
rm -rf mak4i-reference
```

Windows PowerShell:

```powershell
Set-Location ..
Remove-Item -Recurse -Force .\mak4i-reference
```

### 6. Reset local MAK4I data

**Destructive.** Deletes `.mak4i/`: the organization, principals, projects,
grants and credentials in the local control-plane database, and every
artifact with its complete lineage and history. It does not touch `.venv/`
or the repository. Afterwards, every MCP client registration still points at
the old credential and fails to authenticate until you re-register it with
the new one. Stop MAK4I and release file locks first.

Back up first if there's any chance you'll need the data. Keep the copy
**outside** the repository so it can't be committed or deleted with it:

macOS / Linux:

```bash
cp -R .mak4i ../mak4i-local-backup
```

Windows PowerShell:

```powershell
Copy-Item -Recurse .\.mak4i ..\mak4i-local-backup
```

Then delete the data and create a fresh environment:

macOS / Linux:

```bash
rm -rf .mak4i
mak4i init
```

Windows PowerShell:

```powershell
Remove-Item -Recurse -Force .\.mak4i
mak4i init
```

`mak4i init --force` is **not** a reset: it creates a second, new
organization, project and credential alongside the old ones in the same
database and repoints the local config at them. The old data stays in the
database, just no longer in use.
