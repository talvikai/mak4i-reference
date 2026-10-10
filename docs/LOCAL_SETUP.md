# Local Developer Setup

The one guide for running MAK4I on your own machine: the engine, the CLI
and a local MCP server, with no cloud account and nothing shared with
anyone else. It covers every local runtime option, from installation to
removal.

> **Developer Preview.** This is a release candidate (`v0.1.0-rc.5`) for
> evaluation and feedback, not a production-supported release.
>
> For a shared server that other people and remote AI clients connect to,
> use [`ENTERPRISE_SELF_HOSTED.md`](ENTERPRISE_SELF_HOSTED.md) instead.

## Contents

1. [Supported systems and prerequisites](#1-supported-systems-and-prerequisites)
2. [Install](#2-install)
3. [Initialize](#3-initialize)
4. [Run the server](#4-run-the-server)
5. [Connect an AI client](#5-connect-an-ai-client)
6. [Health and readiness](#6-health-and-readiness)
7. [Stop, start and restart](#7-stop-start-and-restart)
8. [Credential recovery](#8-credential-recovery)
9. [Upgrade](#9-upgrade)
10. [Backup and restore](#10-backup-and-restore)
11. [Uninstall](#11-uninstall)
12. [Manual and advanced setup](#12-manual-and-advanced-setup)
13. [Configuration reference](#13-configuration-reference)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Supported systems and prerequisites

| | |
|---|---|
| Operating systems | macOS and Linux (tested). Windows 10/11 with PowerShell: the commands are given separately below and have been syntax-checked, but haven't yet been run on Windows. |
| Git | any recent version |
| uv | <https://docs.astral.sh/uv/getting-started/installation/>. You don't install Python yourself: the project pins Python 3.13 and `uv` downloads it. MAK4I supports Python 3.11+. |
| An MCP-capable AI client | for example Claude Code (section 5) |

Everything MAK4I stores locally lives in a gitignored `.mak4i/` directory
inside the repository.

## 2. Install

macOS / Linux:

```bash
git clone --branch v0.1.0-rc.5 --depth 1 https://github.com/talvikai/mak4i-reference.git
cd mak4i-reference
uv sync --extra dev --no-editable
source .venv/bin/activate
mak4i --version
```

Windows PowerShell:

```powershell
git clone --branch v0.1.0-rc.5 --depth 1 https://github.com/talvikai/mak4i-reference.git
Set-Location mak4i-reference
uv sync --extra dev --no-editable
.\.venv\Scripts\Activate.ps1
mak4i --version
```

Expected: `mak4i 0.1.0rc5`. Git may note that the tag "is not a commit"
while cloning; that's normal for an annotated release tag.

**Why `--no-editable`.** `uv`'s editable install marks its generated
`.pth` file hidden on macOS, and Python silently skips hidden `.pth`
files, which intermittently breaks `import mak4i`. A non-editable install
has no `.pth` file. Two consequences:

1. A bare `uv run <cmd>` re-syncs to an editable install. Activate the
   environment and call `mak4i` directly, or keep passing `--no-editable`.
2. After adding a source file, `git add` it and run
   `uv sync --extra dev --no-editable --reinstall-package mak4i`
   (the build only includes version-controlled files).

Optional check: `uv run --no-sync pytest` (all green; the GCS integration
tests skip by design).

## 3. Initialize

```bash
mak4i init
```

It asks for an organization name, your display name, a first project name
and the Local HTTP port (press Enter for 9090), or takes them as flags:

```bash
mak4i init --org-name WD_Tech_Soln --display-name "WD Technology Solutions" \
  --project-name Milo --http-port 9095
```

It creates, in order, all in your own local database:

```
Organization → Owner principal (role: owner) → Project → read/write grant → Credential
```

| Path | Contents | Mode |
|---|---|---|
| `.mak4i/config.json` | IDs and names, backend settings, the Local HTTP port | 0644, no secret |
| `.mak4i/credentials.json` | `{"token": "mak4i_…"}`, the raw local credential | **0600** |
| `.mak4i/control-plane.db` | SQLite control plane (stores only the credential's hash) | |
| `.mak4i/artifacts/` | your artifacts, with their history | directory 0700 |

Set `MAK4I_HOME` to keep `.mak4i/` somewhere else.

**Running `init` again** doesn't overwrite anything: it prints the
existing environment (including your owner principal ID) and exits.

**`mak4i init --force` doesn't reuse, repair or reset your environment.**
It runs a full first-time setup again: a **new** organization, owner
principal, project, grant and credential in the same local database, and
it rewrites `.mak4i/config.json` and `credentials.json` to point at them.
Nothing is deleted, but the new identity can't see your existing projects,
so their context disappears from view. To recover a credential, see
[Credential recovery](#8-credential-recovery); to start over, see
[Reset local data](#113-reset-local-data).

**The Local HTTP port** is saved as `http_port` in `.mak4i/config.json`,
and every later `mak4i serve --transport http` uses it. It has no effect on
stdio. A config from before this setting existed uses 9090.

## 4. Run the server

All modes serve the same `.mak4i/` environment: the same organization,
project, credential and stored context. Stop one (Ctrl+C) and start
another at any time; nothing needs re-initializing.

| You're connecting | Mode | Command |
|---|---|---|
| a local MCP client that launches MAK4I itself | [stdio](#41-stdio) | `mak4i serve` |
| local MCP clients that connect by URL | [local HTTP](#42-local-http) | `mak4i serve --transport http` |
| clients on other machines, or a cloud AI client through a tunnel | [network HTTP](#43-network-http-and-tunnels) | `mak4i serve --transport http --host 0.0.0.0` |

### 4.1 stdio

```bash
mak4i serve
```

```
MAK4I local server starting...
Organization: My Org
Project: Demo Project
Transport: stdio

MCP server ready.
```

The banner goes to stderr; stdout carries the MCP protocol. There's no
port and no URL: the client starts `mak4i serve` itself (section 5).
Running `serve` before `init` fails clearly; it never initializes on its
own. `.mak4i/` is authoritative: a stale `MAK4I_TOKEN` or
`MAK4I_CONTROL_PLANE_DB` in your shell is ignored, with a one-line notice.

### 4.2 Local HTTP

```bash
mak4i serve --transport http
```

```
Transport: Streamable HTTP
Host: 127.0.0.1
Port: 9090
MCP endpoint: http://127.0.0.1:9090/mcp
...
MCP server ready.
```

"MCP server ready." appears only once the port is actually bound. Same
engine, tools and credential authentication as stdio; it listens on
**loopback only**, so only this machine can connect.

| To | Do |
|---|---|
| use another port once | `mak4i serve --transport http --port 9100` (the saved port is unchanged) |
| override from the environment | `MAK4I_PORT=9200 mak4i serve --transport http` |
| change it permanently | edit `"http_port"` in `.mak4i/config.json` |

Precedence: `--port` → `MAK4I_PORT` → the saved port → 9090. `mak4i serve`
ignores the generic `PORT` variable. If the port is in use, `serve` stops
with a clear message instead of silently picking another port (clients
are configured with a fixed URL).

### 4.3 Network HTTP and tunnels

A cloud-hosted AI client (Claude.ai, Cowork, Gemini and similar) runs on
someone else's infrastructure and can't reach `127.0.0.1` on your machine.
To reach a local server from elsewhere, it must listen beyond loopback:

```bash
mak4i serve --transport http --host 0.0.0.0
```

> **Security warning.** `--host 0.0.0.0` exposes MAK4I on **every network
> interface** of your machine over **plain, unencrypted HTTP**, and your
> bearer credential travels in every request. Binding to `0.0.0.0`
> provides no security by itself; credential authentication still applies,
> but the traffic isn't protected.
> - Use it only on a trusted network, or with a host firewall that blocks
>   the port from other machines.
> - Give remote clients only an HTTPS URL (a tunnel or TLS proxy), never
>   `http://<your-ip>:<port>`.
> - Stop the server, or restart it without `--host`, when you're done.
> - For anything beyond a short test, use
>   [Enterprise Self-Hosted](ENTERPRISE_SELF_HOSTED.md), which puts MAK4I
>   behind HTTPS.

**HTTPS tunnel for a cloud client (development and testing only).** For
example, Cloudflare's account-less Quick Tunnel, alongside the server
above:

```bash
cloudflared tunnel --url http://127.0.0.1:9090    # your Local HTTP port
```

It prints a temporary `https://….trycloudflare.com` hostname; configure
your cloud client with `https://<hostname>/mcp` and the bearer header.
Details that matter:

1. **`--host 0.0.0.0` is required while tunneling.** On loopback, MAK4I
   keeps DNS-rebinding protection on, which accepts only `Host` headers
   naming `127.0.0.1`/`localhost`. A tunnel forwards its own public
   hostname, which then fails with `421 Misdirected Request`.
2. **The tunnel's `--url` is `http://127.0.0.1:<port>`, with no path.** It
   is where the tunnel connects, not what MAK4I listens on; a path there
   gets appended a second time.
3. **The Quick Tunnel hostname changes on every tunnel restart.** Update
   the client's URL; nothing changes on the MAK4I side, and the credential
   keeps working.

Tunnels aren't a MAK4I dependency or a production architecture. For a
stable address, use a named tunnel, your own TLS reverse proxy, or
Enterprise Self-Hosted.

## 5. Connect an AI client

**Claude Code over stdio** (from the repository directory, with the
environment active):

```bash
claude mcp add mak4i -- mak4i serve
claude mcp get mak4i          # expect: ✔ connected
```

**Clients that connect by URL** (local HTTP, or a tunnel): endpoint
`http://127.0.0.1:<port>/mcp` (or the tunnel's `https://…/mcp`) with the
header `Authorization: Bearer <token>`. The token is in
`.mak4i/credentials.json`. Prefer clients that read it from a file or
environment variable, so it doesn't end up in your shell history. Each
client's exact steps: [`DEMO.md` → Connecting a client](DEMO.md#connecting-a-client).

The tools:

| Tool | Purpose |
|---|---|
| `mak4i_whoami` | which MAK4I connection this is (name, environment, version), your organization and principal |
| `mak4i_list_projects` | the projects your credential can act on, with permissions and connection |
| `mak4i_search` | raw candidate lookup by type, tags and status |
| `mak4i_get_current` | the resolved current knowledge (conflict- and integrity-checked) |
| `mak4i_create` | write new durable knowledge (optional `subject_key` names the subject it decides) |
| `mak4i_supersede` | replace current knowledge, keeping lineage and history (`release_subject_key=true` gives up the lineage's subject claim) |
| `mak4i_history` | every version in a lineage, oldest first |

**Verify:** ask your client to (1) list MAK4I projects (your project with
`["read","write"]`), (2) create an artifact in it, (3) get the current
knowledge (that artifact, `conflicts: []`), and (4) supersede it and show
the history (both versions).

**Several MAK4I connections.** A local server names itself
`MAK4I local (<organization>)`, environment `local`. Set
`MAK4I_INSTANCE_NAME` / `MAK4I_ENVIRONMENT` before `mak4i serve` to name it
yourself. Clients see the name in `mak4i_whoami`, project listings and
access denials. Review writes your client proposes when it has several
MAK4I connections: a server can't control what a client does on another
connection.

## 6. Health and readiness

With the HTTP transport running:

| Endpoint | Auth | Meaning |
|---|---|---|
| `GET /health` | none | the process is up |
| `GET /ready` | none | the control plane is reachable |
| `POST /mcp` | `Authorization: Bearer <credential>` | the MCP endpoint |

```bash
curl http://127.0.0.1:9090/health   # -> ok      (use your Local HTTP port)
curl http://127.0.0.1:9090/ready    # -> ready
```

stdio has no endpoints; a client that shows MAK4I as connected is the
check.

## 7. Stop, start and restart

- **Stop:** Ctrl+C in the window running `mak4i serve`. For a stdio
  client, quitting the client stops its `mak4i serve`. Nothing is deleted.
- **Start / restart:** run `mak4i serve` (with the same options) again.

A credential is a row in the local database, not something held in
memory: stopping and restarting the server, or restarting a tunnel,
doesn't invalidate it. It stays valid until it's revoked or expires.

## 8. Credential recovery

Your environment stays intact in every case below. Don't use
`mak4i init --force`: it creates a separate new organization instead.

**`.mak4i/credentials.json` is missing.** `mak4i serve` stops and prints
your organization, project and owner principal, plus the exact command.
Issue a replacement credential for the existing owner:

```bash
mak4i credential issue --actor <owner_principal_id> --principal-id <owner_principal_id>
```

Save the printed token to `.mak4i/credentials.json` as
`{"token": "<the printed token>"}` (readable only by you), then run
`mak4i serve` again.

**A client gets `401`.** The token it uses is missing, wrong, expired or
revoked. Run `mak4i init` (without `--force`) to print your owner principal
ID, issue a new credential as above, and update the client. If
`mak4i serve` should use it too (stdio clients), also save it to
`.mak4i/credentials.json`. Revoke a leaked token with
`mak4i credential revoke --actor <owner_principal_id> --credential-id cred_…`.

## 9. Upgrade

`.mak4i/` isn't part of the repository, so switching the checkout to a new
release keeps all your data. Stop MAK4I first.

macOS / Linux:

```bash
git fetch --depth 1 origin tag v0.1.0-rc.5
git checkout v0.1.0-rc.5
uv sync --extra dev --no-editable
source .venv/bin/activate
mak4i --version
```

Windows PowerShell:

```powershell
git fetch --depth 1 origin tag v0.1.0-rc.5
git checkout v0.1.0-rc.5
uv sync --extra dev --no-editable
.\.venv\Scripts\Activate.ps1
mak4i --version
```

Expected: `mak4i 0.1.0rc5`. Your data and MCP client registrations keep
working. If `uv sync` reports files in use on Windows, release the file
locks ([Uninstall](#11-uninstall)) and recreate `.venv`
([Recreate the Python environment](#111-recreate-the-python-environment)).

Downgrading isn't always possible: an artifact written with a
`subject_key` (new in v0.1.0-rc.3) can't be read by v0.1.0-rc.2. RC4 and RC5
add no change to stored data.

## 10. Backup and restore

Stop MAK4I, then copy `.mak4i/` **outside** the repository, so it can't be
committed or deleted along with it.

macOS / Linux:

```bash
cp -R .mak4i ../mak4i-local-backup
```

Windows PowerShell:

```powershell
Copy-Item -Recurse .\.mak4i ..\mak4i-local-backup
```

The copy contains your raw local credential (`credentials.json`); keep it
private. To restore, stop MAK4I and put the copy back as `.mak4i/`
(replacing the current one), then run `mak4i serve`.

## 11. Uninstall

Pick the operation you need. Run them from the repository directory
unless a step says otherwise, and never paste the macOS/Linux commands
into PowerShell.

**What each one keeps:**

| Operation | Deletes | Keeps |
|---|---|---|
| [Recreate the Python environment](#111-recreate-the-python-environment) | `.venv/` only | all data, client registrations |
| [Remove client registrations](#112-remove-client-registrations) | client configuration only | all data |
| [Reset local data](#113-reset-local-data) | `.mak4i/` (all local data) | the repository and `.venv/` |
| [Remove everything](#114-remove-everything) | the repository, including `.venv/` and `.mak4i/` | backups you made outside it |

**Windows: release file locks first.** Windows won't delete files in use:
stop `mak4i serve`, quit every client that launches MAK4I over stdio,
close other terminals using this `.venv`
(`Get-Process python, uv -ErrorAction SilentlyContinue` lists what's
running), run `deactivate` if the environment is active, and move out of
the repository before deleting it.

### 11.1 Recreate the Python environment

macOS / Linux:

```bash
deactivate 2>/dev/null || true
rm -rf .venv
uv sync --extra dev --no-editable
source .venv/bin/activate
```

Windows PowerShell:

```powershell
if (Get-Command deactivate -ErrorAction SilentlyContinue) { deactivate }
Remove-Item -Recurse -Force .\.venv
uv sync --extra dev --no-editable
.\.venv\Scripts\Activate.ps1
```

### 11.2 Remove client registrations

Only the client's configuration changes; no MAK4I data is deleted. Use the
name you registered. The commands are the same in every shell:

```bash
claude mcp remove mak4i                               # Claude Code (add -s local|user|project for one scope)
codex mcp remove mak4i                                # Codex CLI
grok mcp remove mak4i                                 # Grok CLI (add -s <scope> for one scope)
npx -y @google/gemini-cli mcp remove -s user mak4i    # Gemini CLI, if added with -s user
```

Gemini CLI removes from the project scope unless you pass the scope you
added with. Claude.ai and Cowork: remove the connector in
**Customize → Connectors**.

### 11.3 Reset local data

> **Permanently deletes** `.mak4i/`: the organization, principals,
> projects, grants and credentials, and every artifact with its history.
> Back up first ([Backup and restore](#10-backup-and-restore)) if there's
> any chance you'll need it. Stop MAK4I first.

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

Afterwards every client registration still holds the old credential and
fails until you update it with the new one.

### 11.4 Remove everything

> **Permanently deletes** the whole `mak4i-reference` directory, including
> `.venv/` and **your local data in `.mak4i/`**. Back up `.mak4i/` first to
> keep the data, and remove the client registrations (11.2) first: they'd
> point at a server that no longer exists.

Run it from the directory that **contains** `mak4i-reference`.

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

## 12. Manual and advanced setup

`mak4i init` is a convenience wrapper over the same control-plane
commands, which remain the right tool for several organizations, extra
principals, scripting or understanding the model. Each command's output
feeds the next:

```bash
# 1. Organization and its owner. Prints {"organization": {...}, "owner": {...}}.
mak4i org create --name "My Org" --owner-display-name "Me"

# 2. A project (--actor: the owner's principal_id; --organization-id: from step 1).
mak4i project create --actor <owner_principal_id> --organization-id <organization_id> --name "My Project"

# 3. A read/write grant (use --permissions read for read-only).
mak4i grant create --actor <owner_principal_id> \
  --principal-id <owner_principal_id> --project-id <project_id> --permissions read,write

# 4. A credential. Prints the raw token once.
mak4i credential issue --actor <owner_principal_id> --principal-id <owner_principal_id>

# 5. Run the server directly with that token (stdio).
MAK4I_TOKEN=<the raw token> python -m mak4i.mcp_server
```

Without `mak4i init`, `MAK4I_CONTROL_PLANE_DB` defaults to
`sqlite:///./mak4i-control-plane.db` in the current directory. A new
database needs its schema: `MAK4I_CONTROL_PLANE_CREATE_TABLES=1` for a
throwaway file, or `uv run alembic upgrade head` for one you keep.

More commands, all with `--actor <owner_principal_id>`, owner-only and
scoped to your own organization:

| Command | Purpose |
|---|---|
| `mak4i principal create --organization-id <id> --type human\|service\|agent --display-name <name> [--role member\|owner]` | add a principal |
| `mak4i grant revoke --principal-id <id> --project-id <id>` | remove a grant |
| `mak4i credential revoke --credential-id <id>` | revoke a credential immediately |
| `mak4i org list` / `org show --organization-id <id>` | organizations (trusted-operator scope) |
| `mak4i project list --organization-id <id>` / `project show --project-id <id>` | projects |
| `mak4i principal list --organization-id <id>` / `principal show --principal-id <id>` | principals |
| `mak4i grant list --principal-id <id>` | a principal's grants |
| `mak4i credential list --principal-id <id>` | a principal's credentials (never the token or its hash) |

`credential issue` also prints ready-to-copy connect instructions
(`--no-connection-help` suppresses them).

**Operator artifact commands.** Without a running server or a credential,
using `--principal <id>` (a trusted, local-only mode, audited as
`operator_impersonation` and never exposed over MCP):

```bash
mak4i doctor      --principal <id> --project <project_id>
mak4i create      --principal <id> --project <project_id> --artifact-id … --artifact-type … --title … --content …
mak4i supersede   --principal <id> --project <project_id> --old-id … --content … --reason …
mak4i search      --principal <id> --project <project_id> --tags …
mak4i get-current --principal <id> --project <project_id> --tags …
mak4i history     --principal <id> --project <project_id> --lineage-id …
```

After `mak4i init`, the IDs are in `.mak4i/config.json`.

## 13. Configuration reference

The one table of Local settings. `mak4i serve` sets the backend variables
from `.mak4i/`, so you normally set none of them.

| Variable | Meaning |
|---|---|
| `MAK4I_HOME` | where `.mak4i/` lives (default: `./.mak4i`) |
| `MAK4I_TRANSPORT` | `stdio` (default) or `http` / `streamable-http`; `--transport` wins |
| `MAK4I_HOST` | HTTP bind address (default `127.0.0.1`); `--host` wins. See the [security warning](#43-network-http-and-tunnels) before using anything else. |
| `MAK4I_PORT` | HTTP port for one run; `--port` wins; otherwise the saved port, then 9090 |
| `MAK4I_INSTANCE_NAME`, `MAK4I_ENVIRONMENT` | how this server names itself to AI clients (default `MAK4I local (<organization>)` / `local`) |
| `MAK4I_TOKEN` | the credential for `python -m mak4i.mcp_server` over stdio (manual setup); ignored by `mak4i serve` |
| `MAK4I_CONTROL_PLANE_DB`, `MAK4I_STORE`, `MAK4I_LOCAL_STORE_DIR`, `MAK4I_CONTROL_PLANE_CREATE_TABLES` | backends for the manual setup (section 12); ignored by `mak4i serve` |

## 14. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "MAK4I has not been initialized locally." | Run `mak4i init`. |
| "Local credential file missing" | See [Credential recovery](#8-credential-recovery). |
| `curl: (7) Failed to connect` | The server isn't running, or the port is wrong: check the banner's `Port:` line. |
| `401 {"error":"unauthorized"}` | Missing, wrong, expired or revoked credential. See [Credential recovery](#8-credential-recovery). |
| A working credential suddenly returns `401` after changing directories | `mak4i serve` uses the `.mak4i/` in its current directory. Two directories mean two separate environments with different credentials (their banners can look identical). |
| `421 Misdirected Request` while tunneling | The server is on loopback; restart with `--host 0.0.0.0` after reading its [security warning](#43-network-http-and-tunnels). |
| A cloud client can't reach your endpoint | It can't reach `127.0.0.1` on your machine: use a tunnel (section 4.3) or Enterprise Self-Hosted. |
| A cloud client asks how the server "signs in" | MAK4I doesn't implement OAuth by design; use the client's custom-header setup and enter `Authorization: Bearer <credential>`. |
| `access denied … on MAK4I connection '…'` | Your principal has no grant (or only `read`) on that project here. It's final for this connection: don't write the same thing to another MAK4I connection without choosing it deliberately. |
| `ERROR: MAK4I could not start. 127.0.0.1:<port> is already in use.` | Stop the other process, or use `--port`. |
