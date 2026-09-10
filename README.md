# MAK4I Reference Implementation

A technology-agnostic engine that lets durable project knowledge
(architecture decisions, documentation facts, implementation state)
created or changed while working with one AI client become discoverable
and usable by another, without the user repeating themselves.

The engine (`src/mak4i/`) contains no database-, caching-, or
provider-specific logic; the demo scenarios (a Redis caching decision, a
PostgreSQL→MySQL evolution) are acceptance tests, not special cases baked
into the core.

> **This repository is the MAK4I *reference implementation*.** The MAK4I
> protocol and specification are maintained separately at
> <https://github.com/talvikai/mak4i-protocol>.
>
> MAK4I is an open, tool-neutral protocol. This is *one* implementation of
> it — it does not define the protocol, and other independent
> implementations are possible. It runs locally / self-hosted, and the
> same core also underpins Talvik's hosted MAK4I where safe to share.

## The problem it solves

You make a decision with one AI assistant — "we're using MySQL for the
primary database, not PostgreSQL, because of managed-hosting
availability." A week later you open a different assistant to do
implementation work. It has no idea. You re-explain, or worse, it guesses
and you get PostgreSQL-shaped code.

MAK4I gives that knowledge a home outside any one client: a durable
**artifact** with its rationale and history, written through one client's
tool call and retrieved through another's — the second client gets the
current decision *and* the trail of how it got there, and surfaces a
disagreement with the local code instead of silently picking a side.

## Prerequisites

