# Enterprise Self-Hosted

The one guide for installing and operating MAK4I Enterprise Self-Hosted:
a shared MAK4I MCP server, on one Linux VM, that several people and remote
AI clients connect to over HTTPS.

**Audience:** cloud administrators, platform engineers, DevOps and SRE.

> **Developer Preview.** This is a release candidate (`v2.0.0-rc.1`) for
> evaluation and feedback, not a production-supported release. It is a
> single-VM reference deployment: Docker Compose with PostgreSQL (the
> control plane), artifacts on a Docker volume, and Caddy for HTTPS.
> Architecture and production options: [`DEPLOYMENT.md`](DEPLOYMENT.md).
>
> Running MAK4I only on your own machine? Use
> [`LOCAL_SETUP.md`](LOCAL_SETUP.md) instead. Don't use `mak4i init` or
> `mak4i serve` for Enterprise Self-Hosted.

## Contents

1. [What you get](#1-what-you-get)
2. [Who provides what](#2-who-provides-what)
3. [Requirements](#3-requirements)
4. [Get the release](#4-get-the-release)
5. [Install with the bootstrap](#5-install-with-the-bootstrap)
6. [Create your organization and access](#6-create-your-organization-and-access)
7. [Connect AI clients](#7-connect-ai-clients)
8. [Operate](#8-operate)
9. [Upgrade from v0.1.0-rc.5](#9-upgrade-from-v010-rc5)
10. [Uninstall](#10-uninstall)
11. [Manual Compose procedure](#11-manual-compose-procedure)
12. [Configuration reference](#12-configuration-reference)
13. [Troubleshooting](#13-troubleshooting)
14. [Bootstrap reference](#14-bootstrap-reference)
15. [Beyond this release](#15-beyond-this-release)

---

## 1. What you get

```
 AI client A ─┐                        ┌──────────────── one Linux VM (Docker Compose) ────────────────┐
 AI client B ─┼─ HTTPS :443 ──────────▶│ caddy ──▶ mak4i (MCP server, uid 10001) ──▶ postgres (private)  │
  (Bearer     │  (tls profile)         │   :80/:443    │ 127.0.0.1:8080 only              volume pgdata  │
  credential) │                        │               └──▶ /data/artifacts (volume artifacts)          │
              └─ private-http profile ▶│ MAK4I HTTP on a private address (your TLS load balancer)       │
                                       └────────────────────────────────────────────────────────────────┘
```

| Service | What it does | Exposure |
|---|---|---|
| `postgres` | Control-plane database: organizations, principals, projects, grants, credential hashes. | **Never published.** Only reachable inside Compose. |
| `migrate` | Applies database migrations (`alembic upgrade head`) on every start, then exits `0`. | None. |
| `mak4i` | The MCP server (Streamable HTTP) and the `mak4i` operator CLI. Non-root (uid 10001), all Linux capabilities dropped, one replica. | `127.0.0.1:8080` (tls profile), or the private address you choose (private-http). |
| `caddy` | tls profile only: obtains and renews the certificate and proxies HTTPS to `mak4i`. | `0.0.0.0:80` and `:443`. |

Data lives in the Docker volumes `mak4i_pgdata` and `mak4i_artifacts`
(plus `mak4i_caddy_data` and `mak4i_caddy_config` with TLS). Volumes
survive restarts, upgrades and a data-preserving uninstall.

## 2. Who provides what

MAK4I's bootstrap never creates or changes cloud resources, DNS records,
firewall rules or IAM. It checks for them and tells you what's missing.

| You (the cloud or platform administrator) provide | The bootstrap provides |
|---|---|
| The Linux VM | Preflight validation of the host, network and configuration |
| A static IP (or a load balancer) | A secure local configuration (`.env`, mode 600, random database password) |
| DNS: an `A` record for your MAK4I domain | Docker Compose orchestration with pinned images |
| Firewall / network policy: inbound TCP 80 and 443 | Database migrations |
| Docker Engine and the Docker Compose plugin | Health, readiness and HTTPS verification |
| Integration with your secrets management, where required | Lifecycle commands: status, restart, backup, restore, upgrade, uninstall |

## 3. Requirements

### 3.1 Host

| | Requirement |
|---|---|
| Operating system | Linux, x86-64. **Tested:** Ubuntu 22.04 / 24.04, Debian 12 / 13. Other distributions that run Docker Engine are expected to work; preflight warns. arm64 isn't tested in this release. Windows and macOS hosts aren't supported for Enterprise Self-Hosted. |
| CPU | 2 vCPU or more |
| Memory | 4 GiB or more recommended; 2 GiB minimum (preflight fails below that) |
| Disk | 20 GiB free for Docker recommended; 10 GiB minimum |
| Software | Docker Engine with the Docker Compose v2 plugin, `git`, `curl`. `dig` (optional) improves DNS diagnostics. |
| User | A non-root user that can run `docker` (member of the `docker` group). |

Install Docker Engine from your distribution or Docker's own packages
(<https://docs.docker.com/engine/install/>), then:

```bash
sudo usermod -aG docker "$USER"   # then log out and back in
docker compose version            # must print v2.x or later
```

### 3.2 Network

| | Requirement |
|---|---|
| Public address | An external IP for the VM. A **static** IP is recommended, because your DNS record points directly at it. |
| DNS | An `A` record: your MAK4I domain → the VM's external IP. If a CDN proxies the name (for example Cloudflare's orange cloud), set it to **DNS only** until the first certificate has been issued. |
| Inbound | TCP **80** and **443** from the internet (tls profile). Nothing else. |
| Outbound | HTTPS to Docker Hub (images), `ghcr.io` (build tooling) and, with the tls profile, Let's Encrypt (`acme-v02.api.letsencrypt.org`). |
| Never public | **PostgreSQL** (not published at all) and **MAK4I's port 8080** (bound to `127.0.0.1` in the tls profile). Anyone who can reach the database can act as any principal. |

### 3.3 Cloud notes

**Verified on Google Cloud** (RC3 deployment, `mak4i-enterprise.<domain>`):

- A fresh Debian VM with 2 vCPU and about 8 GB RAM was used; Docker Engine
  and the Docker Compose plugin were required.
- The VM needed an external IP; a static IP was used because DNS points
  directly to it. The DNS zone was on Cloudflare, with an `A` record for
  the MAK4I domain, kept **DNS only** during the first certificate request.
- **GCP blocked inbound 80 and 443 by default.** Caddy was listening, but
  external connections timed out until HTTP and HTTPS traffic was allowed
  to the VM (the VM's "Allow HTTP/HTTPS traffic" firewall setting, or an
  equivalent VPC firewall rule for `tcp:80,443`).
- Certificate issuance first failed with **NXDOMAIN** while the new DNS
  record hadn't propagated, then with **connection timeouts** while
  80/443 were blocked. Once DNS resolved and the firewall allowed 80/443,
  Caddy obtained the Let's Encrypt certificate automatically.
- The VM kept a **cached negative DNS answer** for a while after the
  authoritative DNS already resolved. `curl --resolve` confirmed the
  service in the meantime (see [Troubleshooting](#13-troubleshooting)).
- MAK4I's port 8080 stayed on `127.0.0.1` and PostgreSQL stayed
  unpublished. `/health` and `/ready` answered over public HTTPS, and a
  restart preserved the deployment, projects, principals and permission
  enforcement.

**AWS and Azure (general guidance, not yet verified with this release):**

- AWS: an EC2 instance with an Elastic IP; a security group allowing
  inbound TCP 80 and 443; your DNS `A` record → the Elastic IP.
- Azure: a VM with a static public IP; a network security group allowing
  inbound TCP 80 and 443; your DNS `A` record → the public IP.
- On both, the bootstrap reads the public IP from instance metadata
  (read-only) to compare it with DNS. Pass `--public-ip` if that isn't
  available.

## 4. Get the release

```bash
git clone --branch v2.0.0-rc.1 --depth 1 https://github.com/talvikai/mak4i-reference.git
cd mak4i-reference
```

Run every command in this guide from this `mak4i-reference` directory
unless it says otherwise.

## 5. Install with the bootstrap

The bootstrap, `deploy/bootstrap/mak4i-enterprise`, is the recommended
way to install and operate this release. (The
[manual Compose procedure](#11-manual-compose-procedure) is kept for
troubleshooting, air-gapped or custom environments, and administrators who
want full control.)

### 5.1 Choose a profile

| Profile | Use it when | What's exposed |
|---|---|---|
| `tls` | Remote SaaS clients (Claude.ai and others) or anyone on the internet will connect. | Caddy on 80/443 with an automatic certificate. MAK4I on `127.0.0.1:8080` only. |
| `private-http` | Clients reach MAK4I over a trusted private network, or you terminate TLS on your own load balancer or reverse proxy. | MAK4I's plain HTTP on `127.0.0.1` (default) or the private address you pass with `--bind`. |

> **`private-http` is plain HTTP.** Bearer credentials cross that network
> unencrypted. Bind it to loopback (use an SSH tunnel) or a private address
> behind your TLS load balancer. The bootstrap warns for a private address
> and refuses a public one unless you pass `--allow-public-bind`, which you
> should only use behind a firewall that admits nothing but your load
> balancer. Binding to all interfaces (`0.0.0.0`) is never safe on its own.

### 5.2 Preflight

Preflight only reads; it changes nothing:

```bash
./deploy/bootstrap/mak4i-enterprise preflight --profile tls --domain mak4i.example.com
```

Expected: one line per check, then a summary. Every `[FAIL]` must be fixed
before `install` will run; `[WARN]` lines are advisory.

```
  [PASS] Linux distribution                 Debian GNU/Linux 12 (bookworm)
  [PASS] Docker Engine                      running (server 29.1.3), usable by admin
  [PASS] DNS A record                       mak4i.example.com -> 203.0.113.10 (this VM, from gcp)
  [PASS] Local port 80                      free
  [PASS] Port 8080 exposure                 127.0.0.1 only; PostgreSQL is never published
  [INFO] Inbound 80/443 from the internet   not tested (add --check-inbound). ...
  ...
Preflight: 18 passed, 0 warning(s), 0 failed.
```

`--check-inbound` also tests that port 80 is reachable from the internet by
briefly running a throwaway responder container on port 80. The VM can't
always reach its own public IP, so if it reports a warning, test from
another machine: `curl -v http://mak4i.example.com/`.

### 5.3 Install

**tls profile:**

```bash
./deploy/bootstrap/mak4i-enterprise install --profile tls --domain mak4i.example.com
```

**private-http profile** (loopback; use an SSH tunnel or a local proxy):

```bash
./deploy/bootstrap/mak4i-enterprise install --profile private-http
```

Behind your own TLS load balancer, bind to the VM's private address and
give the URL clients will use:

```bash
./deploy/bootstrap/mak4i-enterprise install --profile private-http \
  --bind 10.0.0.12 --endpoint https://mak4i.internal.example.com/mcp
```

Optional: `--instance-name "Acme MAK4I" --environment production` sets how
this installation names itself to AI clients (see
[Connect AI clients](#7-connect-ai-clients)).

`install`:

1. runs preflight (and stops on any FAIL, changing nothing);
2. writes `deploy/compose/.env` with mode 600 and a random database
   password (never printed, never passed on a command line);
3. validates the Compose configuration and pulls the pinned images;
4. builds the MAK4I image and starts the stack, running migrations first;
5. verifies `/health`, `/ready` and that the schema is at the latest
   migration;
6. with `tls`, waits (up to 5 minutes, `--cert-timeout`) for a valid
   certificate, verified over HTTPS on this host;
7. prints the endpoint and the next steps.

Expected ending:

```
MAK4I Enterprise v2.0.0-rc.1 is installed and healthy.
  Profile:   tls
  Endpoint:  https://mak4i.example.com/mcp
  ...
```

**If the certificate isn't ready in time**, `install` exits with code 5
after printing Caddy's recent certificate messages. MAK4I itself is
running; only HTTPS isn't ready yet, and Caddy keeps retrying on its own.
The first certificate request often fails until **public DNS has
propagated and ports 80 and 443 are reachable**. Fix what the messages
point to (see [Troubleshooting](#13-troubleshooting)), then check with
`./deploy/bootstrap/mak4i-enterprise status`.

**If install fails part-way**, it says so and leaves the installation in a
documented `pending` state. Its configuration and any data are kept. Fix
the cause (the message and the log say what failed), then run the **same
install command again** to resume.

**Install is idempotent.** Running it again on a healthy installation
changes nothing: it never regenerates the database password, creates
organizations or replaces credentials. It refuses to overwrite a different
installation: a different profile or domain, existing data volumes without
their `.env`, or a manual installation (adopt that with
[`upgrade`](#9-upgrade-from-v010-rc5)).

### 5.4 Verify

```bash
./deploy/bootstrap/mak4i-enterprise status
```

Expected: every container running and healthy, `/health: ok`,
`/ready: ready`, the schema at the latest migration, the data counts, and
with `tls`, `HTTPS: https://<domain>/ready -> ready (certificate
verified)`, ending with `Healthy.`. From any other machine:

```bash
curl -fsS https://mak4i.example.com/ready; echo    # -> ready
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mak4i.example.com/mcp   # -> 401 (credential required)
```

## 6. Create your organization and access

A fresh installation has no organizations, principals or credentials. You
create them with the `mak4i` operator CLI **inside the `mak4i` container**
(which talks to the private database directly), from
`deploy/compose/`:

```bash
cd deploy/compose
```

With the tls profile, add `--profile tls` after `docker compose` in the
commands below, or leave it out: `exec` works either way.

Every command below passes `--actor` (the acting owner's principal ID)
because no credential exists yet. That's also why the database must never
be reachable from outside: anyone who can connect to it can act as any
principal.

**6.1 Organization and owner**

```bash
docker compose exec mak4i mak4i org create --name "Acme Corp" --owner-display-name "Platform Admin"
```

Output (abridged); keep both IDs:

```json
{ "organization": { "organization_id": "org_…", "name": "Acme Corp" },
  "owner":        { "principal_id": "prn_…", "role": "owner" } }
```

```bash
ORG=org_…      # organization.organization_id
OWNER=prn_…    # owner.principal_id
```

**6.2 Project**

```bash
docker compose exec mak4i mak4i project create --actor "$OWNER" --organization-id "$ORG" --name "Platform"
PROJECT=prj_…  # project_id from the output
```

**6.3 Grants: read/write and read-only**

```bash
# The owner: read and write.
docker compose exec mak4i mak4i grant create --actor "$OWNER" \
  --principal-id "$OWNER" --project-id "$PROJECT" --permissions read,write

# A reviewer who may only read.
docker compose exec mak4i mak4i principal create --actor "$OWNER" \
  --organization-id "$ORG" --type human --display-name "Reviewer"
REVIEWER=prn_…
docker compose exec mak4i mak4i grant create --actor "$OWNER" \
  --principal-id "$REVIEWER" --project-id "$PROJECT" --permissions read
```

A read-only principal can use `mak4i_list_projects`, `mak4i_get_current`,
`mak4i_search` and `mak4i_history`; `mak4i_create` and `mak4i_supersede`
are denied.

**6.4 Credentials**

```bash
docker compose exec mak4i mak4i credential issue --actor "$OWNER" \
  --principal-id "$OWNER" --display-name "Owner - Claude Code"
```

The raw token is shown **once**, followed by a ready-to-paste connect
command for your endpoint:

```
credential_id: cred_…
Authorization: Bearer mak4i_…
Save this token now. It will not be shown again.
```

MAK4I stores only a hash of the token, so a lost token can't be recovered:
revoke it and issue a new one.

| Task | `docker compose exec mak4i mak4i …` |
|---|---|
| Add a person or service | `principal create --actor "$OWNER" --organization-id "$ORG" --type human\|service --display-name "…"` |
| Add an agent | `principal create --actor "$OWNER" --organization-id "$ORG" --type agent --agent-id release-bot --display-name "…"` — `--agent-id` is required, unique in the organization, permanent and never reused; records the agent writes carry it as authenticated provenance |
| Rename (history keeps the old name) | `principal rename --actor "$OWNER" --principal-id … --display-name "…"` |
| Deactivate (immediate; history kept) | `principal deactivate --actor "$OWNER" --principal-id …` — every credential and OAuth authorization of the principal stops working |
| Add a project | `project create --actor "$OWNER" --organization-id "$ORG" --name "…"` |
| Give access | `grant create --actor "$OWNER" --principal-id … --project-id … --permissions read` (or `read,write`) |
| Remove access (immediate) | `grant revoke --actor "$OWNER" --principal-id … --project-id …` |
| Issue a credential | `credential issue --actor "$OWNER" --principal-id … --display-name "…"` (optional `--expires-at`) |
| Revoke a credential (immediate) | `credential revoke --actor "$OWNER" --credential-id cred_…` |
| Inspect (never prints tokens) | `org show`, `project list`, `principal list`, `grant list`, `credential list` |
| Give conflict-resolution rights | `grant create … --permissions read,write,resolve` (`resolve` lets a principal settle conflicts; it never grants administration) |
| Archive a project (nothing deleted; access stops) | `project archive --actor "$OWNER" --project-id …` |
| See who changed what | `audit list --actor "$OWNER"` — every administrative action, including denied attempts, with actor and time; never secrets |
| Recover lost ids (organization, owner, projects) | `./deploy/bootstrap/mak4i-enterprise admin-info` (or `mak4i admin inventory` in the container) |
| Suspend / reactivate an organization (instance operator) | `org suspend --organization-id …` / `org reactivate --organization-id …` |
| Onboard an external collaborator in one step | `access provision` (new org, member principal, project, grant and credential) |

Once you hold the owner's token you can stop passing `--actor`: pass the
token in the environment (`docker compose exec -e MAK4I_TOKEN mak4i …`,
with `MAK4I_TOKEN` exported in your shell) and the CLI authenticates it.

## 7. Connect AI clients

There are two ways to authenticate a client: a static bearer credential
(any client that can send a header) or OAuth (clients such as claude.ai
that connect only through OAuth — see [7.1](#71-oauth-sign-in)). Both can
be enabled at once.

With a credential, the client connects with the endpoint URL (ending in
`/mcp`) and a static `Authorization: Bearer <token>` header. For example,
Claude Code:

```bash
claude mcp add mak4i-acme --transport http https://mak4i.example.com/mcp \
  --header "Authorization: Bearer <token>"
```

For Claude.ai, Cowork, Gemini CLI and other clients, see
[`DEMO.md` → Connecting a client](DEMO.md#connecting-a-client).

**Several MAK4I connections in one client.** Give each installation a
distinct name (`--instance-name` at install, or `MAK4I_INSTANCE_NAME` in
`.env`) and register it under a distinct client name. MAK4I identifies
itself in `mak4i_whoami`, `mak4i_list_projects` and every access denial,
and tells the client never to retry a denied write on another connection
without the user's confirmation. The server can't enforce what a client
does on *other* connections, so review writes your client proposes when
several MAK4I connections are configured
([#8](https://github.com/talvikai/mak4i-reference/issues/8),
[#9](https://github.com/talvikai/mak4i-reference/issues/9)).

### 7.1 OAuth sign-in

MAK4I includes its own OAuth 2.1 authorization server (MAK-0008): no
external identity provider is needed. A person (or an agent's operator)
signs in on a MAK4I page with a one-time **sign-in code** issued by the
CLI, then approves the client. Tokens never exceed the principal's
project grants, and revoking or deactivating takes effect on the next
request.

**Requirements.** HTTPS on a public name that the client can reach (the
`tls` profile). Hosted clients such as claude.ai connect from the
internet, so a private-http installation can't be used with them.

**Enable it** in `deploy/compose/.env`, then restart the stack:

```bash
MAK4I_PUBLIC_ENDPOINT=https://mak4i.example.com/mcp
MAK4I_OAUTH_ENABLED=1
```

Check discovery (both must return JSON without a token):

```bash
curl -s https://mak4i.example.com/.well-known/oauth-protected-resource/mcp
curl -s https://mak4i.example.com/.well-known/oauth-authorization-server
```

**Connect a client.** Add the server by URL only
(`https://mak4i.example.com/mcp`) and choose OAuth. Clients that publish a
client ID metadata document (for example Claude) register themselves; for
others, an owner pre-registers the client:

```bash
docker compose exec mak4i mak4i oauth client register --actor "$OWNER" \
  --name "My client" --redirect-uri https://client.example/callback
```

**Sign in.** When the client opens the MAK4I sign-in page, get a code
(for yourself, or as an owner for anyone in your organization) and enter
it, then review and approve the consent page:

```bash
docker compose exec mak4i mak4i oauth sign-in-code --actor "$OWNER" --principal-id "$PRINCIPAL"
```

**Revoke.** `mak4i oauth authorization list|revoke` (by authorization,
principal or client) and `mak4i oauth client disable`. Deactivating a
principal or removing its grant also stops its OAuth access at once.

Which clients have been verified end to end is recorded in the release
notes; treat any other client as untested.

## 8. Operate

### 8.1 Status, stop, start and restart

```bash
./deploy/bootstrap/mak4i-enterprise status     # health, readiness, schema, data, HTTPS
./deploy/bootstrap/mak4i-enterprise restart    # restarts and verifies health and data
```

`restart` records the control-plane counts (organizations, principals,
projects, grants, credentials) before restarting and fails if they differ
afterwards. Services use `restart: unless-stopped`, so the stack comes
back after a VM reboot as long as Docker starts at boot
(`sudo systemctl enable docker`).

To stop without removing anything, use the [manual procedure](#113-stop-start-and-logs).

### 8.2 Persistence check

After a restart or reboot, `status` should show the same counts as
before, and existing credentials keep working: a credential lives in the
database until it's revoked or expires, independent of restarts.

### 8.3 Backup

```bash
./deploy/bootstrap/mak4i-enterprise backup --backup-dir /srv/mak4i-backups
```

(Default: `~/mak4i-backups`.) Each backup is a timestamped directory,
mode 700, containing:

| File | Contents |
|---|---|
| `control-plane.dump` | PostgreSQL dump (`pg_dump -Fc`), validated with `pg_restore --list` |
| `artifacts/` | the artifacts volume |
| `artifacts.sha256` | a checksum per artifact file |
| `env` | a copy of `.env`, **including the database password**. Keep backups private. |
| `manifest.env` | release, time, profile, data counts, checksums |

MAK4I is stopped for a few seconds during the copy so the database and
artifacts match; `--online` skips that (less consistent). The bootstrap
refuses to write backups inside the repository (the Docker build context)
or into system directories. Copy backups off the VM on your own schedule.

### 8.4 Restore

```bash
./deploy/bootstrap/mak4i-enterprise restore --from /srv/mak4i-backups/mak4i-backup-20261001T120000Z
```

Restore **replaces all current data** of this installation: the database
contents and the artifacts volume. Credentials issued after the backup stop
working. It verifies every checksum and the backup's release first, asks
you to type `restore` (or pass `--yes` for automation), runs migrations,
and checks that the restored counts match the manifest. Expect MAK4I to be
unavailable for under a minute on small installations. It restores
`v2.0.0-rc.1` backups; the installation's current `.env` is kept.

**On a new VM:** install first (section 5), then restore. Restoring
brings back all organizations, principals, grants and credentials.

### 8.5 Logs

```bash
cd deploy/compose
docker compose logs -f mak4i      # structured JSON audit events, one per line
docker compose logs caddy         # certificates (tls profile)
```

Every bootstrap run is also logged, mode 600, under
`~/.local/state/mak4i-enterprise/logs/`. No secret is ever printed, so none
is logged.

## 9. Upgrade from v0.1.0-rc.5

The supported path is `v0.1.0-rc.5` → `v2.0.0-rc.1`, preserving all data.
An installation on an earlier release upgrades to `v0.1.0-rc.5` first,
following that release's guide.

**What changes for you.** v2.0.0-rc.1 adds database tables and a column
(migrations `0002`–`0005`: OAuth, `agent_id`, conflict resolution records,
administrative audit), and new fields on records written from now on
(provenance, resolution fields). Existing principals, credentials, grants
and records are kept unchanged; existing agent principals get an
`agent_id` of the form `agent-<12 hex digits>`. Read the **breaking
changes** in [`CHANGELOG.md`](../CHANGELOG.md) (conflict object shape,
filters no longer hide one side of a conflict, releasing a subject key
during a conflict is refused) before upgrading clients that rely on them.

```bash
git fetch --depth 1 origin tag v2.0.0-rc.1
git checkout v2.0.0-rc.1
./deploy/bootstrap/mak4i-enterprise upgrade --backup-dir /srv/mak4i-backups
```

`upgrade`:

1. detects the installed version (from the bootstrap state or the running
   container; `--from-version v0.1.0-rc.5` if the stack is stopped) and
   refuses any other path;
2. **takes a verified backup first** (or verifies one you give with
   `--use-backup DIR`);
3. pulls and builds the pinned `v2.0.0-rc.1` images;
4. recreates the containers; the migrations run before MAK4I starts;
5. verifies the version, health, readiness, schema, unchanged data counts
   and HTTPS;
6. prints the rollback steps.

Database, artifacts, certificates, `.env` and every credential are kept.
OAuth stays off until you enable it ([7.1](#71-oauth-sign-in)).

**Rollback.** v0.1.0-rc.5 can't read records written by v2.0.0-rc.1, so
rolling back means restoring the backup `upgrade` took (anything written
after the upgrade is lost):

```bash
git fetch --depth 1 origin tag v0.1.0-rc.5 && git checkout v0.1.0-rc.5   # rollback to the previous release
./deploy/bootstrap/mak4i-enterprise restore --from /srv/mak4i-backups/mak4i-backup-<timestamp>
```

If PostgreSQL reports a **collation version change** after the upgrade
(`upgrade` warns and prints the command), rebuild the indexes once. This
happens when an older installation's PostgreSQL image was built on an
earlier Debian release than the one this release pins.

## 10. Uninstall

### 10.1 Remove the application, keep the data (default)

```bash
./deploy/bootstrap/mak4i-enterprise uninstall
```

Removes the containers and their network. **Keeps** the data volumes
(database, artifacts, certificates), `deploy/compose/.env` (it holds the
database password needed to open the database again) and your backups.
Reinstall with the same data and credentials:

```bash
./deploy/bootstrap/mak4i-enterprise install
```

### 10.2 Remove everything

> **Destructive and irreversible.** Take a backup first if there's any
> chance you'll need the data.

```bash
./deploy/bootstrap/mak4i-enterprise uninstall --destroy-data
```

It lists exactly what it will delete:

- `mak4i_pgdata`: organizations, principals, projects, grants, credentials
  (every issued token stops working);
- `mak4i_artifacts`: every artifact with its lineage and history;
- `mak4i_caddy_data` / `mak4i_caddy_config`: certificates and the ACME
  account (tls profile);
- `deploy/compose/.env`.

You must type `destroy mak4i data` to continue. For automation,
`--destroy-data --yes-destroy-data --non-interactive` confirms without a
prompt. Afterwards it verifies that the volumes are gone. It never deletes
backups, the repository, or the shared `postgres`/`caddy` images;
`--remove-images` also removes the locally built `mak4i-reference:local`
image.

Finally, remove the MCP registration from each AI client (for example
`claude mcp remove mak4i-acme`), or it keeps pointing at a server that no
longer exists, and delete the repository directory if you no longer need
it.

## 11. Manual Compose procedure

For troubleshooting, air-gapped or custom environments, and administrators
who want full control. The bootstrap does all of this for you; don't mix
the two on one installation unless a step says so. Run these from
`deploy/compose/`. The `docker compose` commands are the same in Windows
PowerShell, but Enterprise Self-Hosted is supported on Linux only.

### 11.1 Configure

```bash
cd deploy/compose
cp .env.example .env
chmod 600 .env
```

Edit `.env` (every variable is described in
[Configuration reference](#12-configuration-reference)). At least:

- `POSTGRES_PASSWORD`: the output of `openssl rand -hex 32` (letters and
  digits only; it's embedded in a database URL);
- `MAK4I_DOMAIN` and `MAK4I_PUBLIC_ENDPOINT=https://<domain>/mcp` for TLS,
  or `MAK4I_PUBLIC_ENDPOINT=http://127.0.0.1:8080/mcp` without;
- keep `MAK4I_HTTP_BIND=127.0.0.1` with TLS.

### 11.2 Start and verify

Compose profiles: with `--profile tls`, Caddy is included; without it,
only PostgreSQL, the migration and MAK4I run. Pass the same profile to
every later `up`, `down` and `ps`.

```bash
docker compose --profile tls up -d --build --wait    # or: docker compose up -d --build --wait
docker compose --profile tls ps -a
```

Expected: `postgres` healthy, `migrate` exited (0), `mak4i` healthy,
`caddy` up. Migrations: the `migrate` service runs `alembic upgrade head`
before `mak4i` starts, on every `up`, and is a no-op when the schema is
current. Check the applied revision with:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select version_num from alembic_version"'
curl -fsS http://127.0.0.1:8080/health; echo    # -> ok
curl -fsS http://127.0.0.1:8080/ready;  echo    # -> ready
```

Then [create your organization](#6-create-your-organization-and-access).

### 11.3 Stop, start and logs

```bash
docker compose --profile tls stop     # stop containers; nothing is deleted
docker compose --profile tls start
docker compose --profile tls down     # remove containers and network; volumes (all data) are kept
docker compose --profile tls up -d --wait
docker compose logs migrate           # migration run (exit 0 = success)
```

### 11.4 Backup and restore

The dump is written inside the container and copied out with
`docker compose cp`, which avoids shell redirection (which corrupts binary
files in Windows PowerShell).

```bash
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/mak4i-control-plane.dump'
docker compose exec -T postgres sh -c 'pg_restore --list /tmp/mak4i-control-plane.dump > /dev/null && echo dump-ok'
docker compose cp postgres:/tmp/mak4i-control-plane.dump /srv/mak4i-backups/mak4i-control-plane.dump
docker compose exec -T postgres rm /tmp/mak4i-control-plane.dump
docker compose cp mak4i:/data/artifacts /srv/mak4i-backups/mak4i-artifacts-backup
cp .env /srv/mak4i-backups/mak4i-compose.env && chmod 600 /srv/mak4i-backups/mak4i-compose.env
```

Keep backups **outside the repository**. A backup is verified when
`dump-ok` was printed and the artifacts copy contains one `org_…`
directory per organization.

Restore into a running stack (after `up`, before creating anything):

```bash
docker compose stop mak4i
docker compose cp /srv/mak4i-backups/mak4i-control-plane.dump postgres:/tmp/mak4i-control-plane.dump
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner /tmp/mak4i-control-plane.dump && rm /tmp/mak4i-control-plane.dump'
docker compose run --rm --no-deps --entrypoint find mak4i /data/artifacts -mindepth 1 -delete
docker compose cp /srv/mak4i-backups/mak4i-artifacts-backup/. mak4i:/data/artifacts/
docker compose run --rm --no-deps -u root --cap-add CHOWN --cap-add DAC_READ_SEARCH --entrypoint chown mak4i -R mak4i:mak4i /data/artifacts
docker compose --profile tls up -d --wait
```

The `chown` step is required: copied files keep the backup's owner and
mode, and MAK4I runs unprivileged with all capabilities dropped.
(`DAC_READ_SEARCH` lets that one-off `chown` descend into backup
directories you've made private.)

### 11.5 Upgrade

Back up first, then:

```bash
git fetch --depth 1 origin tag v2.0.0-rc.1
git checkout v2.0.0-rc.1
docker compose --profile tls up -d --build --wait
```

### 11.6 Remove

Keep the data (copy `.env` somewhere safe first; you need its password to
open the database again):

```bash
docker compose --profile tls down
```

**Destroy all data (irreversible; back up first):** `down -v` deletes the
volumes listed in [Remove everything](#102-remove-everything).

```bash
docker compose --profile tls down -v
rm .env
```

Avoid `docker system prune` or `docker volume prune`: they affect every
project on the host, not only MAK4I.

## 12. Configuration reference

The one table of Enterprise Self-Hosted settings. The bootstrap writes
`deploy/compose/.env`; `compose.yaml` fixes the rest.

| Variable | Where | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | `.env` (required) | Database password. Generated by the bootstrap. Letters and digits only. |
| `POSTGRES_USER`, `POSTGRES_DB` | `.env` | Database user and name (default `mak4i`). |
| `MAK4I_PUBLIC_ENDPOINT` | `.env` | The client-facing MCP URL, including `/mcp`. Used by `credential issue` to print connect commands, and shown by `mak4i_whoami`. |
| `MAK4I_DOMAIN` | `.env` (tls) | The DNS name Caddy gets a certificate for. |
| `MAK4I_TLS_ISSUER` | `.env` (tls) | `acme` (default; Let's Encrypt) or `internal` (Caddy's own CA, for private networks; clients must trust `/data/caddy/pki/authorities/local/root.crt` from the `caddy` container). |
| `MAK4I_HTTP_BIND` | `.env` | Host address MAK4I's HTTP port is published on. `127.0.0.1` with TLS; a private address for private-http. **Never** a public address or `0.0.0.0` without a firewall in front. |
| `MAK4I_HTTP_PORT` | `.env` | Host port for MAK4I's HTTP listener (default `8080`). |
| `MAK4I_INSTANCE_NAME`, `MAK4I_ENVIRONMENT` | `.env` | How this installation names itself to AI clients (`mak4i_whoami`, project listings, denials). Default `MAK4I Enterprise` / `enterprise`. |
| `MAK4I_OAUTH_ENABLED` | `.env` | `1` enables OAuth sign-in ([7.1](#71-oauth-sign-in)); default `0`. Requires an https `MAK4I_PUBLIC_ENDPOINT`. |
| `MAK4I_OAUTH_ISSUER` | `.env` | OAuth issuer. Default: `MAK4I_PUBLIC_ENDPOINT` without its trailing `/mcp`. Same origin as the endpoint. |
| `MAK4I_OAUTH_CIMD`, `MAK4I_OAUTH_DCR` | `.env` | Client registration modes: client ID metadata documents (default `1`) and dynamic registration (default `0`). |
| `MAK4I_OAUTH_CIMD_ALLOWED_HOSTS` | `.env` | Optional comma-separated allowlist of hosts whose client metadata documents are accepted. |
| `MAK4I_OAUTH_ACCESS_TOKEN_TTL`, `MAK4I_OAUTH_REFRESH_IDLE_TTL`, `MAK4I_OAUTH_REFRESH_ABSOLUTE_TTL`, `MAK4I_OAUTH_CODE_TTL`, `MAK4I_OAUTH_SIGN_IN_CODE_TTL` | `.env` | Token policy in seconds; defaults 3600, 604800 (7 days), 2592000 (30 days), 60, 600. After the idle or absolute lifetime a person must sign in again. |
| `MAK4I_OAUTH_RATE_LIMIT_PER_MINUTE` | `.env` | Sign-in / token / revocation / registration attempts per client address per minute (default 20). |
| `MAK4I_TRUSTED_PROXIES` | `.env` | Reverse proxies whose `X-Forwarded-For` is trusted (default none). With the `tls` profile, set it to the Compose network (find it with `docker network inspect mak4i_default`) so rate limits see real client addresses. |
| `MAK4I_LOG_LEVEL` | `.env` | Log level (default `INFO`). |

Check the configuration the way the server will (no secrets printed):
`docker compose exec mak4i mak4i config check`.
| `MAK4I_BOOTSTRAP_PROFILE`, `MAK4I_INSTALL_STATE`, `MAK4I_INSTALLED_RELEASE` | `.env` | Bootstrap bookkeeping. Don't edit. |
| `MAK4I_TRANSPORT=http`, `MAK4I_HOST=0.0.0.0`, `MAK4I_PORT=8080`, `MAK4I_STORE=local`, `MAK4I_LOCAL_STORE_DIR=/data/artifacts`, `MAK4I_CONTROL_PLANE_DB` | `compose.yaml` (fixed) | The container runtime settings. `MAK4I_HOST=0.0.0.0` applies **inside** the container only; host exposure is `MAK4I_HTTP_BIND`. |

The full runtime contract for running the container on another platform is
in [`DEPLOYMENT.md` → Runtime contract](DEPLOYMENT.md#runtime-contract).

> **Secrets.** A mode-600 `.env` on a single VM is acceptable for this
> Developer Preview. Production deployments keep the database URL in a
> secret manager ([`DEPLOYMENT.md` → Security](DEPLOYMENT.md#security)).

## 13. Troubleshooting

### 13.1 DNS, firewall and certificates

| Symptom | Cause and fix |
|---|---|
| Caddy log: `NXDOMAIN` | The `A` record doesn't exist or hasn't propagated. Check `dig +short <domain>` (and `dig +short <domain> @1.1.1.1`). Caddy retries on its own once it resolves. |
| Caddy log: `timeout` / `connection refused` during the challenge; external `curl` times out while Caddy is listening | Inbound TCP 80/443 is blocked. Allow them in the cloud firewall (on GCP: allow HTTP and HTTPS traffic to the VM, or a VPC rule for `tcp:80,443`; AWS: the security group; Azure: the network security group) and any host firewall. |
| Public DNS resolves, but the VM (and preflight) still can't | The VM's resolver cached the earlier NXDOMAIN answer. It expires on its own. Meanwhile, test directly: `curl --resolve <domain>:443:<vm-ip> https://<domain>/ready`. |
| Certificate issued for the wrong IP / a CDN certificate appears | A CDN proxy is in front of the name. Set the record to DNS only until the first certificate is issued. |
| `install` exited with code 5 after "no valid certificate" | MAK4I is running; HTTPS isn't ready. Fix the cause above, then `status`. |
| `caddy` restarting | `docker compose --profile tls logs caddy`. Check `MAK4I_DOMAIN` is set. |

### 13.2 Stack

| Symptom | Cause and fix |
|---|---|
| Preflight: `this user can't use Docker` | `sudo usermod -aG docker "$USER"`, then log out and back in. |
| `required variable POSTGRES_PASSWORD is missing a value` | `.env` missing or the password empty. Use the bootstrap, or see [Configure](#111-configure). |
| `migrate` exits non-zero | `docker compose logs migrate`. Often a password with URL-unsafe characters. Changing the password after the first start doesn't change it inside the existing database. |
| `/ready` returns `503` | The database is unreachable: is `postgres` healthy (`status`)? |
| `install` refuses: data volumes exist without `.env` | Restore the saved `.env` (a bootstrap backup has it as `env`), or delete the old data with `uninstall --destroy-data`. |
| `install` refuses: manual installation | Adopt it with `upgrade` (section 9). |
| Permission errors writing artifacts after a manual restore | Run the `chown` step in [Backup and restore](#114-backup-and-restore). |

### 13.3 Clients

| Symptom | Cause and fix |
|---|---|
| `401` | Missing, wrong, revoked or expired token. The server deliberately doesn't say which. With OAuth enabled the response's `WWW-Authenticate` header tells OAuth clients where to sign in. |
| `403 insufficient_scope` | An OAuth token without the scope a tool needs (for example a read-only connection calling a write tool). Reconnect and approve the wider scope; scopes never exceed the principal's grants anyway. |
| The OAuth sign-in page says the code is invalid | Codes work once and expire after 10 minutes; issue a new one with `mak4i oauth sign-in-code`. |
| `404` | The URL doesn't end in `/mcp`. |
| `421 Misdirected Request` | Your own proxy reaches MAK4I on a loopback bind. Keep `MAK4I_HOST=0.0.0.0` inside the container (as `compose.yaml` sets); control exposure with `MAK4I_HTTP_BIND`. |
| Sessions drop or responses stall behind your own proxy | It buffers or times out streamed responses; see [`DEPLOYMENT.md` → Reverse proxy requirements](DEPLOYMENT.md#reverse-proxy-requirements). |
| `access denied … on MAK4I connection '…'` | The principal has no grant (or only `read`) on that project on this installation. It's final for this connection; grant access here rather than writing elsewhere. |

## 14. Bootstrap reference

```
./deploy/bootstrap/mak4i-enterprise <command> [options]      # --help for all options
```

| Command | Purpose | Changes state |
|---|---|---|
| `preflight` | Check host, Docker, network, configuration | No (`--check-inbound` briefly runs a probe container) |
| `install` | Configure, start, verify | Yes (`--dry-run` shows the plan) |
| `status` | Containers, version, health, readiness, schema, data, HTTPS | No |
| `admin-info` | Non-secret inventory: organizations, owners, projects, principals (incl. `agent_id`), grants, credential metadata. Use it to recover ids you didn't keep; tokens can't be recovered, only reissued | No |
| `restart` | Restart and verify health and data | Yes |
| `upgrade` | Back up, then `v0.1.0-rc.5` → `v2.0.0-rc.1` | Yes |
| `backup` | Timestamped, verified backup | Writes the backup only |
| `restore` | Replace all data with a backup | Yes (confirmation required) |
| `uninstall` | Remove containers; `--destroy-data` removes data too | Yes (destroy needs confirmation) |

Options that never take secrets; `--non-interactive` never prompts (a
command that needs confirmation then fails unless its confirmation flag is
given); `--dry-run` shows what a state-changing command would do.

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | A step failed (see the message and the log) |
| 2 | Invalid command line |
| 3 | Preflight found a FAIL |
| 4 | Refused: conflicting, orphaned or manual existing installation |
| 5 | Not healthy, not ready, or the certificate wasn't issued in time |
| 6 | Confirmation not given |
| 7 | Unsupported upgrade path or environment |
| 8 | Backup or restore validation failed |

## 15. Beyond this release

- **Not provided yet:** Terraform (or other infrastructure-as-code)
  modules for provisioning the VM, IP, DNS and firewall, and Kubernetes or
  Helm packaging. Both are candidates for future releases; this release
  supports the single-VM Compose stack and its bootstrap only.
- **Windows:** the bootstrap is Linux-only; Enterprise Self-Hosted isn't
  supported on Windows hosts in this release.
- **Production:** managed PostgreSQL, object storage for artifacts, a
  secret manager and TLS at your load balancer: see
  [`DEPLOYMENT.md` → Production options](DEPLOYMENT.md#production-options).
