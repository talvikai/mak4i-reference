# Deployment architecture

How a MAK4I Reference server is put together, what it needs from any
platform, and the options for running it. This is **not** an installation
guide; installation and operation have exactly two canonical guides:

| Deployment flavor | For | Guide |
|---|---|---|
| **Local Developer Setup** | one developer on their own machine; stdio or local HTTP; nothing shared | [`LOCAL_SETUP.md`](LOCAL_SETUP.md) |
| **Enterprise Self-Hosted** | a shared server on one Linux VM for a team and remote AI clients, over HTTPS | [`ENTERPRISE_SELF_HOSTED.md`](ENTERPRISE_SELF_HOSTED.md) |

> **Developer Preview.** `v0.1.0-rc.5` is a release candidate, not a
> production-supported release.

## Architecture

```
AI client ──HTTPS (Authorization: Bearer <per-principal credential>)──▶ MCP server (container)
                                                                          │
                                       Streamable HTTP /mcp               │
                                       credential auth → Principal ───────┼──▶ control-plane database
                                       MAK4IEngine + Authorizer           │    (organizations, principals,
                                                                          │     projects, grants, credentials)
                                       ArtifactStore ─────────────────────┼──▶ artifact store
                                                                          │    (organization/project-scoped)
                                       AuditLogger ───────────────────────┴──▶ stdout (structured JSON)
```

The MCP server is one stateless process with two backing stores, each
chosen by configuration:

| Plane | Interface | Local | Enterprise Self-Hosted | Other options |
|---|---|---|---|---|
| Control plane (identity, authorization) | `ControlPlaneStore` | SQLite | PostgreSQL 16 | any SQLAlchemy-supported database |
| Artifacts (durable knowledge) | `ArtifactStore` | `LocalJSONStore` | `LocalJSONStore` on a volume | `GCSArtifactStore`, or your own object-store implementation |
| Audit | `AuditLogger` | stdout | stdout (container logs) | your log pipeline |

SQLite, PostgreSQL and GCS are reference choices, not requirements of the
MAK4I protocol. Nothing in the protocol, tool contract, artifact schema or
authorization model depends on them.

## Authorization model

- **The credential is the gate.** Every `/mcp` request carries a
  per-principal bearer credential. The server resolves it to a principal,
  and every read or write is checked against that principal's grants on
  the named project. There's no shared secret and no anonymous access.
- **Fail closed, without detail.** A missing, unknown, revoked or expired
  credential gets `401` with no reason (the reason is audit-only). An
  unknown project and a project without a grant are denied identically, so
  denials can't be used to discover what exists. Denials also name the
  MAK4I connection that refused and say not to retry elsewhere
  (`mak4i_whoami` identifies the connection).
- **Revocation is immediate:** no cache or TTL.
- **Credentials are hash-only.** A raw token exists in plaintext only in
  the output of `credential issue`; the store keeps its SHA-256 hash, and
  no log contains it.
- **Database access is administrative access.** The operator CLI talks to
  the control-plane database directly and can act as any principal
  (`--actor`), which is how the first owner is created. The database must
  never be reachable from outside.

## Runtime contract

What any platform must provide to run the container image
(`Dockerfile` at the repository root):

| Variable | Meaning |
|---|---|
| `MAK4I_TRANSPORT` | `http` (or `streamable-http`) to serve MCP over HTTP; `stdio` (default) for one local session |
| `MAK4I_HOST` | bind address, default `127.0.0.1`. A container must set `0.0.0.0` to be reachable from outside **the container**; host or network exposure must then be restricted by port publishing, a private network or a firewall, with TLS in front. `0.0.0.0` provides no security by itself. |
| `MAK4I_PORT` (or `PORT`) | bind port, default `8080` |
| `MAK4I_CONTROL_PLANE_DB` | SQLAlchemy URL of the control plane, e.g. `postgresql+psycopg://…`. **A secret**: it contains the database password. |
| `MAK4I_STORE` | `local` (`LocalJSONStore`) or `gcs` |
| `MAK4I_LOCAL_STORE_DIR` | directory for `LocalJSONStore` (image default `/data/artifacts`) |
| `MAK4I_GCS_BUCKET`, `MAK4I_GCP_PROJECT` | for `MAK4I_STORE=gcs` |
| `MAK4I_PUBLIC_ENDPOINT` | the client-facing MCP URL (connect instructions, `mak4i_whoami`) |
| `MAK4I_INSTANCE_NAME`, `MAK4I_ENVIRONMENT` | how this installation names itself to AI clients |
| `MAK4I_TOKEN` | stdio only: the one credential a local stdio session authenticates |
| `MAK4I_CONTROL_PLANE_CREATE_TABLES` | `1` builds the schema directly (throwaway databases only; never on a database you keep) |

