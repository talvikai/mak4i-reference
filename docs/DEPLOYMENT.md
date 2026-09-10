# Deploying the MAK4I MCP server

This document describes the **general shape** of a hosted MAK4I MCP
server deployment — the runtime contract, the authorization model, the
database migration step, and the local development path. It is written so
that someone standing up their **own** MAK4I reference deployment can do
so without reverse-engineering it from code.

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

Operator-specific material for any *particular* hosted instance — the GCP
project, resource names, one-time provisioning commands, deploy runbook,
service-account operations, Secret Manager administration — is not in
this repository. If you are an external developer wanting to *use* a
hosted instance rather than run your own, you deploy nothing at all — see
the "Hosted Developer Preview" section of the top-level `README.md`.

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
| `MAK4I_TRANSPORT` | for HTTP serving | `streamable-http` for a hosted server; `stdio` (default) for local single-user use |
| `MAK4I_CONTROL_PLANE_DB` | yes | SQLAlchemy URL for the control plane. Defaults to `sqlite:///./mak4i-control-plane.db`. Hosted: a `postgresql+psycopg://…` URL. **Keep this in a secret store, never a plaintext env literal or the repo** — it contains the database password. |
| `MAK4I_STORE` | yes | `local` (default, `LocalJSONStore`) or `gcs` (`GCSArtifactStore`) |
| `MAK4I_LOCAL_STORE_DIR` | if `MAK4I_STORE=local` | directory for `LocalJSONStore` (default `artifacts/local/`) |
| `MAK4I_GCS_BUCKET` | if `MAK4I_STORE=gcs` | bucket name for `GCSArtifactStore` |
| `MAK4I_GCP_PROJECT` | if `MAK4I_STORE=gcs` | GCP project for the GCS client |
| `MAK4I_CONTROL_PLANE_CREATE_TABLES` | no | `1` builds the control-plane schema directly from metadata (dev/CI convenience against a throwaway SQLite file). Leave unset for a real database — migrate with Alembic instead. |
| `MAK4I_TOKEN` | stdio only | the one credential a local stdio session authenticates for its lifetime |

The same variables are read by `cli.py` and `mcp_server.py`, so the CLI
and the server always agree about which backends they mean.

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

## Applying the control-plane schema

The control plane is migrated with Alembic (`migrations/`, config in
`alembic.ini`, URL read from `MAK4I_CONTROL_PLANE_DB` by
`migrations/env.py`):

```bash
MAK4I_CONTROL_PLANE_DB="<your control-plane URL>" uv run alembic upgrade head
```

Run this once before first serving traffic, and again after pulling
changes that add a migration. For a throwaway local SQLite database you
can instead set `MAK4I_CONTROL_PLANE_CREATE_TABLES=1` and skip Alembic.

## Deploying the container

The server is a standard container: build the image from the root
`Dockerfile`, push it to a registry your runtime can pull from, and run
it with the environment above (`MAK4I_TRANSPORT=streamable-http`, an
artifact-store selection, and `MAK4I_CONTROL_PLANE_DB` supplied from a
secret rather than a literal). Give it a generous request timeout —
Streamable HTTP sessions are long-lived (an idle session should survive a
30s idle gap; the reference deployment uses the platform maximum).

Platform-specific provisioning (creating the database, the bucket, the
runtime identity, the secret, and the deploy invocation itself) depends
entirely on where you host it and is out of scope for this repo.

## Local stdio dev path

```bash
uv run python -m mak4i.mcp_server
```

Defaults to `MAK4I_STORE=local` (`LocalJSONStore`, `artifacts/local/`),
`MAK4I_TRANSPORT=stdio`, and `MAK4I_CONTROL_PLANE_DB` = a local SQLite
file. stdio also requires `MAK4I_TOKEN` — a credential authenticated once
at process startup, since a local stdio session is single-user for its
whole lifetime.

See **`docs/LOCAL_SETUP.md`** for the full local path: bootstrapping a
control plane, issuing yourself a credential, and running the server and
the CLI against it.
