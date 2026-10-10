# MAK4I Reference Implementation

MAK4I gives durable project knowledge (architecture decisions,
documentation facts, implementation state) a home outside any one AI
client. A decision recorded while working with one assistant becomes
discoverable and usable by another, with its rationale and history,
without you repeating yourself.

This repository is the MAK4I **reference implementation**: a
technology-agnostic engine, a CLI and an MCP server. The MAK4I protocol
and specification are maintained separately at
<https://github.com/talvikai/mak4i-protocol>; this is one implementation
of it, and others are possible.

**Current release: [`v0.1.0-rc.5`](https://github.com/talvikai/mak4i-reference/releases/tag/v0.1.0-rc.5)**
(what changed: [`CHANGELOG.md`](CHANGELOG.md)).

> **Developer Preview: not a production-supported release.** MAK4I
> Reference is published as release candidates for evaluation and
> feedback. APIs, CLI behavior, configuration, protocol details and
> deployment patterns may change between releases. Don't rely on it for
> production workloads yet.

## The problem it solves

You decide with one AI assistant that the primary database is MySQL, not
PostgreSQL, because of managed-hosting availability. A week later you open
a different assistant to implement something. It has no idea: you explain
again, or it guesses and writes PostgreSQL-shaped code.

With MAK4I, that decision is a durable **artifact** written through one
client's tool call and retrieved through another's. The second client gets
the current decision and how it got there, and surfaces a disagreement
with the local code instead of silently picking a side.

## Deployment flavors

| Flavor | Who it's for | What you run | Guide |
|---|---|---|---|
| **Local Developer Setup** | A developer trying MAK4I, or using it personally, on their own machine (macOS, Linux; Windows PowerShell commands provided). | The CLI and a local MCP server over stdio or local HTTP, with everything stored in the repository's own `.mak4i/` directory. Nothing is shared. | [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md) |
| **Enterprise Self-Hosted** | Cloud administrators, platform engineers, DevOps and SRE running a shared MAK4I for a team and remote AI clients (Claude.ai, Claude Code, Gemini and others). | One Linux VM with Docker Compose: PostgreSQL, the MCP server and automatic HTTPS, installed and operated with a bootstrap command. | [`docs/ENTERPRISE_SELF_HOSTED.md`](docs/ENTERPRISE_SELF_HOSTED.md) |

Each guide is the single, complete source for its flavor: requirements,
installation, running, connecting clients, upgrades, backups,
troubleshooting and uninstalling.

## Core concepts

| Concept | What it is |
|---|---|
| **Artifact** | One durable piece of project knowledge: a typed record with a title, content, optional rationale, tags and a version. Immutable once written. |
| **Lineage and supersession** | A new decision *supersedes* an old one. The old version stops being current but is never deleted; history returns the whole chain with each reason. |
| **Discovery and resolution** | A query gathers candidates deterministically, then resolves them to the current applicable set, checking lineage, conflicts and integrity. |
| **Tags and subjects** | Tags classify artifacts for search. An optional `subject_key` names the one logical subject an artifact decides. |
| **Conflicts and integrity errors** | Two current artifacts of the same type with the same `subject_key` are a **conflict**: surfaced, never auto-resolved. A broken lineage is an **integrity error**, and fails closed. |
| **Organizations, projects and grants** | The organization owns projects; the project is the context and authorization boundary; a principal's grant gives read or read/write access to a project. |
| **Credentials** | Every client authenticates with a per-principal bearer credential. It's stored only as a hash and can be revoked instantly. |
| **Connections** | Each MAK4I installation identifies itself to AI clients (`mak4i_whoami`). A denial on one connection is never a reason to write to another. |
| **Audit trail** | Every authentication, authorization, read and write is logged as a structured event with the principal, organization, project and outcome. |

The tools a client sees: `mak4i_whoami`, `mak4i_list_projects`,
`mak4i_search`, `mak4i_get_current`, `mak4i_create`, `mak4i_supersede` and
`mak4i_history`.

## More information

| | |
|---|---|
| Architecture | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) (deployment architecture and runtime contract), [`docs/MVP_ARCHITECTURE.md`](docs/MVP_ARCHITECTURE.md) (engine and authorization design) |
| Walkthrough | [`docs/DEMO.md`](docs/DEMO.md): connecting clients, then create → evolve → history → denial |
| Measurements | [`docs/TOKEN_MEASUREMENT.md`](docs/TOKEN_MEASUREMENT.md), [`docs/LATENCY_BENCHMARK.md`](docs/LATENCY_BENCHMARK.md) |
| Security | [`SECURITY.md`](SECURITY.md): supported versions, how to report a vulnerability privately, image and dependency policy |
| Changes | [`CHANGELOG.md`](CHANGELOG.md) |
| License | MIT: [`LICENSE`](LICENSE) |
| Contributing | Bug reports and feature requests: [GitHub issues](https://github.com/talvikai/mak4i-reference/issues). Security issues: never in a public issue; see [`SECURITY.md`](SECURITY.md). Pull requests are welcome; please open an issue first to discuss larger changes. |

## Repository structure

```
src/mak4i/          engine (models, store, discovery, resolution, context, audit, identity),
                    api.py (MAK4IEngine), mcp_server.py, cli.py
migrations/         Alembic migrations for the control-plane database
deploy/compose/     Enterprise Self-Hosted Docker Compose stack (PostgreSQL, MCP server, Caddy)
deploy/bootstrap/   Enterprise Self-Hosted lifecycle tool (mak4i-enterprise)
docs/               guides and design documents
scripts/            maintenance scripts, including the release-consistency check
tests/              test suite
Dockerfile          container image for the MCP server
```