| Need | Why |
|---|---|
| **Git** | to clone the repository |
| **[uv](https://docs.astral.sh/uv/getting-started/installation/)** | dependency + environment manager (also fetches Python) |
| **An MCP-capable AI client** | for the final integration test — e.g. Claude Code (`claude`) |

Install uv with your OS package manager or its one-line installer — see
its [installation docs](https://docs.astral.sh/uv/getting-started/installation/).

You do **not** need to install Python yourself — this project pins Python
3.13 (`.python-version`), and `uv sync` downloads it automatically if it
isn't already present. MAK4I supports Python 3.11+ (`requires-python =
">=3.11"`).

## Quick Start

```
Clone  →  Install  →  Initialize  →  Start MAK4I  →  Connect AI client  →  Use MAK4I
```

```bash
# 1. Clone
git clone https://github.com/talvikai/mak4i-reference.git
cd mak4i-reference

# 2. Install (uv fetches Python 3.13 if needed)
uv sync --extra dev --no-editable

# 3. Activate the environment
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1

# 4. Initialize your local MAK4I environment (prompts for a few names)
mak4i init

# 5. Start the MCP server
mak4i serve
```

`mak4i init` creates a self-contained local MAK4I instance for you — an
organization, an owner principal, a project, a read/write grant, and a
credential — and saves a local config so `serve` needs nothing further.
It all lives in a gitignored `.mak4i/` directory in the repo. You never
copy an org id, project id, or token between commands.

`mak4i serve` then starts the existing MCP server (stdio transport by
default) against that instance, in the foreground — Ctrl-C to stop.

> Activating the venv (`source .venv/bin/activate`) and calling `mak4i`
> directly is the supported path. Bare `uv run mak4i …` can re-trigger an
> editable install that CPython's `site.py` mishandles on macOS — see
> `docs/LOCAL_SETUP.md`.

## Connect Claude Code

From the repository directory (where `.mak4i/` lives), with the venv
active:

```bash
claude mcp add mak4i -- mak4i serve
```

This registers a stdio MCP server that Claude Code launches on demand.
Check it and list the tools:

```bash
claude mcp get mak4i          # should report: ✔ connected
```

In a Claude Code session, the MAK4I tools are now available:
`mak4i_list_projects`, `mak4i_search`, `mak4i_get_current`,
`mak4i_create`, `mak4i_supersede`, `mak4i_history`.

## Verify MAK4I

Ask Claude Code to run these (or call the tools directly). All four steps
should succeed:

1. **Connection + project visible** — `mak4i_list_projects` returns the
   one project `mak4i init` created, with `permissions: ["read", "write"]`.
2. **Create context** — `mak4i_create` with that `project`, an
   `artifact_id`, `artifact_type: "decision"`, a `title`, and
   `content` — returns the new artifact, `created_by` = your principal id.
3. **Retrieve current** — `mak4i_get_current` with the same `project`
   returns that artifact under `artifacts`, with `conflicts: []`.
4. **History (optional)** — `mak4i_supersede` the artifact with new
   `content` and a `reason`, then `mak4i_get_current` shows the new
   version and `mak4i_history` shows both, oldest first, with the reason.

`docs/DEMO.md` is the full walkthrough.

## Local development / self-hosting

`mak4i init` → `mak4i serve` (above) is the whole local Quick Start — you
own and administer the instance, everything is on your machine, nothing
is shared.

The granular admin commands (`mak4i org create`, `mak4i project create`,
`mak4i credential issue`, …) are unchanged and remain the right tool for
multiple organizations, extra principals, scripting, or understanding the
model directly. **`docs/LOCAL_SETUP.md`** covers the recommended path,
the client connection, and the full manual path, plus scaling up to a
shared self-hosted instance (your own database and artifact store — two
environment variables and a migration; deployment contract in
`docs/DEPLOYMENT.md`). Talvik's hosted implementation is one deployment
of the open MAK4I protocol, not a definition of it.

## Hosted / server deployments

The same implementation runs as a long-lived HTTP MCP server, not only
the local stdio path. It ships the generic pieces a hosted MAK4I needs —
Streamable HTTP transport, a SQL control plane (SQLite or PostgreSQL via
SQLAlchemy + Alembic), an object-storage `ArtifactStore` abstraction,
per-principal credential authentication over `Authorization: Bearer`, and
the organization / project / grant model — all selected by environment
variable, nothing baked in. See **`docs/DEPLOYMENT.md`** for the runtime
contract and the auth model.

`mak4i preview provision` is an operator helper for onboarding one
external collaborator against a hosted deployment (a new organization, a
`member` principal, a project, a read/write grant, and a credential). It
is an operator command — not public self-service, no signup flow.

Talvik operates a hosted MAK4I Developer Preview built on this
implementation. It is access-controlled and not a generally available
product; this reference implementation defines neither the protocol nor
that service. See <https://github.com/talvikai/mak4i-protocol> for the
protocol.

## Core concepts

| Concept | What it is |
|---|---|
| **Artifact** | One durable piece of project knowledge: a typed record with a title, content, optional rationale, tags, and a version. Immutable once written. |
| **Lineage & supersession** | A new decision *supersedes* an old one. The old version stops being "current" but is never deleted — `history` returns the whole chain, oldest first, with each supersession's reason. |
| **Discovery → resolution** | A query first gathers candidate artifacts deterministically (`search`), then resolves them to the current applicable set (`get_current`) — applying lineage and checking for conflicts and integrity errors. |
| **Conflicts vs. integrity errors** | Two independent live decisions of the same shape are a **conflict** — surfaced, never auto-resolved. A broken lineage (zero or multiple active versions, dangling pointer) is an **integrity error** — a distinct type, fail-closed. |
| **Audit trail** | Every authenticate, authorize, discover, resolve, inject, create, and supersede is logged as a structured event with the acting principal, org, project, and outcome. |

### Security model

```
Credential  ──▶  Principal  ──▶  Grant  ──▶  Project  ──▶  authorized operation
   (bearer          (who you        (what you     (the context /
    token,           are, from       may do on     authorization
    hashed,          the token,      a project:    boundary)
    revocable)       never from      read/write)
                     the call)
```

- **Organization** is the ownership boundary — who owns a set of projects.
- **Project** is the primary context and authorization boundary — every
  tool call names a `project`, and every read/write is checked against
  the calling principal's grants on it. A denial is explicit and looks
  identical whether the project is in another org or doesn't exist, so
  denials can't be used to probe what exists.
- **Provenance is principal-derived.** `created_by` and every audit
  `principal_id` come from the authenticated credential — there is no
  caller-supplied `actor` or `created_by` argument.

### Tools

`mak4i_list_projects`, `mak4i_search`, `mak4i_get_current`,
`mak4i_create`, `mak4i_supersede`, `mak4i_history` — exposed over MCP.
The same operations are runnable from the CLI without MCP.

## Architecture overview

Two planes, each behind an interface with a local and a hosted
implementation:

```
Control plane (identity + authorization)      Artifact plane (durable knowledge)
   MAK4IEngine / Authorizer                       ArtifactStore interface
        │                                              │
   ControlPlaneStore interface                    ┌────┴─────────────┐
        │                                     LocalJSONStore    GCSArtifactStore
   SQLAlchemy                                   (JSON on disk)   (private bucket)
        │
   ┌────┴──────────┐
 SQLite         PostgreSQL
 (local dev)    (hosted reference)
```

**SQLite, PostgreSQL, and GCS are reference implementation and
deployment choices — not requirements the MAK4I protocol imposes.** The
control plane runs on any SQLAlchemy-supported database; artifacts live
in any backend implementing `ArtifactStore`. See
`docs/MVP_ARCHITECTURE.md` for the full design and `docs/DEPLOYMENT.md`
for the deployment contract.

## Status

The full engine (artifact model, `ArtifactStore` / `LocalJSONStore` /
`GCSArtifactStore`, Discovery / Resolver / ContextBuilder, `MAK4IEngine`,
Audit Logger), the CLI, the local stdio MCP server, and the Streamable
HTTP MCP server with per-principal credential authentication are all
implemented, with multi-organization / per-project read-write
authorization and an audit trail carrying principal / org / project on
every event.

The core interoperability behavior — a decision created through one AI
client, evolved, then retrieved with its rationale by a different fresh
client with zero shared context; conflicts surfaced rather than
auto-resolved; an implementation client surfacing a repo-vs-MAK4I
discrepancy instead of picking a side — has been exercised against a
running service with real AI clients (Claude.ai, Claude Code, Cowork,
Gemini CLI). One documented client gap: ChatGPT's connector UI has no
bearer-token/header auth support. `docs/DEMO.md` walks the behavior;
`docs/TOKEN_MEASUREMENT.md` and `docs/LATENCY_BENCHMARK.md` record
measurements without overclaiming.

## Repository structure

```
src/mak4i/
  models/      # Artifact schema and validation
  store/       # ArtifactStore interface, LocalJSONStore, GCSArtifactStore — org/project-scoped
  discovery/   # Deterministic candidate lookup
  resolution/  # Applicability, lineage, conflict/integrity resolution
  context/     # Compact ContextPackage construction
  audit/       # Audit event logging
  identity/    # Control plane: org/principal/project/grant/credential,
               # ControlPlane service, Authorizer (see MVP_ARCHITECTURE.md §25)
  api.py       # MAK4IEngine — the one library surface (used by CLI, tests, MCP)
  config.py    # Backend + control-plane-DB selection from env (shared by cli.py and mcp_server.py)
  localconfig.py  # local-only .mak4i/ config for `mak4i init` / `mak4i serve`
  mcp_server.py
  cli.py       # operator CLI + `init` / `serve` / `preview provision` onboarding wrappers
artifacts/
  examples/    # demo fixtures (e.g. the stale-config fixture for the implementation flow)
  schedovia/   # legacy placeholder (.gitkeep); LocalJSONStore's real runtime dir is artifacts/local/, untracked
migrations/    # Alembic migrations for the control-plane database
scripts/
  seed_dev_preview.py    # seeds a multi-org fixture (generic seeding logic)
  migrate_gcs_layout.py  # copy-and-validate object-store layout migration
  benchmark_latency.py
tests/
Dockerfile     # at repo root — container build for the MCP server
docs/
```

## Documentation

| Document | What's in it |
|---|---|
| `docs/LOCAL_SETUP.md` | Running MAK4I locally and self-hosting your own instance |
| `docs/DEPLOYMENT.md` | Deployment contract: runtime env vars, auth model, migrations, storage neutrality |
| `docs/MVP_ARCHITECTURE.md` | Architecture design — engine, control plane, resolver, and the multi-org authorization layer |
| `docs/DEMO.md` | Demo walkthrough: connecting a client, then a full create → evolve → history → denial tour |
| `docs/TOKEN_MEASUREMENT.md` | Measured token/context comparison (no fixed savings % claimed) |
| `docs/LATENCY_BENCHMARK.md` | Measured MCP/store request latency |