Settings for each flavor: [Local](LOCAL_SETUP.md#13-configuration-reference),
[Enterprise Self-Hosted](ENTERPRISE_SELF_HOSTED.md#12-configuration-reference).

**Endpoints.** `GET /health` (liveness) and `GET /ready` (the control
plane is reachable; `503` if not) are unauthenticated and return a bare
status word only. `POST /mcp` is the only route that serves MAK4I tools.

**Schema migrations.** The control plane is migrated with Alembic
(`alembic upgrade head`, included in the image). Run it before first
serving traffic and after every upgrade; it's a no-op when the schema is
current. The Enterprise Compose stack does this automatically.

**Image.** Runs as an unprivileged user (uid 10001); the only writable
path is `/data`. Base images are pinned by version and digest, and scanned
on a schedule (`SECURITY.md`).

### `LocalJSONStore` is single-writer

It serializes writes with per-artifact file locks and atomic renames on a
local filesystem, which is safe for **exactly one** MCP server process on a
local disk or block volume. Don't run a second replica against the same
directory, and don't put it on a network filesystem. To scale out, use an
object-storage `ArtifactStore`.

### Reverse proxy requirements

MAK4I serves plain HTTP; TLS terminates in front of it. The proxy or load
balancer must:

- **not buffer responses:** Streamable HTTP streams server-sent events
  (Caddy `flush_interval -1`, nginx `proxy_buffering off`);
- **allow long-lived requests:** idle timeouts well above 30 seconds;
- **pass the `Host` header through, and reach MAK4I on a non-loopback
  bind.** On a loopback bind, MAK4I's DNS-rebinding guard rejects your
  real hostname with `421`;
- **expose only HTTPS** to clients: credentials travel in a header on
  every request.

`deploy/compose/Caddyfile` is a working example.

## Security

- `MAK4I_CONTROL_PLANE_DB` and any storage credentials are secrets: keep
  them in your platform's secret manager. (The single-VM Enterprise stack
  keeps the database password in a mode-600 `.env`, acceptable only for
  this Developer Preview.)
- Least privilege for the runtime identity: read/write on its own artifact
  store and access to the control-plane database, nothing broader.
- HTTPS is the operator's responsibility; never expose the plain-HTTP port
  to the internet.
- DNS-rebinding protection is on for loopback binds (the local default)
  and off for other binds, where real clients use the real hostname.
  Credential authentication is the boundary either way.
- No CORS is configured: clients are server-to-server MCP clients with a
  bearer credential, not browser JavaScript.
- OAuth isn't part of this reference implementation; the bearer
  credential is the whole authentication surface.
- Vulnerability reporting and the image and dependency policy:
  [`SECURITY.md`](../SECURITY.md).

## Backups

Two independent stores need backing up on the same schedule: the
control-plane database (the authorization system of record) and the
artifact store (the knowledge itself). Restoring one without the other
fails closed rather than corrupting anything. How:
[Local](LOCAL_SETUP.md#10-backup-and-restore),
[Enterprise Self-Hosted](ENTERPRISE_SELF_HOSTED.md#83-backup).

## Production options

| Option | Control plane | Artifacts | Where |
|---|---|---|---|
| Single-VM Enterprise Self-Hosted | PostgreSQL 16 container, volume | `LocalJSONStore` on a volume (one replica) | [`ENTERPRISE_SELF_HOSTED.md`](ENTERPRISE_SELF_HOSTED.md) |
| Container platform + managed services | managed PostgreSQL with backups and point-in-time recovery | an object-storage `ArtifactStore` | the runtime contract above and your platform's documentation |

Moving from the single-VM stack to managed services mostly means
replacing parts: point `MAK4I_CONTROL_PLANE_DB` at a managed database
(restored from `pg_dump`), move the artifacts to an object store, keep
secrets in a secret manager, and terminate TLS at your load balancer.
Infrastructure-as-code (for example Terraform) and Kubernetes/Helm
packaging aren't provided in this release.
