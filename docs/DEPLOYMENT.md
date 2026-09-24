# Deploying the MAK4I MCP server

This document describes the **general shape** of an Enterprise
Self-Hosted MAK4I MCP server deployment — the runtime contract, the
authorization model, the database migration step, and the local
development path. It is written so that someone standing up their
**own** MAK4I Reference deployment can do so without reverse-engineering
it from code.

> **Installing for the first time?** Start with
> [`ENTERPRISE_SELF_HOSTED.md`](ENTERPRISE_SELF_HOSTED.md): a step-by-step
> Developer Preview quick start (one Linux VM, Docker Compose, PostgreSQL,
> optional automatic HTTPS). This document is the platform-neutral
> reference that guide relies on: the runtime contract, security
> properties, and production deployment options.

## Prerequisites

- A container runtime (or any way to run the `Dockerfile` at the repo
  root, or just `python -m mak4i.mcp_server` directly on a host with
  Python 3.11+).
- A SQLAlchemy-supported database for the control plane — PostgreSQL is
  the reference choice; anything SQLAlchemy speaks works.
- An `ArtifactStore` backend — the bundled `LocalJSONStore` (a plain
  directory, works anywhere, including a single self-hosted VM with no
  cloud dependency at all) or `GCSArtifactStore` if you're already on
  GCP. Implement the same `ArtifactStore` interface for another object
  store (S3, Azure Blob, ...) if you need one — see "MAK4I is deployment-
  and storage-neutral" below; none is bundled today, so this is a
  documented extension point, not a gap in what's already portable.
- TLS termination in front of the container if serving over HTTPS —
  MAK4I itself is transport-agnostic (see "Security considerations"
  below).
- No GCP account, and nothing Talvik-specific, is required for any of
  the above.

## MAK4I is deployment- and storage-neutral

MAK4I is a protocol and an engine, not a hosting stack. The engine talks
to two interfaces — `ControlPlaneStore` (identity/authorization) and
`ArtifactStore` (durable knowledge) — and knows nothing about what is
behind them. You can run it:

- entirely in-process against SQLite and JSON files on disk (the default;
  see `docs/LOCAL_SETUP.md`), or
- as an HTTP service against any SQLAlchemy-supported database and any
  object store you implement `ArtifactStore` against.

**A concrete reference deployment** — the one whose contract is described
below — runs the container on Google Cloud Run, the control plane on
Cloud SQL for PostgreSQL, and artifacts in a private Google Cloud Storage
bucket. **Cloud Run, Cloud SQL, and GCS are reference deployment choices,
not MAK4I protocol requirements.** Nothing in the protocol, the tool
contract, the artifact schema, or the authorization model depends on
them; swap in a different container host, a different SQL database, or a
different `ArtifactStore` backend and the behavior is identical.

Operator-specific material for any *particular* deployment — the GCP
project, resource names, one-time provisioning commands, deploy runbook,
service-account operations, Secret Manager administration — is not in
this repository. This document covers MAK4I Reference Enterprise
Self-Hosted deployments: you (or your organization) deploy and operate
it. If instead you want to *use* Talvik's own separately hosted, managed
implementation rather than run your own, that's **MAK4I Platform** — a
distinct product this repository does not define — see the "Enterprise
Self-Hosted" section of the top-level `README.md` for how the two
relate.

The Dockerfile lives at the repo root so that a source-based build has it
at the build-context root with no path override.

## Architecture

```
AI client ──HTTPS (Authorization: Bearer <per-principal credential>)──▶ MCP server (container)
                                                                          │
                                       mak4i-mcp-server (Streamable HTTP)  │
                                       _CredentialAuthMiddleware ──────────┼──▶ control-plane database
                                       (per-request auth → Principal)      │    (orgs / principals /
                                       MAK4IEngine + Authorizer            │    projects / grants /
                                                                          │    credentials)
                                       ArtifactStore ────────────────────┼──▶ artifact store
                                                                          │    (org/project-scoped)
                                       AuditLogger ───────────────────────┴──▶ stdout (structured JSON)
```

The MCP server is one stateless container. It needs two backing stores,
each selected by environment variable and each with a local and a hosted
implementation:

