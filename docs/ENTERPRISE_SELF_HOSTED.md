# Enterprise Self-Hosted: Developer Preview Quick Start

This guide takes you from a clean Linux VM to a running MAK4I Enterprise
Self-Hosted MCP server that several remote AI clients share. Follow it top
to bottom; every command is meant to be copied as written.

> **What this is:** a single-VM reference deployment for evaluating MAK4I
> as a Developer Preview. It uses Docker Compose with PostgreSQL (the
> control plane), `LocalJSONStore` on a Docker volume (the artifacts), and
> optional automatic HTTPS through Caddy.
>
> **What this isn't:** the production architecture. For managed
> PostgreSQL, object storage, a container platform, and a secret manager,
> see [`DEPLOYMENT.md` → Production deployment options](DEPLOYMENT.md#production-deployment-options).
> `DEPLOYMENT.md` is also the reference for every environment variable,
> endpoint, and security property this guide relies on.

> **Do not use `mak4i init` here.** `mak4i init` / `mak4i serve` are for
> [Local setup](LOCAL_SETUP.md) only: `init` always creates its own local
> SQLite environment and ignores the PostgreSQL database this stack runs.
> Enterprise Self-Hosted is bootstrapped with the explicit commands in
> [step 7](#7-bootstrap-your-organization).

## Contents

1. [What you'll end up with](#1-what-youll-end-up-with)
2. [Prerequisites](#2-prerequisites)
3. [Get the code](#3-get-the-code)
4. [Configure](#4-configure)
5. [Start the stack](#5-start-the-stack)
6. [Verify](#6-verify)
7. [Bootstrap your organization](#7-bootstrap-your-organization)
8. [Connect two AI clients](#8-connect-two-ai-clients)
9. [Verify shared project context](#9-verify-shared-project-context)
10. [Add people and projects](#10-add-people-and-projects)
11. [Operate: backups, upgrades, logs](#11-operate-backups-upgrades-logs)
12. [Troubleshooting](#12-troubleshooting)
13. [Moving beyond the quick start](#13-moving-beyond-the-quick-start)

---

## 1. What you'll end up with

```
 AI client A ─┐                        ┌─────────────── one Linux VM (Docker Compose) ───────────────┐
 AI client B ─┼─ HTTPS :443 ──────────▶│ caddy ──▶ mak4i (MCP server, uid 10001) ──▶ postgres:16      │
  (Bearer     │  (--profile tls)       │             │                               (volume pgdata) │
  credential) │                        │             └──▶ /data/artifacts (volume artifacts)          │
              └─ or SSH tunnel ───────▶│ 127.0.0.1:8080 (plain HTTP, host-local only)                  │
                                       └──────────────────────────────────────────────────────────────┘
```

Four services, all defined in [`deploy/compose/compose.yaml`](../deploy/compose/compose.yaml):

| Service | What it does |
|---|---|
| `postgres` | Control-plane database (organizations, principals, projects, grants, credential hashes). **Never published on the host.** |
| `migrate` | One-shot `alembic upgrade head`. Runs on every `up`, then exits `0`. `mak4i` doesn't start until it succeeds. |
| `mak4i` | The MCP server (Streamable HTTP) and the `mak4i` operator CLI. Runs as a non-root user. One replica only. |
| `caddy` | Optional (`--profile tls`). Gets and renews a TLS certificate for your domain and proxies to `mak4i`. |

Data lives in two named Docker volumes, `mak4i_pgdata` and
`mak4i_artifacts`, which survive `docker compose down`, restarts, and
upgrades.

## 2. Prerequisites

- A Linux VM (any distribution Docker supports). A small VM, for example
  2 vCPU and 2 GB RAM, is plenty for an evaluation.
- **Docker Engine with the Compose v2 plugin.** Check with:
  ```bash
  docker compose version     # must print v2.x or later
  ```
  The commands below assume your user can run `docker` (member of the
  `docker` group); otherwise prefix them with `sudo`.
- `git`, `curl`, and `openssl` (preinstalled on most distributions).
- **For remote SaaS clients such as Claude.ai:** a DNS name pointing at
  the VM (an `A`/`AAAA` record), with inbound TCP **80** and **443** open.
  Nothing else needs to be open. Without a domain you can still run the
  stack and reach it through an SSH tunnel (see [step 5](#5-start-the-stack)).

## 3. Get the code

```bash
git clone https://github.com/talvikai/mak4i-reference.git
cd mak4i-reference/deploy/compose
```

**Run every remaining command in this guide from `deploy/compose/`.**

## 4. Configure

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and set these values (the file explains each one):

| Variable | Set it to |
|---|---|
| `POSTGRES_PASSWORD` | Output of `openssl rand -hex 32`. Use letters and digits only, because the value is embedded in a database URL. |
| `MAK4I_DOMAIN` | Your DNS name, e.g. `mak4i.example.com` (TLS profile only). |
| `MAK4I_PUBLIC_ENDPOINT` | The full URL clients will use, **including `/mcp`**: `https://mak4i.example.com/mcp` with TLS, or `http://127.0.0.1:8080/mcp` without. |

Leave `MAK4I_HTTP_BIND=127.0.0.1` unless you have a specific reason.
Anything else serves bearer credentials over unencrypted HTTP to your
network.

> **Secrets: Developer Preview vs. production.** Keeping the database
> password in a `chmod 600` `.env` file on a single VM is acceptable for
> this Developer Preview. A production deployment keeps the control-plane
> database URL in a secret manager, never in a plaintext file. See
> [`DEPLOYMENT.md` → Security considerations](DEPLOYMENT.md#security-considerations).

Everything else (transport, bind address, storage backend, artifact
path) is fixed in `compose.yaml`. The full list of variables is in
[`DEPLOYMENT.md` → Runtime configuration](DEPLOYMENT.md#runtime-configuration).

## 5. Start the stack

**With HTTPS (recommended; required for Claude.ai and other SaaS clients):**

```bash
docker compose --profile tls up -d --build --wait
```

**Without a domain (evaluation over an SSH tunnel):**

```bash
docker compose up -d --build --wait
```

In this mode MAK4I listens only on the VM's `127.0.0.1:8080`. To reach it
from your workstation, open a tunnel and keep it running:

```bash
ssh -N -L 8080:127.0.0.1:8080 <you>@<vm-address>
```

Clients on that workstation then use `http://127.0.0.1:8080/mcp`. SaaS
clients such as Claude.ai can't reach a tunnel; they need the TLS profile.

The first build takes a minute or two. `--wait` returns once every
service is healthy (or has exited successfully, in `migrate`'s case).

> If you started with `--profile tls`, pass `--profile tls` to every later
> `docker compose up`/`down`/`ps` as well, so the `caddy` service is
> included.

## 6. Verify

```bash
docker compose ps -a
```

Expected: `postgres` **healthy**, `migrate` **Exited (0)**, `mak4i`
**healthy**, and, with TLS, `caddy` **Up**.

```bash
curl -fsS http://127.0.0.1:8080/health; echo      # -> ok      (process is up)
curl -fsS http://127.0.0.1:8080/ready;  echo      # -> ready   (database reachable)
curl -fsS https://<MAK4I_DOMAIN>/ready; echo      # -> ready   (TLS profile: end to end)
```

Confirm the schema migration was applied:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select version_num from alembic_version"'
```

An unauthenticated request to the MCP endpoint must be refused:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/mcp   # -> 401
```

If anything here fails, see [Troubleshooting](#12-troubleshooting).

## 7. Bootstrap your organization

A fresh deployment has no organizations, principals, or credentials. You
create them with the `mak4i` CLI, which runs **inside the `mak4i`
container** via `docker compose exec`. It uses the same database as the
server, and the database is never exposed.

Why every command below passes `--actor`: until step 7.4, no credential
exists to identify you, so you name the acting owner explicitly. That's
also why the control-plane database must never be reachable from outside.
Anyone who can connect to it can act as any principal.

**7.1 — Create the organization and its owner principal**

```bash
docker compose exec mak4i mak4i org create --name "Acme Corp" --owner-display-name "Platform Admin"
```

Output (abridged):

```json
{
  "organization": { "organization_id": "org_…", "name": "Acme Corp", … },
  "owner":        { "principal_id": "prn_…", "role": "owner", … }
}
```

Save both IDs in shell variables for the next commands:

```bash
ORG=org_…      # organization.organization_id
OWNER=prn_…    # owner.principal_id
```

**7.2 — Create a project** (the boundary that knowledge and access are scoped to)

```bash
docker compose exec mak4i mak4i project create --actor "$OWNER" --organization-id "$ORG" --name "Platform"
```

```bash
PROJECT=prj_…  # project_id from the output
```

**7.3 — Grant the owner read/write on the project**

```bash
docker compose exec mak4i mak4i grant create --actor "$OWNER" \
  --principal-id "$OWNER" --project-id "$PROJECT" --permissions read,write
```

**7.4 — Issue a credential for your first AI client**

```bash
docker compose exec mak4i mak4i credential issue --actor "$OWNER" \
  --principal-id "$OWNER" --display-name "Owner - Claude Code"
```

The output shows the raw token **once**, followed by a ready-to-paste
connect command built from `MAK4I_PUBLIC_ENDPOINT`:

```
Credential created.

credential_id: cred_…
Authorization: Bearer mak4i_…

Save this token now. It will not be shown again.

Connect an AI client:

MCP endpoint: https://mak4i.example.com/mcp

  claude mcp add mak4i --transport http https://mak4i.example.com/mcp \
    --header "Authorization: Bearer mak4i_…"
```

Store the token somewhere safe. MAK4I keeps only its hash, so a lost
token can't be recovered: revoke it and issue a new one
([step 10](#10-add-people-and-projects)).

**7.5 — Add a second principal for the second client**

Two clients could share the owner's identity (issue a second credential
for `$OWNER`). A more realistic check is a second person with their own
principal and access to the same project:

```bash
docker compose exec mak4i mak4i principal create --actor "$OWNER" \
  --organization-id "$ORG" --type human --display-name "Teammate"
```

```bash
TEAMMATE=prn_…  # principal_id from the output
```

```bash
docker compose exec mak4i mak4i grant create --actor "$OWNER" \
  --principal-id "$TEAMMATE" --project-id "$PROJECT" --permissions read,write

docker compose exec mak4i mak4i credential issue --actor "$OWNER" \
  --principal-id "$TEAMMATE" --display-name "Teammate - Claude.ai"
```

Save the second token too.

> **Optional:** once you hold the owner's token, you can stop passing
> `--actor`. The CLI derives the actor by authenticating the credential:
> `docker compose exec -e MAK4I_TOKEN=<owner token> mak4i mak4i principal list --organization-id "$ORG"`.

## 8. Connect two AI clients

Every MCP client connects the same way: the endpoint URL (ending in
`/mcp`) plus a static `Authorization: Bearer <token>` header.

**Client A: Claude Code, on your workstation, with the owner's token:**

```bash
claude mcp add mak4i --transport http https://mak4i.example.com/mcp \
  --header "Authorization: Bearer <owner token>"
claude mcp get mak4i        # should report the server as connected
```

**Client B: Claude.ai (or Cowork), with the teammate's token.** Go to
**Customize → Connectors → Add custom connector** and set:

- URL: `https://mak4i.example.com/mcp`
- Authentication: None
- Request header: name `authorization`, value `Bearer <teammate token>`

Any other MCP client that supports a static header works the same way,
including a second Claude Code on another machine and Gemini CLI. See
[`DEMO.md` → Connecting a client](DEMO.md#connecting-a-client) for each
client's exact steps and known limitations.

## 9. Verify shared project context

Knowledge written through one client must be usable from the other.

1. In **client A**, ask: *"List my MAK4I projects."* You should see
   `Platform` with read and write permissions.
2. Still in **client A**: *"Record in MAK4I, project Platform, that our
   caching decision is an in-process LRU cache with no external cache
   service."* The client calls `mak4i_create`.
3. In **client B** (a different principal, a different product):
   *"Check MAK4I: what is our current caching decision for the Platform
   project?"* It calls `mak4i_get_current` and answers with the decision
   recorded in client A.

You can also confirm on the server, as the teammate:

```bash
docker compose exec mak4i mak4i get-current --principal "$TEAMMATE" --project "$PROJECT"
```

Your Enterprise Self-Hosted deployment is working. For a longer tour
(supersede, history, conflicts, denial of an unauthorized project), see
[`DEMO.md`](DEMO.md#a-full-walkthrough).

## 10. Add people and projects

All of these run as `docker compose exec mak4i mak4i …` and are
owner-scoped (an owner can never see or change another organization's
entities):

| Task | Command |
|---|---|
| Add a person, service, or agent | `principal create --actor "$OWNER" --organization-id "$ORG" --type human\|service\|agent --display-name "…"` |
| Add a project | `project create --actor "$OWNER" --organization-id "$ORG" --name "…"` |
| Give access | `grant create --actor "$OWNER" --principal-id … --project-id … --permissions read` (or `read,write`) |
| Remove access (immediate) | `grant revoke --actor "$OWNER" --principal-id … --project-id …` |
| Issue a client credential | `credential issue --actor "$OWNER" --principal-id … --display-name "…"` (optional `--expires-at`) |
| Revoke a credential (immediate) | `credential revoke --actor "$OWNER" --credential-id cred_…` |
| Inspect | `org show`, `project list`, `principal list`, `grant list`, `credential list` (never prints tokens) |
| Onboard an external collaborator in one step | `access provision` (new org + member principal + project + grant + credential; uses `MAK4I_PUBLIC_ENDPOINT`) |

Full command reference: [`LOCAL_SETUP.md` → Section 3](LOCAL_SETUP.md#section-3--manual--advanced-setup).
The commands behave the same against this PostgreSQL control plane.

## 11. Operate: backups, upgrades, logs

### Backups

Back up both stores, on the same schedule. They're independent, and a
complete recovery needs both. See [`DEPLOYMENT.md` → Backups](DEPLOYMENT.md#backups).

```bash
# Control plane (PostgreSQL custom-format dump)
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > mak4i-control-plane-$(date +%F).dump

# Artifacts (the LocalJSONStore volume)
docker compose exec -T mak4i tar -C /data -cf - artifacts > mak4i-artifacts-$(date +%F).tar
```

Restore into a freshly started stack (after `up`, before bootstrapping):

```bash
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
  < mak4i-control-plane-YYYY-MM-DD.dump
docker compose exec -T mak4i tar -C /data -xf - < mak4i-artifacts-YYYY-MM-DD.tar
docker compose restart mak4i
```

Also keep a copy of `.env`. Without the same `POSTGRES_PASSWORD`, the
existing `pgdata` volume can't be opened.

### Upgrades

```bash
git pull
docker compose up -d --build --wait     # add --profile tls if you use it
```

`migrate` applies any new migration automatically before `mak4i`
restarts. Back up first. The general procedure is in
[`DEPLOYMENT.md` → Upgrading](DEPLOYMENT.md#upgrading).

### Logs and audit

```bash
docker compose logs -f mak4i      # structured JSON audit events, one per line
docker compose logs migrate       # migration run (empty output + exit 0 = success)
```

### Stop, start, remove

```bash
docker compose down        # stops and removes containers; volumes (your data) are kept
docker compose up -d --wait
docker compose down -v     # DANGER: also deletes the pgdata and artifacts volumes
```

Services use `restart: unless-stopped`, so the stack comes back after a
VM reboot as long as Docker starts at boot.

## 12. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `required variable POSTGRES_PASSWORD is missing a value` | `.env` not created or the password is empty. Run the step 4 commands again. |
| `migrate` exits non-zero | `docker compose logs migrate`. Usually the password contains URL-unsafe characters: use `openssl rand -hex 32`. If you change the password after the first start, the existing `pgdata` volume still has the old one. |
| `/ready` returns `503` / `mak4i` unhealthy | Control-plane database unreachable. Check `docker compose ps` (is `postgres` healthy?) and `docker compose logs mak4i`. |
| `/health` works on the VM but clients can't connect | Without TLS the port is bound to `127.0.0.1` on purpose. Use the SSH tunnel or the TLS profile. |
| `caddy` restarting / no certificate | `docker compose --profile tls logs caddy`. Check that `MAK4I_DOMAIN` is set, DNS points at this VM, and ports 80 and 443 are reachable from the internet. |
| Client gets `401` | Missing, wrong, revoked, or expired token, or the header lacks the literal `Bearer ` prefix. The server deliberately doesn't say which. |
| Client gets `404` | The URL doesn't end in `/mcp`. |
| Client gets `421 Misdirected Request` | MAK4I is bound to loopback behind a proxy. In this stack `compose.yaml` sets `MAK4I_HOST=0.0.0.0`. Don't override it. See [`DEPLOYMENT.md` → Reverse proxy / TLS requirements](DEPLOYMENT.md#reverse-proxy--tls-requirements). |
| Sessions drop or responses stall behind your own proxy | Your proxy is buffering or timing out streamed responses. Same `DEPLOYMENT.md` section. |
| `access denied` from a CLI command | `--actor` isn't an owner in that organization, or the entity belongs to another organization. |
| Permission errors writing artifacts after switching to a bind mount | The container runs as uid `10001`. `chown -R 10001 <dir>` on the host, or keep the named volume. |

## 13. Moving beyond the quick start

This stack is intentionally one VM, one MAK4I replica, and a `.env` file.
Before production use, see
[`DEPLOYMENT.md` → Production deployment options](DEPLOYMENT.md#production-deployment-options)
for:

- a managed PostgreSQL service with backups and point-in-time recovery,
- an object-storage `ArtifactStore` (required for more than one MAK4I
  replica; `LocalJSONStore` is single-writer),
- a container platform with health-checked rollouts,
- control-plane credentials held in a secret manager,
- TLS termination at your load balancer or ingress.
