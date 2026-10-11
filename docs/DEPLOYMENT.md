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

- **The credential or access token is the gate.** Every `/mcp` request
  carries a per-principal bearer credential or an OAuth access token from
  the built-in authorization server (MAK-0008). The server resolves it to
  a principal, and every read or write is checked against that principal's
  live grants (`read`, `write`, `resolve`) on the named project, capped by
  the token's scopes. There's no shared secret and no anonymous access.
- **Identity comes only from authentication.** An agent's `agent_id`, the
  author of a record and its provenance are taken from the authenticated
  principal, never from request content (MAK-0006 §3, §7).
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
  never be reachable from outside. There is no administration over HTTP or
  MCP; every administrative action is recorded in the audit log
  (`mak4i audit list`).

### stdio and HTTP authentication

| Transport | How the principal is authenticated |
|---|---|
| stdio | One credential (`MAK4I_TOKEN` or `MAK4I_TOKEN_FILE`) for the life of the local process, re-checked before every operation. OAuth doesn't apply. |
| HTTP | Per request: a principal credential (`mak4i_…`) or, with `MAK4I_OAUTH_ENABLED=1`, an OAuth access token (`mak4at_…`). The two are told apart by prefix and never fall back to each other. |

**OAuth state and restarts.** Codes, authorizations, refresh tokens and
revocations are stored (hashed) in the control-plane database, so a
restart or redeploy changes nothing for connected clients. OAuth uses
opaque tokens and server-side browser sessions, so there are no signing
keys to rotate; to invalidate everything at once, revoke authorizations
(`mak4i oauth authorization revoke --client-id …` or per principal).

### Custom authentication adapters

The Reference supplies no SSO/OIDC/SAML connector and never trusts an
identity header from the network. If you put your own identity layer in
front of MAK4I (or build one into a fork), keep this boundary
(MAK-0006 §9):

- verify the external assertion cryptographically or over a trusted
  channel, and map a **stable** external subject (not a display name or
  email alone) to exactly one MAK4I principal;
- never create or change grants, roles or `agent_id` as a side effect of
  sign-in unless an owner configured that mapping explicitly;
- honor deactivation and revocation on every request;
- leave authorization to MAK4I's grants — an adapter authenticates, it
  never authorizes.

The simplest conforming adapter issues MAK4I sign-in codes or credentials
to principals you already provisioned, so MAK4I's own authorization and
audit stay unchanged.

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
| `MAK4I_TOKEN` | stdio only: the one credential a local stdio session authenticates (re-checked before every operation) |
| `MAK4I_OAUTH_ENABLED` and `MAK4I_OAUTH_*` | the built-in OAuth authorization server for HTTP (MAK-0008); see [Enterprise → OAuth sign-in](ENTERPRISE_SELF_HOSTED.md#71-oauth-sign-in) for every setting. Issuer and advertised URLs are derived from `MAK4I_PUBLIC_ENDPOINT` (and `MAK4I_OAUTH_ISSUER`), never from request headers. |
| `MAK4I_CONTROL_PLANE_CREATE_TABLES` | `1` builds the schema directly (throwaway databases only; never on a database you keep) |
| `MAK4I_CONTROL_PLANE_DB_FILE`, `MAK4I_TOKEN_FILE` | read the secret from a file (Docker/Kubernetes secrets) instead of the environment; setting both forms is an error |
| `MAK4I_DEPLOYMENT_MODE` | `local` or `self-hosted` (default: `self-hosted` unless bound to loopback) |
| `MAK4I_TRUSTED_PROXIES` | comma-separated IPs/CIDRs of your reverse proxies, whose `X-Forwarded-For` is trusted for the client address (rate limits, logs). Never `*`. Advertised URLs never come from request headers. |
| `MAK4I_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |

**Validation.** The server validates all of these at start and refuses to
run with a complete list of problems. `mak4i config check` runs the same
validation and prints the effective configuration with secrets shown only
as references; its output follows the versioned schema in
[`config.schema.json`](config.schema.json) (`mak4i config schema`).

Settings for each flavor: [Local](LOCAL_SETUP.md#13-configuration-reference),
[Enterprise Self-Hosted](ENTERPRISE_SELF_HOSTED.md#12-configuration-reference).

**Endpoints.** `GET /health` (liveness) and `GET /ready` (the control
plane is reachable; `503` if not) are unauthenticated and return a bare
status word only. `POST /mcp` is the only route that serves MAK4I tools.
With OAuth enabled, the discovery documents under `/.well-known/` and the
authorization server's `/oauth/…` endpoints and sign-in pages are also
served (unauthenticated by design). A reverse proxy that serves MAK4I
under a path prefix must strip the prefix for `/mcp` and `/oauth/…`, and
must pass `/.well-known/oauth-protected-resource/<prefix>/mcp` and
`/.well-known/oauth-authorization-server/<prefix>` through unchanged.

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
directory, and don't put it on a network filesystem.

### Replicas

This release supports **one MAK4I server process per installation**.
Besides `LocalJSONStore`, three pieces of state are per process: the OAuth
rate limiter, the MCP session-to-principal binding, and in-flight
streamable-HTTP sessions. Durable state (identities, OAuth, conflict
resolutions, audit) is in the database and is replica-safe, but running
several replicas has not been tested and isn't supported in this release;
multi-node, active-active or managed-HA deployments are out of scope.

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
- Authentication is a per-principal bearer credential or, when enabled,
  an OAuth access token from the built-in authorization server
  (MAK-0008). The two are distinguished by prefix and never fall back to
  each other. OAuth state (codes, tokens, refresh rotation, revocation)
  is stored only as hashes in the control-plane database and survives
  restarts; no signing keys are used. OAuth rate limits are per process,
  which matches the single-replica deployment this release supports.
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