| Plane | Interface | Local implementation | Hosted implementation (reference) |
|---|---|---|---|
| Control plane (identity/authz) | `ControlPlaneStore` | SQLite via SQLAlchemy | PostgreSQL via SQLAlchemy |
| Artifact store (durable knowledge) | `ArtifactStore` | `LocalJSONStore` (JSON files on disk) | `GCSArtifactStore` (one private object-storage bucket) |
| Audit | `AuditLogger` | stdout | stdout → whatever collects container logs |

SQLite / PostgreSQL / GCS are **reference implementation choices**, not
requirements the MAK4I protocol imposes. Any SQLAlchemy-supported
database works for the control plane; any backend implementing
`ArtifactStore` works for artifacts.

## Runtime configuration

All configuration is environment variables (`src/mak4i/config.py`):

| Variable | Required | Meaning |
|---|---|---|
| `MAK4I_TRANSPORT` | for HTTP serving | `http` or `streamable-http` (equivalent — the latter is kept for compatibility with configuration already in the field) for a hosted server; `stdio` (default) for local single-user use |
| `MAK4I_HOST` | no | bind address for the HTTP transport. Defaults to `127.0.0.1` (safe for local protocol testing). **A container/hosted deployment must set this to `0.0.0.0`** to accept connections from outside the container — the default no longer does this implicitly. |
| `MAK4I_PORT` | no | bind port for the HTTP transport. Takes precedence over `PORT` when both are set. |
| `PORT` | no | the existing container-platform convention for the bind port (e.g. Cloud Run sets this automatically) — still fully supported; used when `MAK4I_PORT` isn't set. Default `8080` if neither is set. |
| `MAK4I_CONTROL_PLANE_DB` | yes | SQLAlchemy URL for the control plane. Defaults to `sqlite:///./mak4i-control-plane.db`. Hosted: a `postgresql+psycopg://…` URL. **Keep this in a secret store, never a plaintext env literal or the repo** — it contains the database password. |
| `MAK4I_STORE` | yes | `local` (default, `LocalJSONStore`) or `gcs` (`GCSArtifactStore`) |
| `MAK4I_LOCAL_STORE_DIR` | if `MAK4I_STORE=local` | directory for `LocalJSONStore` (default `artifacts/local/`; the container image sets `/data/artifacts`, its one writable path — mount a persistent volume there). See "`LocalJSONStore` is single-writer" below. |
| `MAK4I_GCS_BUCKET` | if `MAK4I_STORE=gcs` | bucket name for `GCSArtifactStore` |
| `MAK4I_GCP_PROJECT` | if `MAK4I_STORE=gcs` | GCP project for the GCS client |
| `MAK4I_CONTROL_PLANE_CREATE_TABLES` | no | `1` builds the control-plane schema directly from metadata (dev/CI convenience against a throwaway SQLite file). Leave unset for a real database — migrate with Alembic instead. |
| `MAK4I_TOKEN` | stdio only | the one credential a local stdio session authenticates for its lifetime. The operator CLI also accepts it to derive `--actor` (see "Bootstrapping a fresh enterprise deployment"). |
| `MAK4I_PUBLIC_ENDPOINT` | no (CLI only) | the full client-facing MCP URL, **including `/mcp`** (e.g. `https://mak4i.example.com/mcp`). Not read by the server. `mak4i credential issue` uses it to print an exact, HTTP-only connect command; `mak4i access provision` requires it (or `--endpoint`). |

The same variables are read by `cli.py` and `mcp_server.py`, so the CLI
and the server always agree about which backends they mean — a container
needs none of `mak4i serve`'s CLI flags; setting these directly (as the
`Dockerfile`'s `CMD ["python", "-m", "mak4i.mcp_server"]` does) is fully
equivalent. Where both a CLI flag and an environment variable are given
(only relevant when using `mak4i serve` rather than the container's
direct entry point), the flag wins: `--transport` > `MAK4I_TRANSPORT`,
`--host` > `MAK4I_HOST`, and `--port` > `MAK4I_PORT` > the Local HTTP port
saved by `mak4i init` > 9090 (`mak4i serve` is Local-only and does not
read `PORT`; the container entry point's port resolution above is
unchanged).

### Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | liveness probe — the process is up |
| `GET /ready` | none | readiness probe — the control-plane backend is reachable; `503` if not |
| `POST /mcp` | `Authorization: Bearer <credential>` | the MCP Streamable HTTP endpoint — the only route that serves MAK4I tools |

`/health` and `/ready` intentionally return bare status text/codes only —
never configuration, connection strings, or any other detail — and carry
no project knowledge, which is why they're the only unauthenticated
routes. Point your platform's liveness check at `/health` and its
readiness check (the one gating whether traffic is routed to this
instance) at `/ready`.

## Authorization model

The MCP server's HTTP ingress can be left open (no platform-level
invoker check) because **`_CredentialAuthMiddleware` inside
`mcp_server.py` is the authorization boundary**. On every request it
authenticates a per-principal credential from the
`Authorization: Bearer <token>` header, resolves it to a `Principal`, and
the `Authorizer` then fail-closes every artifact read/write against that
principal's grants. There is no shared secret and no anonymous access;
a missing, unknown, revoked, or expired credential is a `401` with no
detail (the reason is audit-only, so a failed request cannot be used to
probe why it failed).

This is what lets external SaaS connectors (Claude.ai, Cowork, and
others) that cannot present a platform identity token still connect
safely: the credential in the header is the gate.

The runtime identity the container runs as needs only:

- read/write on the artifact store (scoped to that store, not broader),
- network reachability to the control-plane database,
- read access to wherever `MAK4I_CONTROL_PLANE_DB` is kept.

No long-lived key files are committed anywhere; the reference deployment
uses the platform's workload identity / Application Default Credentials
rather than a downloaded key.

**Control-plane database access is administrative access.** The MCP
endpoint is protected by credentials, but the operator CLI talks to the
control-plane database directly and accepts `--actor <principal-id>`
without a credential (it's how the very first owner acts before any
credential exists). Anyone who can connect to that database can therefore
act as any principal. Never expose it publicly: keep it on a private
network reachable only by the MCP server and your operators.

## Applying the control-plane schema

The control plane is migrated with Alembic (`migrations/`, config in
`alembic.ini`, URL read from `MAK4I_CONTROL_PLANE_DB` by
`migrations/env.py`):

```bash
MAK4I_CONTROL_PLANE_DB="<your control-plane URL>" uv run alembic upgrade head
```

The container image includes `alembic.ini` and `migrations/`, so the
same image that serves traffic can also migrate, with no Python toolchain
on the host:

```bash
docker run --rm -e MAK4I_CONTROL_PLANE_DB="<your control-plane URL>" <mak4i-image> alembic upgrade head
```

(The Compose quick start runs exactly this as its one-shot `migrate`
service before the server starts.) Run it once before first serving
traffic, and again after every upgrade. It's a no-op when the schema is
already current.

For a throwaway local SQLite database you can instead set
`MAK4I_CONTROL_PLANE_CREATE_TABLES=1` and skip Alembic. **Never use it on
a database you intend to keep.** It creates the tables without recording
an Alembic revision, so the next `alembic upgrade head` (your upgrade
path) tries to create them again and fails.

## Bootstrapping a fresh enterprise deployment

Once the container is up and the schema is migrated, everything else —
the first organization, its owner, a project, a grant, and the first
credential — is self-service from the CLI. No Talvik involvement, no
hand-written SQL, and nothing here is specific to this repository's own
operator (a **different** organization's admin runs the exact same
commands against their own deployment).

The contract, on any platform:

- **The CLI must reach the same control-plane database as the server.**
  The simplest way is to run it inside the MCP server's own container,
  which already has the CLI and the right `MAK4I_CONTROL_PLANE_DB`
  (`docker compose exec mak4i mak4i …` in the Compose quick start;
  `docker exec`/`kubectl exec` or a one-off task elsewhere).
- **`--actor <owner principal id>` is required until a credential
  exists.** After step 4, `MAK4I_TOKEN=<that credential>` lets the CLI
  derive the actor by authenticating it instead.
- **Do not use `mak4i init`.** It's the [Local setup](LOCAL_SETUP.md)
  bootstrap: it always provisions a separate local SQLite environment and
  deliberately ignores `MAK4I_CONTROL_PLANE_DB`.

The Compose-specific, copy-paste version of this sequence, including a
second principal and client, is
[`ENTERPRISE_SELF_HOSTED.md` step 7](ENTERPRISE_SELF_HOSTED.md#7-bootstrap-your-organization).
The generic form:

```bash
# Point the CLI at the same control-plane database the server uses
# (already set if you run the CLI inside the server's container).
export MAK4I_CONTROL_PLANE_DB=<your control-plane URL>

# 1. First organization + its owner principal.
mak4i org create --name "Acme Corp" --owner-display-name "Platform Admin"
#   -> {"organization": {"organization_id": "org_...", ...}, "owner": {"principal_id": "prn_...", ...}}

# 2. A project — the primary context/authorization boundary.
mak4i project create --actor <owner_principal_id> \
  --organization-id <organization_id> --name "Platform"

# 3. The owner grants itself (or any other principal) access to it.
mak4i grant create --actor <owner_principal_id> \
  --principal-id <owner_principal_id> --project-id <project_id> \
  --permissions read,write

# 4. A credential for an AI client — prints the raw token once, plus a
#    ready-to-copy connect command for $MAK4I_PUBLIC_ENDPOINT when set.
mak4i credential issue --actor <owner_principal_id> \
  --principal-id <owner_principal_id> --display-name "Claude Code"
```

Everything from here on is ordinary operation, self-service:
`mak4i principal create` to add more principals, `mak4i grant create` to
extend access to more projects, `mak4i credential issue`/`revoke` to
manage AI-client connections, and the read-only `list`/`show` counterpart
of each (`mak4i org|project|principal list|show`, `mak4i grant|credential
list`) to inspect the current state without touching the database
directly. Every one of these is owner-scoped: an owner in one
organization gets `access denied`, never a peek, at another
organization's principals, projects, grants, or credentials — see
`docs/LOCAL_SETUP.md` Section 3 for the full command reference (identical
commands whether the control plane behind them is a local SQLite file or
this deployment's PostgreSQL database).

## Deploying the container

The server is a standard container: build the image from the root
`Dockerfile`, push it to a registry your runtime can pull from, and run
it with the environment above — `MAK4I_TRANSPORT=http` (or
`streamable-http`), **`MAK4I_HOST=0.0.0.0`** (required — the default is
loopback-only, safe for local testing but unreachable from outside the
container), an artifact-store selection, and `MAK4I_CONTROL_PLANE_DB`
supplied from a secret rather than a literal. Give it a generous request
timeout — Streamable HTTP sessions are long-lived (an idle session should
survive a 30s idle gap; the reference deployment uses the platform
maximum).

The image runs as an unprivileged user (uid `10001`). Its only writable
path is `/data`; `MAK4I_LOCAL_STORE_DIR` defaults to `/data/artifacts`
in the image. The same image also runs migrations (`alembic upgrade
head`) and the operator CLI (`mak4i`).

### `LocalJSONStore` is single-writer

`LocalJSONStore` serializes writes with per-artifact `fcntl` file locks
and atomic renames on a local filesystem. That is safe for **exactly one
MCP server process** on a local disk or block volume. Don't run more
than one replica against the same directory, and don't put it on a
network filesystem (NFS/SMB lock semantics vary). To scale beyond one
instance, use an object-storage `ArtifactStore` (`GCSArtifactStore`, or
your own implementation of the interface).

### Reverse proxy / TLS requirements

MAK4I serves plain HTTP; TLS is terminated in front of it (see "Security
considerations"). Whatever proxy or load balancer you use must:

- **not buffer responses.** Streamable HTTP streams server-sent events.
  Disable response buffering or flush immediately (Caddy
  `flush_interval -1`; nginx `proxy_buffering off`).
- **allow long-lived requests.** Set upstream read/idle timeouts well
  above the 30 s idle gap a session must survive (minutes, not seconds).
- **pass the `Host` header through**, and **reach MAK4I on a
  non-loopback bind** (`MAK4I_HOST=0.0.0.0` inside the container, or the
  host's private address). With a loopback bind, MAK4I's DNS-rebinding
  guard stays on and rejects requests for your real hostname with `421`.
  Restrict exposure with the container's port publishing or a firewall
  instead.
- expose only HTTPS to clients. Credentials travel in a header on every
  request.

`deploy/compose/Caddyfile` is a working example of all of the above.

Platform-specific provisioning (creating the database, the bucket, the
runtime identity, the secret, and the deploy invocation itself) depends
on where you host it; see "Production deployment options" below. The
single-VM Compose stack in `deploy/compose/` automates all of it for a
Developer Preview.

## Production deployment options

MAK4I doesn't prescribe a hosting stack. Three shapes, from smallest to
largest:

| Option | Control plane | Artifact store | Where it's described |
|---|---|---|---|
| **Single-VM Developer Preview** | PostgreSQL 16 container, named volume | `LocalJSONStore` on a named volume (one replica) | [`ENTERPRISE_SELF_HOSTED.md`](ENTERPRISE_SELF_HOSTED.md), `deploy/compose/` |
| **Container platform + managed services** | managed PostgreSQL (backups, PITR) | object-storage `ArtifactStore` | this document: the runtime contract above + your platform's docs |
| **Reference cloud deployment** | Cloud SQL for PostgreSQL | `GCSArtifactStore` (private bucket) | "MAK4I is deployment- and storage-neutral" above |

Moving from the Compose stack to production mostly means replacing
parts, not re-architecting. Point `MAK4I_CONTROL_PLANE_DB` at a managed
database (restored from `pg_dump`), switch `MAK4I_STORE` to an object
store (migrating the existing artifacts into it), keep the database URL in a secret manager
instead of `.env`, and terminate TLS at your load balancer or ingress.

## Local dev paths (stdio and HTTP)

```bash
uv run python -m mak4i.mcp_server
```

Defaults to `MAK4I_STORE=local` (`LocalJSONStore`, `artifacts/local/`),
`MAK4I_TRANSPORT=stdio`, and `MAK4I_CONTROL_PLANE_DB` = a local SQLite
file. stdio also requires `MAK4I_TOKEN` — a credential authenticated once
at process startup, since a local stdio session is single-user for its
whole lifetime.

Setting `MAK4I_TRANSPORT=http` instead runs the identical process in
Streamable HTTP mode, on loopback by default — useful for exercising the
real `/mcp` protocol endpoint locally before deploying it, without
needing `MAK4I_TOKEN` at all (the credential travels per-request instead,
in the `Authorization` header). `mak4i serve --transport http` is the
CLI convenience over the same thing when working from a `mak4i init`
environment.

See **`docs/LOCAL_SETUP.md`** for the full local path — both transports,
bootstrapping a control plane, issuing yourself a credential, and running
the server and the CLI against it.

## Security considerations

- **Credentials are high-entropy, hash-only, and shown once.** A raw
  token (`generate_token()`, 32 bytes of `secrets.token_urlsafe`) exists
  in plaintext only in the CLI's own stdout at the moment
  `credential issue` runs; the store keeps a SHA-256 hash
  (`Credential.token_hash`), never the raw value. `credential list` never
  prints it either (see `docs/LOCAL_SETUP.md`). Nothing in this codebase
  logs a raw token — `AuditLogger` records `principal_id`/
  `organization_id`/`auth_method`, never the credential itself.
- **Fail closed.** A missing, unknown, expired, or revoked credential is
  a `401` with no distinguishing detail (`ControlPlane.authenticate`'s
  `CredentialInvalidError` is uniform on purpose) — a failed request
  cannot be used to probe *why* it failed. `Authorizer.require` fails
  closed identically for an unknown project vs. one that exists but has
  no grant.
- **Revocation is immediate.** `credential revoke` / `grant revoke` take
  effect on the very next request — there is no cache or TTL to wait out.
- **Non-enumeration.** Cross-organization lookups (`org`, `principal`,
  `project`, `grant`, `credential` `list`/`show`) raise the same
  `AccessDeniedError` whether the target doesn't exist or simply belongs
  to another organization — never a different error that would let an
  admin fingerprint another tenant's existence.
- **`MAK4I_CONTROL_PLANE_DB` is a secret** (it embeds the database
  password) — keep it in your platform's secret manager, never a
  plaintext env literal committed anywhere. The same applies to any
  artifact-store credentials (e.g. cloud storage service-account keys).
  (The single-VM Compose quick start is the one documented exception: for
  a Developer Preview, it keeps the database password in a `chmod 600`
  `.env` file on the VM. That is an evaluation convenience, not a
  production pattern.)
- **Least privilege for the runtime identity.** The container needs only
  read/write on its own artifact store and network reachability plus
  read access to the control-plane database — nothing broader.
- **HTTPS is the caller's responsibility.** MAK4I is transport-agnostic;
  putting a real TLS-terminating load balancer or reverse proxy in front
  of the container (never serving `streamable-http` bare over plain HTTP
  to the internet) is part of standing up "your own infrastructure."
- **OAuth is intentionally not part of this reference implementation.**
  The bearer-credential model above is the whole authentication surface
  here; a hosted product layering OAuth or another auth scheme on top
  does so in its *own* code, mapping down to a `Credential` the same way
  this CLI does — never by changing anything in this repository.
- **DNS-rebinding protection is conditional on the bind host, not
  disabled outright.** A loopback bind (`127.0.0.1`/`localhost`/`::1` —
  the local-testing default) keeps it enabled, matching the threat model
  it exists for (a malicious webpage tricking a browser into reaching a
  localhost-bound process via a spoofed `Host` header). Any other bind
  (what `MAK4I_HOST=0.0.0.0` deployment uses) disables it, because that
  threat model doesn't apply to a server real clients reach under its
  real hostname, and the guard would otherwise reject every legitimate
  request with `421`. Credential authentication is the actual boundary
  either way.
- **No CORS is configured**, and none should be added without a concrete
  need. This server is designed for server-to-server MCP clients
  presenting a bearer credential (Claude.ai/Cowork/Claude Code/Gemini/
  custom agents), not for a credential-bearing request originating from
  arbitrary browser JavaScript — adding permissive CORS would only widen
  the attack surface for no current client.

## Backups

Two independent things to back up, matching the two backing stores:

- **Control-plane database** (organizations, principals, projects,
  grants, credential *hashes*) — back it up the way you'd back up any
  PostgreSQL (or other SQLAlchemy-supported) database: `pg_dump` /
  managed automated snapshots / point-in-time recovery, on whatever
  schedule your organization's data-loss tolerance requires. This is the
  authorization system of record — losing it without a backup means
  losing every org/principal/project/grant/credential relationship, even
  though the artifacts themselves would still be intact.
- **Artifact store** (the durable project knowledge itself) — back up
  `LocalJSONStore`'s directory like any other application data directory
  (filesystem snapshot, rsync, etc.), or rely on your object-storage
  provider's own versioning/replication if using a cloud
  `ArtifactStore` implementation.

Restoring either independently is safe: an artifact referencing a
project/organization that no longer exists in a restored control plane
simply becomes inaccessible (fails closed, like any other authorization
gap) rather than corrupting anything; a control plane restored without
its matching artifacts just has projects that report no current context
until the artifact store catches up.

The exact backup and restore commands for the Compose quick start are in
[`ENTERPRISE_SELF_HOSTED.md` step 11](ENTERPRISE_SELF_HOSTED.md#11-operate-backups-upgrades-logs).

## Upgrading

1. **Read the release notes** for what changed, particularly any new
   Alembic migration.
2. **Back up first** (see above) — routine practice before any schema
   change.
3. **Deploy the new container image / pull the new code.**
4. **Apply migrations**: `alembic upgrade head` from the new image (see
   "Applying the control-plane schema"), or `MAK4I_CONTROL_PLANE_DB="<url>"
   uv run alembic upgrade head` from a source checkout. Safe to run even
   when there's nothing new to apply.
5. **Restart the server** so it picks up the new code.

In the Compose quick start, steps 3–5 are a single
`docker compose up -d --build --wait`: the `migrate` service runs before
the server restarts.

There is no separate "upgrade command" beyond the same `alembic upgrade
head` used for first-time bootstrap — migrations are additive and
idempotent to re-run.
