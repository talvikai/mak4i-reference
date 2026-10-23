# MAK4I for your team (Enterprise Self-Hosted)

> **Developer Preview.** This guide describes the release candidate
> `v2.1.0-rc.1`, for evaluation and feedback. It isn't a
> production-supported release.

## What this guide covers

This guide shows you how to install the free MAK4I on a Linux server for
your team, connect your team's AI apps to it, check that it works, and
keep it running and up to date.

- **What you get:** one shared MAK4I on one Linux server, reachable over
  HTTPS at an address such as `https://mak4i.example.com/mcp`, with its
  own sign-in. Several people, and several AI apps per person, use the same
  projects.
- **Your AI apps:** claude.ai, ChatGPT, Claude Code and Grok connect over
  the internet ([how each app connects](#how-each-ai-app-connects)).
- **Your data:** MAK4I stores everything on your server (a PostgreSQL
  database and a data folder, both inside Docker). When an AI app reads
  from MAK4I, what it reads is sent to that app, and to its provider if
  the app runs in the cloud.
- **Cost:** MAK4I itself is free. You pay your cloud provider for the
  server, and your domain registrar for the domain name, if you don't
  already have them.

**Just for yourself?** You don't need this guide. The
[Local Setup guide](LOCAL_SETUP.md) installs MAK4I on your own computer,
and it also covers claude.ai and ChatGPT through a secure tunnel that you
set up. Use this guide when a **team** shares one MAK4I, or when you want
MAK4I on a server rather than on your computer. Don't use `mak4i init` or
`mak4i serve` (from the Local guide) on an Enterprise server.

## Who does what

Two kinds of people use this guide. Each follows only their own part.

| | **Administrator** (one person, or a small IT team) | **AI users** (everyone on the team) |
|---|---|---|
| Does | creates the server, installs MAK4I, turns on sign-in, creates the organization, people and projects, registers AI apps, issues sign-in codes, backs up and upgrades | adds MAK4I to their AI app and signs in |
| Needs | a cloud account, a domain name, and a terminal (command line) on the server | the MAK4I address, a client ID (for claude.ai and ChatGPT), and a sign-in code from the administrator |
| Follows | [Part 1](#part-1-set-up-and-run-mak4i-administrator) | [Part 2](#part-2-connect-your-ai-app-ai-users) |

The administrator is usually also an AI user, and follows Part 2 too.

## How each AI app connects

Every app connects to the same address (your domain, ending in `/mcp`),
and each person signs in once per app with a **sign-in code** that the
administrator gives them. What the administrator prepares differs by app:

| AI app | What the administrator does once | Status for this release |
|---|---|---|
| **claude.ai** | registers it and gives users its client ID ([5.1](#51-register-claudeai-and-chatgpt)) | tested: connected, saved and read |
| **ChatGPT** | registers it and gives users its client ID ([5.1](#51-register-claudeai-and-chatgpt)) | tested: connected, saved and read |
| **Claude Code** | nothing: it registers itself ([5.2](#52-apps-that-register-themselves-claude-code-and-grok)) | tested |
| **Grok** | nothing: it registers itself ([5.2](#52-apps-that-register-themselves-claude-code-and-grok)) | connected and signed in; using MAK4I from Grok isn't yet tested |
| **Another app** | depends on the app ([5.3](#53-other-apps)) | untested |

All of them need sign-in turned on ([section 3](#3-turn-on-sign-in)) and
a server reachable from the internet over HTTPS. Apps that can send a
fixed access key instead of signing in are covered in
[Part 3](#18-access-keys-instead-of-sign-in).

## Installation at a glance

The administrator does steps 1 to 6, once. Each takes minutes, except
waiting for a new domain name to start working, which can take longer.

1. [Before you start](#1-before-you-start): create a Linux server, give it
   a fixed address and a domain name, open ports 80 and 443, and install
   Docker and Git.
2. [Install MAK4I](#2-install-mak4i): download it, check the server, and
   install with one command (about 2 minutes).
3. [Turn on sign-in](#3-turn-on-sign-in).
4. [Create your organization, people and projects](#4-create-your-organization-people-and-projects).
5. [Register AI apps and issue sign-in codes](#5-register-ai-apps-and-issue-sign-in-codes).
6. [Check it works](#6-check-it-works): save something from one AI app
   and read it back.

Then each AI user follows [Part 2](#part-2-connect-your-ai-app-ai-users).

You type the administrator's commands into a terminal connected to the
server. Every step shows exactly what to copy and what you should see
afterwards.

## Find your section

| I want to… | Go to |
|---|---|
| Set up MAK4I for my team for the first time | [1. Before you start](#1-before-you-start), then follow Part 1 in order |
| Connect my AI app to my team's MAK4I | [Part 2: Connect your AI app](#part-2-connect-your-ai-app-ai-users) |
| Add a person or a project | [4. Create your organization, people and projects](#4-create-your-organization-people-and-projects) |
| Give someone a sign-in code | [5.4 Issue a sign-in code](#54-issue-a-sign-in-code) |
| Check that MAK4I works | [6. Check it works](#6-check-it-works) |
| Stop, start or restart MAK4I | [7. Start, stop, restart and reboot](#7-start-stop-restart-and-reboot) |
| Find which release I run, and upgrade | [8. Find your release and upgrade](#8-find-your-release-and-upgrade) |
| Back up, restore, or undo an upgrade | [9. Back up, restore and roll back](#9-back-up-restore-and-roll-back) |
| Fix a problem | [10. Fix common problems](#10-fix-common-problems) |
| Remove MAK4I | [11. Uninstall](#11-uninstall) |
| A private network or my own load balancer, access keys, per-app access, manual setup, settings | [Part 3: Advanced](#part-3-advanced) |

**Follow one section from start to finish.** You don't need to read the
other sections first.

### Contents

**[Part 1: Set up and run MAK4I](#part-1-set-up-and-run-mak4i-administrator)** (administrator)

1. [Before you start](#1-before-you-start)
2. [Install MAK4I](#2-install-mak4i)
3. [Turn on sign-in](#3-turn-on-sign-in)
4. [Create your organization, people and projects](#4-create-your-organization-people-and-projects)
5. [Register AI apps and issue sign-in codes](#5-register-ai-apps-and-issue-sign-in-codes)
6. [Check it works](#6-check-it-works)
7. [Start, stop, restart and reboot](#7-start-stop-restart-and-reboot)
8. [Find your release and upgrade](#8-find-your-release-and-upgrade)
9. [Back up, restore and roll back](#9-back-up-restore-and-roll-back)
10. [Fix common problems](#10-fix-common-problems)
11. [Uninstall](#11-uninstall)

**[Part 2: Connect your AI app](#part-2-connect-your-ai-app-ai-users)** (AI users)

12. [What to ask your administrator for](#12-what-to-ask-your-administrator-for)
13. [Connect your app](#13-connect-your-app)
14. [Sign in, and when you'll sign in again](#14-sign-in-and-when-youll-sign-in-again)
15. [Keep your access safe](#15-keep-your-access-safe)

**[Part 3: Advanced](#part-3-advanced)** (for platform engineers)

16. [How the installation is built](#16-how-the-installation-is-built)
17. [A private network or your own load balancer](#17-a-private-network-or-your-own-load-balancer)
18. [Access keys instead of sign-in](#18-access-keys-instead-of-sign-in)
19. [Different access per AI app](#19-different-access-per-ai-app)
20. [Change notifications](#20-change-notifications)
21. [Manual Compose procedure](#21-manual-compose-procedure)
22. [Configuration reference](#22-configuration-reference)
23. [Bootstrap reference](#23-bootstrap-reference)
24. [What has been tested](#24-what-has-been-tested)
25. [Beyond this release](#25-beyond-this-release)

---

# Part 1: Set up and run MAK4I (administrator)

**Replace the examples with your own values.** This guide uses
`mak4i.example.com` for your MAK4I domain, `203.0.113.10` for your
server's address, and `Acme Corp` for your organization. Replace them in
every command.

**Copy one box at a time,** exactly as shown, into the terminal on your
server, unless a step says to run it somewhere else.

## 1. Before you start

MAK4I's installer never creates or changes servers, addresses, domain
names (DNS), firewalls or cloud permissions. You set these up in your
cloud provider's and domain provider's websites, with Steps 1 to 4. The
installer then checks them and tells you what's missing.

| You provide | The installer (bootstrap) provides |
|---|---|
| A Linux server (VM) | Checks of the server, network and configuration |
| A fixed public address (static IP) | A secure configuration file (`.env`, private, with a random database password) |
| A domain name pointing at that address (DNS `A` record) | The MAK4I services in Docker, with pinned versions |
| Inbound ports 80 and 443 open (firewall) | The HTTPS certificate, obtained and renewed automatically |
| Docker and Git on the server | Health checks, and commands to check, restart, back up, restore, upgrade and uninstall |

[Example: Google Cloud](#example-google-cloud) shows Steps 1 to 4 with
exact commands.

### Step 1: Create a Linux server

Create a virtual machine (VM) with your cloud provider:

| | Choose |
|---|---|
| Operating system | **Debian 12** (recommended; used for this guide's walkthrough) or Debian 13, Ubuntu 22.04 or 24.04. 64-bit Intel/AMD (x86-64). Windows and macOS servers aren't supported. |
| Size | **2 virtual CPUs and 4 GB of memory** (for example Google Cloud `e2-medium`). 2 GB works for a small trial; the installer warns. |
| Disk | **30 GB**. (A 20 GB disk leaves only about 15 GB free, and the installer warns.) |
| User | your normal (non-root) login. You'll use `sudo` for a few setup commands. |

### Step 2: Give it a fixed public address

In your cloud provider, reserve a **static** external IP address and
attach it to the server. Write it down. Your domain name will point at
this address, so it must not change when the server restarts.

### Step 3: Point your domain name at it

At the company that hosts your domain's DNS (for example Cloudflare, or
your domain registrar), add a record:

| Type | Name | Value |
|---|---|---|
| `A` | `mak4i` (for `mak4i.example.com`) | your server's address from Step 2 |

**Using Cloudflare?** Set the record to **DNS only** (grey cloud), not
proxied (orange cloud), at least until MAK4I has its first certificate.

A new record can take from a minute to an hour to work everywhere. Check
it from your own computer:

```bash
nslookup mak4i.example.com
```

**You should see** your server's address in the answer. If you see
`NXDOMAIN` or `can't find`, wait a few minutes and try again.

### Step 4: Open ports 80 and 443

In your cloud provider's firewall, allow **inbound TCP 80 and 443** from
anywhere to this server. MAK4I uses 80 only to obtain its certificate,
and 443 for HTTPS. Open nothing else: the database and MAK4I's internal
port 8080 must never be reachable from outside.

- **Google Cloud:** tick **Allow HTTP traffic** and **Allow HTTPS
  traffic** on the VM, or add a firewall rule for `tcp:80,443`. Google
  Cloud blocks both by default.
- **AWS:** a security group allowing inbound TCP 80 and 443.
- **Azure:** a network security group allowing inbound TCP 80 and 443.

### Step 5: Connect to the server and install Docker and Git

Open a terminal on the server: with your cloud provider's **SSH** button,
or `ssh` from your computer. Every command from now on runs there.

The Docker that comes with Debian is too old for MAK4I (it has Compose
v1). Install Docker from Docker's own packages. Copy this whole box (on
Debian and Ubuntu):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
. /etc/os-release
sudo curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

**Then log out and connect again** (type `exit`, then reconnect), so you
may use Docker without `sudo`. Check:

```bash
docker compose version
git --version
```

**You should see** `Docker Compose version v2…` (or later) and
`git version …`. Other Linux systems: install Docker Engine with the
Compose plugin from <https://docs.docker.com/engine/install/>.

| If you see | Do this |
|---|---|
| `permission denied while trying to connect to the Docker daemon` | You didn't log out and back in after `usermod`. Do that. |
| `docker: 'compose' is not a docker command` | Docker came from your system's own packages. Use the box above. |

### Example: Google Cloud

This example creates everything in Steps 1, 2 and 4 with the `gcloud`
command-line tool, on your own computer or in Cloud Shell. Replace
`my-project` and the region and zone. (In the Google Cloud console, the
same choices are under **Compute Engine → VM instances → Create
instance**, **VPC network → IP addresses** and **Firewall**.)

```bash
gcloud compute addresses create mak4i-ip --region us-central1 --project my-project
gcloud compute addresses describe mak4i-ip --region us-central1 --project my-project --format='value(address)'
```

**You should see** the new address, for example `203.0.113.10`. Use it
for your DNS record (Step 3), and here:

```bash
gcloud compute instances create mak4i --project my-project --zone us-central1-f \
  --machine-type e2-medium --image-family debian-12 --image-project debian-cloud \
  --boot-disk-size 30GB --address 203.0.113.10 --tags http-server,https-server
gcloud compute firewall-rules create mak4i-web --project my-project --network default \
  --direction INGRESS --allow tcp:80,tcp:443 --source-ranges 0.0.0.0/0 --target-tags http-server,https-server
```

(If your project already has the `default-allow-http` and
`default-allow-https` rules, the second command isn't needed.) Connect
with:

```bash
gcloud compute ssh mak4i --zone us-central1-f --project my-project
```

What we saw when we set this up: the first certificate request failed
while the DNS record was new (`NXDOMAIN`) and while ports 80/443 were
still blocked (timeouts). Both fixed themselves once DNS and the firewall
were right: MAK4I keeps trying.

**AWS and Azure** work the same way (an Elastic IP or a static public IP,
and a security group or network security group for TCP 80 and 443), but
haven't been tested with this release.

## 2. Install MAK4I

### Step 1: Download MAK4I

```bash
cd ~
git clone --branch v2.1.0-rc.1 --depth 1 https://github.com/talvikai/mak4i-reference.git
cd mak4i-reference
```

**You should see** `Cloning into 'mak4i-reference'...`. A note about
"detached HEAD" is normal.

**Always start in this folder.** Every administrator command in this
guide runs from `~/mak4i-reference` unless it says otherwise. In a new
terminal, go there first: `cd ~/mak4i-reference`.

### Step 2: Check the server

This only reads; it changes nothing. Replace the domain with yours:

```bash
./deploy/bootstrap/mak4i-enterprise preflight --profile tls --domain mak4i.example.com
```

**You should see** one line per check, then a summary:

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

Fix every `[FAIL]` before you continue; [10.1](#101-installing-dns-firewall-and-certificates)
explains the common ones. `[WARN]` lines are advice (for example less
memory or disk than recommended) and don't stop the installation.

### Step 3: Install

Choose a name for this installation, which AI apps show to your users
(for example `Acme MAK4I`). Replace the domain and the name:

```bash
./deploy/bootstrap/mak4i-enterprise install --profile tls --domain mak4i.example.com --instance-name "Acme MAK4I" --environment production
```

This takes about 2 minutes. It checks the server again, writes the
private configuration file `deploy/compose/.env` (with a random database
password that is never shown), starts the MAK4I services, and waits for
the HTTPS certificate (up to 5 minutes).

**You should see** at the end:

```
MAK4I Enterprise v2.1.0-rc.1 is installed and healthy.
  Profile:   tls
  Endpoint:  https://mak4i.example.com/mcp
  ...
```

The `Next:` lines that follow point to creating your organization: do
[section 3](#3-turn-on-sign-in) first, or afterwards; the order doesn't
matter.

| If you see | Do this |
|---|---|
| it stops with exit code 5 after "no valid certificate" | MAK4I is running, but HTTPS isn't ready yet. Usually the DNS record is new or ports 80/443 are blocked: see [10.1](#101-installing-dns-firewall-and-certificates). MAK4I keeps trying by itself; check with `./deploy/bootstrap/mak4i-enterprise status`. |
| it stops with another error | It says what failed and keeps what it has done. Fix the cause, then run the **same install command again**: it continues where it stopped. |
| `refusing` an existing installation | See [10.2](#102-running-mak4i). |

Running the install command again on a working installation changes
nothing. It never replaces the database password, your organizations or
anyone's access.

### Step 4: Check the installation

```bash
./deploy/bootstrap/mak4i-enterprise status
```

**You should see** each service `running` (and `healthy`),
`/health: ok`, `/ready: ready`, `Database schema: at the latest
migration`, `HTTPS: https://mak4i.example.com/ready -> ready (certificate
verified)`, and finally `Healthy.`

From your own computer, open this address in a web browser:

```
https://mak4i.example.com/ready
```

**You should see** the word `ready`, with a padlock (a valid
certificate) in the address bar.

## 3. Turn on sign-in

claude.ai, ChatGPT, Claude Code and Grok connect by signing in. Sign-in is
off after installation. To turn it on, add one line to the configuration
file and restart MAK4I with it.

```bash
cd ~/mak4i-reference/deploy/compose
grep MAK4I_PUBLIC_ENDPOINT .env
echo "MAK4I_OAUTH_ENABLED=1" >> .env
docker compose up -d --wait mak4i
```

**You should see** first `MAK4I_PUBLIC_ENDPOINT=https://mak4i.example.com/mcp`
(the installer wrote it), and at the end `Container mak4i-mak4i-1
Healthy`.

(`up -d --wait mak4i` recreates MAK4I with the new setting.
`./deploy/bootstrap/mak4i-enterprise restart` is **not** enough after a
change to `.env`: it keeps the old settings.)

Check from your own computer, in a web browser:

```
https://mak4i.example.com/.well-known/oauth-protected-resource/mcp
```

**You should see** text starting with
`{"resource":"https://mak4i.example.com/mcp"`. If you see `Not Found`,
sign-in isn't on: check that `.env` contains `MAK4I_OAUTH_ENABLED=1`
exactly once, and run the `docker compose up` line again.

## 4. Create your organization, people and projects

MAK4I's administration commands run **inside** the MAK4I service, from the
`deploy/compose` folder. You'll create:

- an **organization**, with you as its **owner**: the owner manages
  people, projects and access, and issues sign-in codes;
- a **project**: a shared space of knowledge, for example one per product
  or team;
- **access** (a grant) for each person on each project: `read` (see),
  `write` (save and change), and `resolve` (settle conflicts, when two AI
  apps saved different answers to the same question).

Each command prints an ID (`org_…`, `prn_…` for a person, `prj_…`). The
boxes below save each ID in the file `~/mak4i-ids.env`, so you don't have
to copy them by hand.

Each command also prints one or two lines starting with `{"event": …`
before its result. That's MAK4I's audit log, recording what you did;
ignore it.

### Step 1: Go to the Compose folder

**In every new terminal** for sections 4, 5 and 6, first run both of
these lines; the second loads the IDs you've saved so far:

```bash
cd ~/mak4i-reference/deploy/compose
. ~/mak4i-ids.env
```

(The very first time, the second line says `No such file or directory`.
That's fine: Step 2 creates the file.)

### Step 2: Create your organization

Replace the organization name and your name:

```bash
docker compose exec -T mak4i mak4i org create --name "Acme Corp" --owner-display-name "Your Name" | tee ~/mak4i-org.json
echo "ORG=$(grep -m1 -o 'org_[0-9a-f-]*' ~/mak4i-org.json)" >> ~/mak4i-ids.env
echo "OWNER=$(grep -m1 -o 'prn_[0-9a-f-]*' ~/mak4i-org.json)" >> ~/mak4i-ids.env
. ~/mak4i-ids.env
cat ~/mak4i-ids.env
```

**You should see** the new organization, with you as `"role": "owner"`,
and then two lines: `ORG=org_…` and `OWNER=prn_…`. `OWNER` is **your**
ID as the owner.

Run this only once. A second run creates a second organization.

### Step 3: Create a project

```bash
docker compose exec -T mak4i mak4i project create --actor "$OWNER" --organization-id "$ORG" --name "Platform" | tee ~/mak4i-project.json
echo "PROJECT=$(grep -m1 -o 'prj_[0-9a-f-]*' ~/mak4i-project.json)" >> ~/mak4i-ids.env
. ~/mak4i-ids.env
```

**You should see** the project, with `"project_id": "prj_…"`.

### Step 4: Give yourself access

Creating a project gives **nobody** access to it, not even you as the
owner: being the owner lets you manage the organization, not read or
write its knowledge. Give yourself access:

```bash
docker compose exec -T mak4i mak4i grant create --actor "$OWNER" --principal-id "$OWNER" --project-id "$PROJECT" --permissions read,write,resolve
```

**You should see** `"permissions"` with `"read"`, `"write"` and
`"resolve"`.

**Why `resolve`?** Without it you can read and save, but can't settle a
conflict (two AI apps saved different answers to the same question).
Leave it out (`--permissions read,write`) if someone else should decide
conflicts.

> **`grant create` replaces a person's access to that project.** Running
> it again with `read,write` removes `resolve`. When you change someone's
> access, list everything they should keep. To see a person's current
> access: `docker compose exec -T mak4i mak4i grant list --actor "$OWNER" --principal-id prn_…`.

### Step 5: Add people

For each person on your team: create them, then give them access. Replace
the name, and `ALEX` with a short label of your choice (letters only):

```bash
docker compose exec -T mak4i mak4i principal create --actor "$OWNER" --organization-id "$ORG" --type human --display-name "Alex Rivera" | tee ~/mak4i-person.json
echo "ALEX=$(grep -m1 -o 'prn_[0-9a-f-]*' ~/mak4i-person.json)" >> ~/mak4i-ids.env
. ~/mak4i-ids.env
docker compose exec -T mak4i mak4i grant create --actor "$OWNER" --principal-id "$ALEX" --project-id "$PROJECT" --permissions read,write
```

**You should see** the new person (`"role": "member"`), then their grant.
Use `--permissions read` for someone who may only look.

| Their access | What they can do from an AI app |
|---|---|
| `read` | list projects, read current knowledge, search, see history and conflicts |
| `read,write` | also save new knowledge and replace it |
| `read,write,resolve` | also settle conflicts |

### Everyday administration

All commands run in `deploy/compose`, after `. ~/mak4i-ids.env`, and
start with `docker compose exec -T mak4i mak4i`:

| Task | Command (after `docker compose exec -T mak4i mak4i`) |
|---|---|
| Add a project | `project create --actor "$OWNER" --organization-id "$ORG" --name "…"` |
| Add a person | `principal create --actor "$OWNER" --organization-id "$ORG" --type human --display-name "…"` |
| Add an automated agent | `principal create --actor "$OWNER" --organization-id "$ORG" --type agent --agent-id release-bot --display-name "…"` (`--agent-id` is permanent and unique in the organization; what the agent saves carries it) |
| Give or change access | `grant create --actor "$OWNER" --principal-id prn_… --project-id prj_… --permissions read,write` (replaces their access to that project) |
| Remove access (immediate) | `grant revoke --actor "$OWNER" --principal-id prn_… --project-id prj_…` |
| Rename a person (history keeps the old name) | `principal rename --actor "$OWNER" --principal-id prn_… --display-name "…"` |
| Remove a person (immediate; history kept) | `principal deactivate --actor "$OWNER" --principal-id prn_…`: all their sign-ins and access keys stop working |
| Archive a project (nothing deleted; access stops) | `project archive --actor "$OWNER" --project-id prj_…` |
| See everything (never shows secrets) | `admin inventory` |
| See who changed what | `audit list --actor "$OWNER"` (every administrative action, including refused attempts) |
| Add another owner | `principal create --actor "$OWNER" --organization-id "$ORG" --type human --role owner --display-name "…"` |
| Suspend an organization, onboard an outside collaborator in one step | see the [administrative CLI reference](ADMIN_CLI.md) |

Every administrative command, who may run it, and what it records and
prints: [ADMIN_CLI.md](ADMIN_CLI.md).

### If you lose an ID

From `~/mak4i-reference`:

```bash
./deploy/bootstrap/mak4i-enterprise admin-info
```

**You should see** your organizations, each with its `owners`,
`projects` (`prj_…`) and `principals` (`prn_…`, with name, role and
access). It never shows sign-in codes, access keys or passwords.

## 5. Register AI apps and issue sign-in codes

### 5.1 Register claude.ai and ChatGPT

claude.ai and ChatGPT must be registered once per installation. Each
registration gives a **client ID** (`mak4c_…`) that every user of that
app enters when they add MAK4I. It isn't a secret. Run the line for each
app your team uses, in `deploy/compose`, after `. ~/mak4i-ids.env`:

```bash
docker compose exec -T mak4i mak4i oauth client register --actor "$OWNER" --name "Claude (claude.ai)" --redirect-uri https://claude.ai/api/mcp/auth_callback
docker compose exec -T mak4i mak4i oauth client register --actor "$OWNER" --name "ChatGPT" --redirect-uri https://chatgpt.com/connector_platform_oauth_redirect
```

**You should see** `OAuth client registered.` and `client_id: mak4c_…`
for each. Note which ID belongs to which app. To see them again later:

```bash
docker compose exec -T mak4i mak4i oauth client list --actor "$OWNER"
```

A new installation (or one whose data you deleted) needs this again.
Restoring a backup keeps registered apps.

### 5.2 Apps that register themselves: Claude Code and Grok

Claude Code and Grok register themselves when a user adds MAK4I: they
publish a description of themselves at a web address (a "client ID
metadata document"), and MAK4I reads it. There's nothing to do for them,
as long as `MAK4I_OAUTH_CIMD` is on (the default).

### 5.3 Other apps

MAK4I accepts an app that either registers itself (as in 5.2) or that you
register (as in 5.1, with that app's redirect address, from its
documentation). MAK4I doesn't let apps register by themselves without
one of these (dynamic client registration is off), so an app that needs
it can't connect. Treat any app not listed in
[How each AI app connects](#how-each-ai-app-connects) as untested.

### 5.4 Issue a sign-in code

Each person signs in once per app with a one-time **sign-in code**. Issue
it **when they're ready**: when their app shows the MAK4I sign-in page.
A code works once, for **10 minutes**.

In `deploy/compose`, after `. ~/mak4i-ids.env`. For yourself:

```bash
docker compose exec -T mak4i mak4i oauth sign-in-code --actor "$OWNER" --principal-id "$OWNER"
```

For someone else (here `$ALEX`, from [4, Step 5](#step-5-add-people);
or their `prn_…` from `admin-info`):

```bash
docker compose exec -T mak4i mak4i oauth sign-in-code --actor "$OWNER" --principal-id "$ALEX"
```

**You should see** `Sign-in code created.` and a line
`sign-in code: QMX9-TKRA-…`. Send the code to that person privately (not
in a shared channel). Only an owner can issue codes for others; anyone
can issue a code for themselves with their own access key
([18](#18-access-keys-instead-of-sign-in)).

### 5.5 What to send each user

Send each person:

1. the MAK4I address, on its own line, exactly:

   ```
   https://mak4i.example.com/mcp
   ```

2. for claude.ai or ChatGPT: that app's client ID from 5.1;
3. a link to [Part 2](#part-2-connect-your-ai-app-ai-users) of this
   guide;
4. when they reach the sign-in page: a sign-in code (5.4).

Put the address on a line by itself. A full stop or other character
copied after `/mcp` makes the sign-in fail.

To give an app less access than the person has (for example, claude.ai
read-only for someone who may write from ChatGPT), see
[19. Different access per AI app](#19-different-access-per-ai-app).

## 6. Check it works

Connect one AI app yourself first ([Part 2](#part-2-connect-your-ai-app-ai-users)),
then:

1. Ask: **"Use MAK4I to show who I am and list my projects."**

   **You should see** your installation's name (for example
   `Acme MAK4I`), your organization, and your project with your
   permissions.
2. Ask: **"Save this in my MAK4I project: our app uses PostgreSQL as its
   database."** The app may ask before it saves; allow it.
3. Start a **new** conversation (or another AI app) and ask: **"According
   to MAK4I, which database does my project use?"**

   **You should see** PostgreSQL: MAK4I remembered it across
   conversations and apps.

**Checking without an AI app.** You can save and read directly on the
server (in `deploy/compose`, after `. ~/mak4i-ids.env`):

```bash
docker compose exec -T mak4i mak4i create --principal "$OWNER" --project "$PROJECT" --artifact-id first-check --artifact-type note --title "First check" --content "MAK4I works."
docker compose exec -T mak4i mak4i get-current --principal "$OWNER" --project "$PROJECT"
```

**You should see** the saved record, then the same record with
`"content": "MAK4I works."` in the current knowledge.

The tools AI apps use are listed in
[LOCAL_SETUP.md → Check it works](LOCAL_SETUP.md#5-check-it-works); they're
the same here.

## 7. Start, stop, restart and reboot

From `~/mak4i-reference`:

| To | Run |
|---|---|
| see whether MAK4I is healthy | `./deploy/bootstrap/mak4i-enterprise status` |
| restart, or start again after a stop | `./deploy/bootstrap/mak4i-enterprise restart` |
| stop (nothing is deleted) | `cd deploy/compose && docker compose --profile tls stop && cd ../..` |

`restart` checks that your organizations, people, projects, access and
access keys are the same afterwards. **You should see**
`data preserved (organizations principals projects grants credentials: 1 2 1 2 0)`:
how many of each, in that order.

**After a server reboot,** MAK4I starts by itself (as long as Docker
starts at boot, which Docker's packages set up). Check with `status`.
Users don't need to sign in again.

**After you change `.env`,** use `docker compose up -d --wait mak4i` in
`deploy/compose` instead of `restart` ([section 3](#3-turn-on-sign-in)).

**Logs:**

```bash
cd ~/mak4i-reference/deploy/compose
docker compose logs --tail 50 mak4i      # MAK4I: one event per line, never secrets
docker compose logs --tail 50 caddy      # HTTPS and certificates
```

Every installer command is also logged under
`~/.local/state/mak4i-enterprise/logs/`.

## 8. Find your release and upgrade

Upgrading keeps all your data, people, access, app registrations and
sign-ins.

### Step 1: Find your release

```bash
cd ~/mak4i-reference
./deploy/bootstrap/mak4i-enterprise status
git describe --tags --always
```

**You should see** `Configured release: v2.0.0-rc.1` (for example) in
`status`, and the same release from `git describe`. Write it down.

| `git describe` prints | You're on |
|---|---|
| a release, for example `v2.0.0-rc.1` | that release |
| a release with more after it, for example `v2.0.0-rc.1-12-g1a2b3c4` | a development version 12 changes after that release |
| only letters and numbers, for example `1a2b3c4` | a development version |

An installation made by hand (the guides of `v0.1.0-rc.1` to
`v0.1.0-rc.3`, before the installer existed) has no `status`. Use
`git describe --tags --always`, or
`cd deploy/compose && docker compose exec mak4i mak4i --version`
(`mak4i 0.1.0rc3` means `v0.1.0-rc.3`; the first two releases print
`0.1.0`).

### Step 2: Find your path

| Your release | Path to `v2.1.0-rc.1` | Read first |
|---|---|---|
| `v2.0.0-rc.1` | **directly**, with Steps 3 to 5 | [What changes from v2.0.0-rc.1](#what-changes-from-v200-rc1) |
| `v0.1.0-rc.5` or `v0.1.0-rc.6` | **directly**, with Steps 3 to 5 | [What changes from v0.1.0-rc.5 or rc.6](#what-changes-from-v010-rc5-or-rc6) |
| `v0.1.0-rc.4` | 1. to `v0.1.0-rc.5` with [that release's guide](https://github.com/talvikai/mak4i-reference/blob/v0.1.0-rc.5/docs/ENTERPRISE_SELF_HOSTED.md#9-upgrade-from-v010-rc4); 2. then directly, as for `v0.1.0-rc.5` | [What changes from v0.1.0-rc.5 or rc.6](#what-changes-from-v010-rc5-or-rc6) |
| `v0.1.0-rc.3` (made by hand) | 1. to `v0.1.0-rc.4` with [its guide](https://github.com/talvikai/mak4i-reference/blob/v0.1.0-rc.4/docs/ENTERPRISE_SELF_HOSTED.md#9-upgrade-from-v010-rc3), whose installer takes over a hand-made installation; 2. to `v0.1.0-rc.5` ([guide](https://github.com/talvikai/mak4i-reference/blob/v0.1.0-rc.5/docs/ENTERPRISE_SELF_HOSTED.md#9-upgrade-from-v010-rc4)); 3. then directly | the same |
| `v0.1.0-rc.1` or `v0.1.0-rc.2` (made by hand) | 1. to `v0.1.0-rc.3` with [its guide](https://github.com/talvikai/mak4i-reference/blob/v0.1.0-rc.3/docs/ENTERPRISE_SELF_HOSTED.md#upgrades) (switch the checkout and rebuild); 2. then as for `v0.1.0-rc.3` | the same |

The in-between steps use those releases' own guides and installers, not
this one; back up before each. Each installer accepts only the releases
named in its own guide, and refuses any other path with
`upgrading from … isn't supported` before it changes anything. The direct
paths were tested for this release; the in-between steps for releases
before `v0.1.0-rc.5` come from those releases' guides and weren't
repeated for this one ([24](#24-what-has-been-tested)).

### Step 3: Tell your users

The upgrade stops MAK4I for a few minutes. Anything an AI app tries to
save in that time fails (and can be repeated afterwards).

### Step 4: Download the new release and upgrade

```bash
cd ~/mak4i-reference
git fetch --depth 1 origin tag v2.1.0-rc.1
git checkout v2.1.0-rc.1
./deploy/bootstrap/mak4i-enterprise upgrade
```

`upgrade` first takes a **verified backup** in `~/mak4i-backups`, then
builds and starts the new release, and checks that every count of your
data is unchanged.

**You should see** at the end the new release, `data preserved`, and the
commands to undo the upgrade, with the backup's folder name
(`~/mak4i-backups/mak4i-backup-<date and time>`). **Keep that folder
name.**

| If you see | Do this |
|---|---|
| `upgrading from … isn't supported` | Your release needs in-between steps: Step 2. Nothing was changed. |
| a warning about a PostgreSQL **collation version** | Run the command it prints, once. (An older installation's database image was built on an older Debian.) |
| `cannot create /srv/mak4i-backups/…` | You passed `--backup-dir` with a folder you can't write to. Leave `--backup-dir` out (the default is `~/mak4i-backups`), or create the folder first: `sudo install -d -o "$USER" -m 700 /srv/mak4i-backups`. |

### Step 5: Check

```bash
./deploy/bootstrap/mak4i-enterprise status
```

**You should see** `Running version: v2.1.0-rc.1` and `Healthy.` Then, in
an AI app that was connected before: **"Use MAK4I to show who I am and
list my projects."** It should work without signing in again.

### What changes from v2.0.0-rc.1

v2.1.0-rc.1 adds the change feed and per-app access. Two additive
database migrations; existing data isn't rewritten:

| Migration | Adds | Effect until you act |
|---|---|---|
| `0006_change_feed` | `change_feed_heads`, `change_events` tables | records changes from the upgrade on; no history before it |
| `0007_client_policies` | `client_policies` table, `projects.client_restrictions` (default off) | none: no per-app limits exist and every project is `off`, so access is unchanged |

- **Sign-ins.** Registered apps, sign-ins and tokens are kept, so
  connected apps stay signed in. One behavior changes: a refresh token
  presented by a different app now also revokes that sign-in (MAK-0008
  §6.5).
- **AI apps** see protocol version `0.3` in `mak4i_whoami`, with two more
  profiles, `server-change-feed` and `server-client-policies`, and a
  `client` field.
- **Configuration.** One new optional setting,
  `MAK4I_CHANGE_FEED_RETENTION_DAYS` (default 7).
- **Existing access isn't changed.** The upgrade doesn't add `resolve`
  (or anything else) to anyone's access. To let someone settle conflicts,
  give it explicitly ([4, Step 4](#step-4-give-yourself-access)).

### What changes from v0.1.0-rc.5 or rc.6

v2.0.0-rc.1 and v2.1.0-rc.1 together add database tables and columns
(migrations `0002`–`0007`: sign-in (OAuth), `agent_id`, conflict
resolution records, administrative audit, change feed, per-app access),
and new fields on records written from now on (provenance, resolution
fields). Existing people, access keys, access and records are kept
unchanged; existing agents get an `agent_id` of the form
`agent-<12 hex digits>`. Everything in
[What changes from v2.0.0-rc.1](#what-changes-from-v200-rc1) applies too.

- **Sign-in stays off** until you turn it on ([section 3](#3-turn-on-sign-in)).
- **Breaking changes** for apps and scripts that rely on them: read
  v2.0.0-rc.1 in [`CHANGELOG.md`](../CHANGELOG.md) (conflict object
  shape, filters no longer hide one side of a conflict, releasing a
  subject key during a conflict is refused, tool calls with arguments a
  tool doesn't define are rejected with `validation_error`, a duplicate
  `artifact_id` is `artifact_exists`).
- **No way back without the backup.** v0.1.0-rc.5 can't read data this
  release writes. Records that existed before the upgrade and were never
  changed stay readable, but undoing the upgrade always means restoring
  the backup ([9.3](#93-undo-an-upgrade-roll-back)).

## 9. Back up, restore and roll back

### 9.1 Back up

Back up whenever you like, before every upgrade (the upgrade also does
it), and copy backups off the server on your own schedule.

```bash
cd ~/mak4i-reference
./deploy/bootstrap/mak4i-enterprise backup
```

MAK4I stops for a few seconds so the copy is consistent.

**You should see** the backup folder, for example
`~/mak4i-backups/mak4i-backup-20261001T120000Z`, and
`control plane: 1 2 1 2 0 (organizations principals projects grants credentials)`:
how many of each it saved, in that order.

Each backup folder (private to you) contains:

| File | Contents |
|---|---|
| `control-plane.dump` | the database (organizations, people, projects, access, app registrations, access-key hashes), checked after writing |
| `artifacts/` | everything your AI apps saved |
| `artifacts.sha256` | a checksum per file |
| `env` | a copy of `.env`, **including the database password**. Keep backups private. |
| `manifest.env` | release, time, and counts |

To keep backups elsewhere, add `--backup-dir <folder>` to `backup`,
`upgrade` and `restore`. The folder must be writable by you and outside
`~/mak4i-reference`; for a folder under `/srv`, create it first with
`sudo install -d -o "$USER" -m 700 /srv/mak4i-backups`. `--online` skips
the short stop (less consistent).

### 9.2 Restore

> **Restore replaces all current data** with the backup's: everything
> saved, and every change to people, access or app registrations after the
> backup, is lost.

```bash
cd ~/mak4i-reference
./deploy/bootstrap/mak4i-enterprise restore --from ~/mak4i-backups/mak4i-backup-20261001T120000Z
```

It checks the backup, asks you to type `restore`, restores, and checks the
counts. MAK4I is unavailable for under a minute on a small installation.

**You should see**
`Restore complete and verified: 1 2 1 2 0 (organizations principals projects grants credentials), … artifact file(s).`

**After a restore:**

- **Everyone signs in again**, in every app: a restore ends every
  sign-in, so access you removed after the backup can't come back through
  an old sign-in. Issue new codes ([5.4](#54-issue-a-sign-in-code)).
- **Re-apply removals made after the backup.** Access keys revoked, people
  removed, access withdrawn and projects archived after the backup are back
  as they were in the backup. Redo them (see
  [Everyday administration](#everyday-administration)); `audit list` before
  the restore shows what you changed.
- Access keys issued after the backup stop working.

**On a new server:** install first ([section 2](#2-install-mak4i)), then
restore. It brings back all organizations, people, access, app
registrations and access keys; the new server's own `.env` is kept.

It restores backups of `v2.1.0-rc.1` and of the releases it upgrades from
(`v0.1.0-rc.5`, `v0.1.0-rc.6`, `v2.0.0-rc.1`).

### 9.3 Undo an upgrade (roll back)

Use this only if the new release doesn't work for you. Rolling back
restores the backup taken at the upgrade, so **everything after the
upgrade is lost**:

- everything saved, replaced or resolved after the upgrade;
- organizations, projects, people, access and access keys created or
  changed after the upgrade (access keys issued after it stop working;
  ones revoked after it work again);
- app registrations and sign-ins made after the upgrade (coming from v0.1:
  all of them), per-app limits, and the change history;
- the administrative audit history after the upgrade.

Export what you need from that period first (for example
`mak4i audit list`, or ask an AI app for `mak4i_history`).

The rollback runs in three steps. The new release first checks the backup
and empties the database (keeping a **recovery backup**, so you can undo
the rollback); then the old release restores the backup. `upgrade`
printed these exact commands with your folder and release. For an upgrade
from `v2.0.0-rc.1`:

```bash
cd ~/mak4i-reference
B=~/mak4i-backups/mak4i-backup-<date and time>                    # printed by upgrade
./deploy/bootstrap/mak4i-enterprise rollback --from "$B"          # 1. with v2.1.0-rc.1: stop MAK4I, empty the database
git fetch --depth 1 origin tag v2.0.0-rc.1 && git checkout v2.0.0-rc.1   # rollback to the previous release
(cd deploy/compose && docker compose --profile tls build mak4i)   # 2. the previous release's MAK4I
./deploy/bootstrap/mak4i-enterprise restore --from "$B"           # 3. with the previous release: restore, start
```

From v0.1, use `v0.1.0-rc.5` or `v0.1.0-rc.6` instead of `v2.0.0-rc.1`.
For the `private-http` profile, leave out `--profile tls`.

**You should see** `rollback` ask you to confirm and name the recovery
backup, then the old release's `restore` end with
`Restore complete and verified`, and `status` show the old release.
Everyone signs in again afterwards.

(Reversing the database migrations, `alembic downgrade`, isn't a
rollback: it doesn't make data written by this release usable by an
earlier one. Use it only together with restoring data.)

## 10. Fix common problems

### 10.1 Installing: DNS, firewall and certificates

| What you see | What to do |
|---|---|
| Preflight `[FAIL] DNS A record`, or Caddy's log shows `NXDOMAIN` | The DNS record doesn't exist yet, or hasn't spread yet ([1, Step 3](#step-3-point-your-domain-name-at-it)). Check with `dig +short mak4i.example.com @1.1.1.1`. MAK4I retries by itself once it resolves. |
| Caddy's log shows `timeout` or `connection refused` during the challenge, or `https://…/ready` times out from your computer | Ports 80/443 are blocked ([1, Step 4](#step-4-open-ports-80-and-443)). Also check any firewall on the server itself. |
| Public DNS answers, but the server (and preflight) still can't | The server remembers the earlier "not found" for a while; it expires by itself. Meanwhile, check directly: `curl --resolve mak4i.example.com:443:203.0.113.10 https://mak4i.example.com/ready`. |
| A certificate from your CDN, or for the wrong address | Your DNS provider proxies the name. Set the record to **DNS only** ([1, Step 3](#step-3-point-your-domain-name-at-it)). |
| `install` exited with code 5 after "no valid certificate" | MAK4I is running; HTTPS isn't ready. Fix the cause above, then `./deploy/bootstrap/mak4i-enterprise status`. |
| Caddy's log mentions **rate limits** or `too many certificates` | You reinstalled with `uninstall --destroy-data` several times: Let's Encrypt allows only 5 certificates for the same name per week. Wait, or use another name for tests ([11.2](#112-remove-everything)). |
| `caddy` keeps restarting | `cd deploy/compose && docker compose --profile tls logs caddy`. Check `MAK4I_DOMAIN` in `.env`. |
| Preflight: `this user can't use Docker` | `sudo usermod -aG docker "$USER"`, then log out and back in. |
| Preflight: `Docker Compose plugin … missing` | Install Docker from Docker's packages ([1, Step 5](#step-5-connect-to-the-server-and-install-docker-and-git)). |
| Preflight `[WARN] Free disk … 20 GiB recommended` | The disk is small. Fine for a trial; for real use, a 30 GB disk. |

### 10.2 Running MAK4I

| What you see | What to do |
|---|---|
| `status`: `/ready failed` (or `/ready` returns `503`) | The database isn't reachable: is `postgres` healthy in `status`? `cd deploy/compose && docker compose logs postgres`. |
| `status` or `restart` fails after you edited `.env` | Run `docker compose up -d --wait mak4i` in `deploy/compose` ([section 3](#3-turn-on-sign-in)), and check the line you changed. |
| `required variable POSTGRES_PASSWORD is missing a value` | `.env` is missing or its password is empty. Restore `.env` from a backup's `env` file, or reinstall. |
| `migrate` exits with an error | `cd deploy/compose && docker compose logs migrate`. |
| `install` refuses: data volumes exist without `.env` | Put back the saved `.env` (a backup has it as `env`), or delete the old data with `uninstall --destroy-data`. |
| `install` refuses: a manual installation | Take it over with `upgrade` ([section 8](#8-find-your-release-and-upgrade)). |
| `error: OAuth is not enabled for this installation` | Turn on sign-in ([section 3](#3-turn-on-sign-in)). |
| `client_not_permitted` for an app | A per-app limit applies on that project ([19](#19-different-access-per-ai-app)). |
| Permission errors for saved files after a manual restore | Run the `chown` step in [21.4](#214-backup-and-restore). |
| The MAK4I log shows many `AUTHENTICATE_FAILURE … missing_token` events, for addresses like `/`, `/.env`, `/.git/HEAD` or `/robots.txt` | Normal: automated scanners probe every public server, and MAK4I refuses them. Each AI app also gets one such refusal just before it signs in. |
| The log shows `404` for `/.well-known/openid-configuration` | Normal: some apps look for it first. MAK4I isn't an OpenID provider; they then use its OAuth address. |

### 10.3 Connecting apps

| What you see | What to do |
|---|---|
| The app's sign-in fails at once, or the log shows requests to `/mcp.` (or another character after `/mcp`) | The address was copied with an extra character. Enter exactly `https://mak4i.example.com/mcp`. |
| `401` | Missing, wrong, revoked or expired sign-in or access key. The server deliberately doesn't say which. Sign in again. |
| The sign-in page says the code is invalid | Codes work once and for 10 minutes. Issue a new one ([5.4](#54-issue-a-sign-in-code)). |
| claude.ai: "Connect" shows no MAK4I sign-in page | Allow pop-ups for claude.ai. If the MAK4I log shows no `/oauth/authorize`, the client ID is missing: enter it under the connector's **Advanced settings** ([13.1](#131-claudeai)). |
| ChatGPT: "client metadata documents are accepted only for public clients" | Use **user-defined OAuth** with the client ID ([13.2](#132-chatgpt)). |
| ChatGPT: signs in, then "action discovery failed" | The address lacks `/mcp`. |
| `403 insufficient_scope`, or the app asks to sign in again in the middle of a task | The app needs a permission this sign-in didn't include (for example `mak4i:resolve` to settle a conflict). Sign in again with a new code; it's granted only if the person has that access. |
| `access denied … on MAK4I connection '…'` | The person has no access (or only `read`) on that project here. Give access ([section 4](#4-create-your-organization-people-and-projects)), rather than letting the app save elsewhere. |
| `404` | The address doesn't end in `/mcp`. |
| `421 Misdirected Request`, or sessions drop behind your own proxy | See [17](#17-a-private-network-or-your-own-load-balancer). |

## 11. Uninstall

### 11.1 Remove MAK4I, keep the data

```bash
cd ~/mak4i-reference
./deploy/bootstrap/mak4i-enterprise uninstall
```

Removes the MAK4I services. **Keeps** all data (database, saved
knowledge, certificate), `deploy/compose/.env` (its database password is
needed to open the data again) and your backups. To bring MAK4I back with
the same data, people and access:

```bash
./deploy/bootstrap/mak4i-enterprise install
```

### 11.2 Remove everything

> **Destructive and irreversible.** Take a backup first if there's any
> chance you'll need the data.

```bash
cd ~/mak4i-reference
./deploy/bootstrap/mak4i-enterprise uninstall --destroy-data
```

It lists exactly what it will delete, and you must type
`destroy mak4i data` to continue:

- the database: organizations, people, projects, access, access keys and
  app registrations (every sign-in and access key stops working);
- everything saved, with its history;
- the HTTPS certificate and its account;
- `deploy/compose/.env`.

It never deletes backups, the `mak4i-reference` folder, or shared Docker
images (`--remove-images` also removes MAK4I's own image).

**The certificate is deleted too.** A new installation for the same
domain requests a new one, and Let's Encrypt issues at most 5 for the
same name per week. Don't use `--destroy-data` for repeated test
installs; use [11.1](#111-remove-mak4i-keep-the-data) instead.

**Afterwards:** each user removes MAK4I from their AI apps
([15](#15-keep-your-access-safe)); delete the `mak4i-reference` folder,
`~/mak4i-ids.env` and the `~/mak4i-*.json` files if you no longer need
them; remove the DNS record, firewall rule, static address and server in
your cloud provider so they stop costing money.

---

# Part 2: Connect your AI app (AI users)

This part is for everyone on the team. You don't need a terminal, except
for Claude Code.

## 12. What to ask your administrator for

| You need | Example | For |
|---|---|---|
| your team's MAK4I address | `https://mak4i.example.com/mcp` | every app |
| the app's client ID | `mak4c_18bda695…` | claude.ai and ChatGPT only |
| a sign-in code, **when you reach the sign-in page** | `QMX9-TKRA-YG7E-…` | every app, once per app |

Ask for the code when your app shows the MAK4I sign-in page: it works
once, for 10 minutes.

**Copy the address exactly**, with nothing after `/mcp` (no full stop,
space or slash).

## 13. Connect your app

### 13.1 claude.ai

1. In claude.ai: **Settings → Connectors → Add custom connector**.
2. Name: your team's MAK4I name (for example `Acme MAK4I`). URL: the
   MAK4I address.
3. Open **Advanced settings** and enter the **OAuth Client ID** from your
   administrator. Leave the secret empty.
4. Choose **Add**, then **Connect**. A MAK4I sign-in page opens in a
   pop-up (allow pop-ups for claude.ai if nothing appears).
5. Sign in ([14](#14-sign-in-and-when-youll-sign-in-again)).

If you change the client ID of an existing connector, remove any second,
unconnected MAK4I connector left behind. Screens: [DEMO.md → Claude.ai /
Cowork](DEMO.md#claudeai--cowork).

### 13.2 ChatGPT

1. In ChatGPT: **Settings → Apps & Connectors → Advanced settings** →
   turn on **Developer mode**.
2. **Create** a connector. Name: your team's MAK4I name. URL: the MAK4I
   address (with `/mcp`).
3. Authentication: **OAuth**, then **user-defined OAuth**. Client ID: from
   your administrator. No secret. Scopes: `mak4i:read mak4i:write` (add
   `mak4i:resolve` only if you'll settle conflicts from ChatGPT and have
   that access).
4. Create it, then sign in ([14](#14-sign-in-and-when-youll-sign-in-again)).

Screens: [DEMO.md → ChatGPT](DEMO.md#chatgpt).

### 13.3 Claude Code

In a terminal on your computer (replace the address and the name):

```bash
claude mcp add --scope user --transport http acme-mak4i https://mak4i.example.com/mcp
```

Then, in Claude Code, type `/mcp`, choose `acme-mak4i`, and
**Authenticate**. Your browser opens the MAK4I sign-in page: sign in
([14](#14-sign-in-and-when-youll-sign-in-again)). Claude Code registers
itself; you need no client ID.

**You should see** `acme-mak4i` as connected in `/mcp`.

### 13.4 Grok

Add MAK4I as a remote MCP server (connector) in Grok, with the MAK4I
address, and choose to sign in. Grok registers itself (from
`https://grok.com/oauth/mcp-client.json`); you need no client ID. Then
sign in ([14](#14-sign-in-and-when-youll-sign-in-again)).

Grok has been seen to connect and sign in to MAK4I. Using MAK4I from Grok
(saving and reading) hasn't been tested yet for this release.

### 13.5 Other apps

Add MAK4I by its address and choose OAuth sign-in. If the app asks for a
client ID, ask your administrator to register the app
([5.3](#53-other-apps)). Other apps are untested.

## 14. Sign in, and when you'll sign in again

1. Your app opens the **MAK4I sign-in page**, which asks for a sign-in
   code.
2. Ask your administrator for a code now, and enter it.
3. The next page shows which app is asking and what it may do (for
   example read and write). Approve it.

**You should see** your app report MAK4I as connected. Then try
[6. Check it works](#6-check-it-works), steps 1 to 3.

New conversations reuse the sign-in. You sign in again (with a new code)
only:

- after 7 days without using MAK4I from that app, or 30 days in total;
- after your administrator removed your access or sign-in, or restored a
  backup;
- when the app needs a permission you haven't approved yet (for example
  settling a conflict).

**Several MAK4I connections** (for example your own Local MAK4I and your
team's): give each a different name in your app. MAK4I names itself in
every answer (`mak4i_whoami`, project lists and access denials). If MAK4I
refuses to save something, don't let the app save it to another MAK4I
connection unless you choose that deliberately: one MAK4I can't control
what an app does on another.

## 15. Keep your access safe

- A **sign-in code** is like a one-time password: don't share it or post
  it in a shared channel. It's useless after it's been used, or after 10
  minutes.
- A **client ID** (`mak4c_…`) isn't a secret.
- An **access key** (`mak4i_…`, [18](#18-access-keys-instead-of-sign-in))
  is a password: keep it out of chats, files you share, and code.
- **To disconnect**, remove MAK4I from the app (claude.ai or ChatGPT:
  remove the connector; Claude Code: `claude mcp remove acme-mak4i`), and
  ask your administrator to end your sign-in for that app.

---

# Part 3: Advanced

For platform engineers: the details behind Part 1, and options beyond
the recommended path.

## 16. How the installation is built

```
 AI client A ─┐                        ┌──────────────── one Linux VM (Docker Compose) ────────────────┐
 AI client B ─┼─ HTTPS :443 ──────────▶│ caddy ──▶ mak4i (MCP server, uid 10001) ──▶ postgres (private)  │
  (OAuth or   │  (tls profile)         │   :80/:443    │ 127.0.0.1:8080 only              volume pgdata  │
  credential) │                        │               └──▶ /data/artifacts (volume artifacts)          │
              └─ private-http profile ▶│ MAK4I HTTP on a private address (your TLS load balancer)       │
                                       └────────────────────────────────────────────────────────────────┘
```

| Service | What it does | Exposure |
|---|---|---|
| `postgres` | Control-plane database: organizations, principals, projects, grants, credential hashes, OAuth state. | **Never published.** Only reachable inside Compose. Anyone who can reach it can act as any principal. |
| `migrate` | Applies database migrations (`alembic upgrade head`) on every start, then exits `0`. | None. |
| `mak4i` | The MCP server (Streamable HTTP) and the `mak4i` operator CLI. Non-root (uid 10001), all Linux capabilities dropped, one replica. | `127.0.0.1:8080` (tls profile), or the private address you choose (private-http). |
| `caddy` | tls profile only: obtains and renews the certificate and proxies HTTPS to `mak4i`. | `0.0.0.0:80` and `:443`. |

Data lives in the Docker volumes `mak4i_pgdata` and `mak4i_artifacts`
(plus `mak4i_caddy_data` and `mak4i_caddy_config` with TLS). Volumes
survive restarts, upgrades and a data-preserving uninstall. This is a
single-instance deployment: no high availability is provided. Production
options (managed PostgreSQL, object storage, a secret manager, TLS at
your load balancer): [`DEPLOYMENT.md`](DEPLOYMENT.md#production-options).

The administration commands run inside the `mak4i` container and pass
`--actor` (the acting owner's principal ID) because they talk to the
database directly, with instance-operator trust. That's also why the
database must never be reachable from outside.

## 17. A private network or your own load balancer

The `tls` profile (Part 1) is for apps that connect from the internet:
claude.ai, ChatGPT and Grok always do. The `private-http` profile serves
MAK4I's plain HTTP instead, for clients on a trusted private network, or
behind your own TLS load balancer or reverse proxy.

| Profile | Use it when | What's exposed |
|---|---|---|
| `tls` | Cloud AI apps or anyone on the internet connect. | Caddy on 80/443 with an automatic certificate. MAK4I on `127.0.0.1:8080` only. |
| `private-http` | Clients reach MAK4I over a trusted private network, or you terminate TLS on your own load balancer. | MAK4I's plain HTTP on `127.0.0.1` (default) or the private address you pass with `--bind`. |

> **`private-http` is plain HTTP.** Bearer tokens cross that network
> unencrypted. Bind it to loopback (use an SSH tunnel) or a private address
> behind your TLS load balancer. The bootstrap warns for a private address
> and refuses a public one unless you pass `--allow-public-bind`, which you
> should only use behind a firewall that admits nothing but your load
> balancer. Binding to all interfaces (`0.0.0.0`) is never safe on its own.

Loopback (use an SSH tunnel or a local proxy):

```bash
./deploy/bootstrap/mak4i-enterprise install --profile private-http
```

Behind your own TLS load balancer, bind to the VM's private address and
give the URL clients will use (sign-in needs it to be `https`):

```bash
./deploy/bootstrap/mak4i-enterprise install --profile private-http \
  --bind 10.0.0.12 --endpoint https://mak4i.internal.example.com/mcp
```

Your proxy must not buffer streamed responses and must keep long requests
open: [`DEPLOYMENT.md` → Reverse proxy requirements](DEPLOYMENT.md#reverse-proxy-requirements).
A `421 Misdirected Request` means your proxy reaches MAK4I on a loopback
bind: keep `MAK4I_HOST=0.0.0.0` inside the container (as `compose.yaml`
sets) and control exposure with `MAK4I_HTTP_BIND`. Hosted apps such as
claude.ai can't reach a private-only installation.

`preflight --check-inbound` (tls) also tests that port 80 is reachable
from the internet by briefly running a throwaway responder container on
port 80. The VM can't always reach its own public IP, so if it warns, test
from another machine: `curl -v http://mak4i.example.com/`.

## 18. Access keys instead of sign-in

An app that can send a fixed `Authorization` header can use an **access
key** (a bearer credential) instead of signing in. Both can be used at
once.

```bash
cd ~/mak4i-reference/deploy/compose
docker compose exec -T mak4i mak4i credential issue --actor "$OWNER" --principal-id "$OWNER" --display-name "Owner - Claude Code"
```

The key is shown **once**, followed by a ready-to-paste connect command:

```
credential_id: cred_…
Authorization: Bearer mak4i_…
Save this token now. It will not be shown again.
```

For example, Claude Code:

```bash
claude mcp add acme-mak4i --transport http https://mak4i.example.com/mcp \
  --header "Authorization: Bearer <token>"
```

MAK4I stores only a hash of the key, so a lost key can't be recovered:
revoke it (`credential revoke --actor "$OWNER" --credential-id cred_…`)
and issue a new one. `credential list` shows keys without their secret;
`--expires-at` on `credential issue` sets an expiry.

With a key you can also act as yourself instead of passing `--actor`:
export it as `MAK4I_TOKEN` in your shell and run
`docker compose exec -e MAK4I_TOKEN mak4i mak4i …`. That's how anyone
(not only an owner) issues a sign-in code for themselves:
`docker compose exec -e MAK4I_TOKEN mak4i mak4i oauth sign-in-code`.

For Cowork, Gemini CLI and other clients, see
[`DEMO.md` → Connecting a client](DEMO.md#connecting-a-client).

**Sign-in details.** MAK4I includes its own OAuth 2.1 authorization server
(MAK-0008); no external identity provider is needed. Tokens never exceed
the principal's project grants, and revoking or deactivating takes effect
on the next request. Access tokens last 1 hour and are refreshed silently;
refresh tokens expire after 7 days unused or 30 days in total
([22](#22-configuration-reference)). ChatGPT also offers a client ID
metadata document, but it declares `private_key_jwt` client
authentication, and MAK4I accepts metadata documents only for public
clients (MAK-0008 §8.3): hence pre-registration for ChatGPT. Claude Code
requests every advertised scope (only grants limit it). Discovery:

```bash
curl -s https://mak4i.example.com/.well-known/oauth-protected-resource/mcp
curl -s https://mak4i.example.com/.well-known/oauth-authorization-server
```

Neither advertises a registration endpoint while `MAK4I_OAUTH_DCR=0`.

**Ending sign-ins.** `oauth authorization list|revoke`:
`--authorization-id oaz_…` (one), `--principal-id …` (all of one
person's), `--client-id …` (**every** person in your organization who
signed in with that app); `oauth client disable` stops an app.
Deactivating a person or removing their grant also stops their sign-ins
at once.

## 19. Different access per AI app

The same person can get different access to a project depending on the
app. For example, claude.ai read-only and ChatGPT read and write.
Effective access is the person's grant, limited by the sign-in's scopes,
limited by the app's policy.

1. Find the app: `oauth client list --actor "$OWNER"` (an access key:
   `credential list`).
2. Set a policy:
   `client-policy set --actor "$OWNER" --principal-id prn_… --project-id prj_… --oauth-client-id mak4c_… --permissions read`
   (for an access key, `--credential-id cred_…`).
3. Optional: `project client-restrictions --project-id prj_… --on` denies
   every app without a policy on that project. Run it without `--on`
   first to see who would lose access.

A refused action returns `client_not_permitted`. The app is identified by
its registration, never by the name it reports. Tokens are bearer tokens,
so a policy limits the app a token was issued to, not software that copies
it (MAK-0006 §5.7.9). Details:
[ADMIN_CLI.md → Client policies](ADMIN_CLI.md#client-policies-client-scoped-access-mak-0006-57).

## 20. Change notifications

MAK4I keeps a change feed per project (MAK-0008 §14). AI apps can
subscribe to project resources and read the feed with `mak4i_changes`.
Administrators read it with:

```bash
docker compose exec mak4i mak4i changes --principal "$OWNER" --project "$PROJECT" --after 0
```

Events are kept for `MAK4I_CHANGE_FEED_RETENTION_DAYS` (default 7) and
are delivered by this single MAK4I instance. Details:
[DEPLOYMENT.md → Change feed and MCP resources](DEPLOYMENT.md#change-feed-and-mcp-resources).

## 21. Manual Compose procedure

For troubleshooting, air-gapped or custom environments, and administrators
who want full control. The bootstrap does all of this for you; don't mix
the two on one installation unless a step says so. Run these from
`deploy/compose/`.

### 21.1 Configure

```bash
cd deploy/compose
cp .env.example .env
chmod 600 .env
```

Edit `.env` (every variable is described in
[Configuration reference](#22-configuration-reference)). At least:

- `POSTGRES_PASSWORD`: the output of `openssl rand -hex 32` (letters and
  digits only; it's embedded in a database URL);
- `MAK4I_DOMAIN` and `MAK4I_PUBLIC_ENDPOINT=https://<domain>/mcp` for TLS,
  or `MAK4I_PUBLIC_ENDPOINT=http://127.0.0.1:8080/mcp` without;
- keep `MAK4I_HTTP_BIND=127.0.0.1` with TLS.

### 21.2 Start and verify

Compose profiles: with `--profile tls`, Caddy is included; without it,
only PostgreSQL, the migration and MAK4I run. Pass the same profile to
every later `up`, `down` and `ps`.

```bash
docker compose --profile tls up -d --build --wait    # or: docker compose up -d --build --wait
docker compose --profile tls ps -a
```

Expected: `postgres` healthy, `migrate` exited (0), `mak4i` healthy,
`caddy` up. The `migrate` service runs `alembic upgrade head` before
`mak4i` starts, on every `up`, and is a no-op when the schema is current.
Check:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select version_num from alembic_version"'
curl -fsS http://127.0.0.1:8080/health; echo    # -> ok
curl -fsS http://127.0.0.1:8080/ready;  echo    # -> ready
```

Then [create your organization](#4-create-your-organization-people-and-projects).

### 21.3 Stop, start and logs

```bash
docker compose --profile tls stop     # stop containers; nothing is deleted
docker compose --profile tls start
docker compose --profile tls down     # remove containers and network; volumes (all data) are kept
docker compose --profile tls up -d --wait
docker compose logs migrate           # migration run (exit 0 = success)
```

### 21.4 Backup and restore

The bootstrap's `backup` ([9.1](#91-back-up)) does all of this for you,
consistently. By hand: **stop MAK4I first**, so the database and the
artifacts are copied from the same moment, and keep it stopped until both
are copied. `docker compose cp` also works on a stopped container. The
dump is written inside the container and copied out with
`docker compose cp`, which avoids shell redirection. Use a backup folder
you own, outside the repository (here `~/mak4i-manual-backup`).

```bash
mkdir -m 700 -p ~/mak4i-manual-backup
docker compose stop mak4i
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/mak4i-control-plane.dump'
docker compose exec -T postgres sh -c 'pg_restore --list /tmp/mak4i-control-plane.dump > /dev/null && echo dump-ok'
docker compose cp postgres:/tmp/mak4i-control-plane.dump ~/mak4i-manual-backup/mak4i-control-plane.dump
docker compose exec -T postgres rm /tmp/mak4i-control-plane.dump
docker compose cp mak4i:/data/artifacts ~/mak4i-manual-backup/mak4i-artifacts-backup
cp .env ~/mak4i-manual-backup/mak4i-compose.env && chmod 600 ~/mak4i-manual-backup/mak4i-compose.env
docker compose start mak4i
```

If any step fails, start MAK4I again with `docker compose start mak4i`
and repeat the backup from the beginning. A backup is verified when
`dump-ok` was printed and the artifacts copy contains one `org_…`
directory per organization.

Restore into a running stack (after `up`, before creating anything):

```bash
docker compose stop mak4i
docker compose cp ~/mak4i-manual-backup/mak4i-control-plane.dump postgres:/tmp/mak4i-control-plane.dump
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner /tmp/mak4i-control-plane.dump && rm /tmp/mak4i-control-plane.dump'
docker compose run --rm --no-deps --entrypoint find mak4i /data/artifacts -mindepth 1 -delete
docker compose cp ~/mak4i-manual-backup/mak4i-artifacts-backup/. mak4i:/data/artifacts/
docker compose run --rm --no-deps -u root --cap-add CHOWN --cap-add DAC_READ_SEARCH --entrypoint chown mak4i -R mak4i:mak4i /data/artifacts
docker compose --profile tls up -d --wait
```

The `chown` step is required: copied files keep the backup's owner and
mode, and MAK4I runs unprivileged with all capabilities dropped.
(`DAC_READ_SEARCH` lets that one-off `chown` descend into backup
directories you've made private.) Unlike the bootstrap's `restore`, this
doesn't revoke OAuth authorizations: run
`docker compose exec mak4i mak4i admin revoke-oauth --all` afterwards if
access was removed after the backup.

### 21.5 Upgrade

Back up first, then:

```bash
git fetch --depth 1 origin tag v2.1.0-rc.1
git checkout v2.1.0-rc.1
docker compose --profile tls up -d --build --wait
```

### 21.6 Remove

Keep the data (copy `.env` somewhere safe first; you need its password to
open the database again):

```bash
docker compose --profile tls down
```

**Destroy all data (irreversible; back up first):** `down -v` deletes the
volumes listed in [Remove everything](#112-remove-everything).

```bash
docker compose --profile tls down -v
rm .env
```

Avoid `docker system prune` or `docker volume prune`: they affect every
project on the host, not only MAK4I.

## 22. Configuration reference

The one table of Enterprise Self-Hosted settings. The bootstrap writes
`deploy/compose/.env`; `compose.yaml` fixes the rest. After changing
`.env`, run `docker compose up -d --wait mak4i` in `deploy/compose`.

| Variable | Where | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | `.env` (required) | Database password. Generated by the bootstrap. Letters and digits only. Changing it after the first start doesn't change it inside the existing database. |
| `POSTGRES_USER`, `POSTGRES_DB` | `.env` | Database user and name (default `mak4i`). |
| `MAK4I_PUBLIC_ENDPOINT` | `.env` | The client-facing MCP URL, including `/mcp`. Written by `install`. Used by `credential issue` to print connect commands, shown by `mak4i_whoami`, and the base of the OAuth addresses. |
| `MAK4I_DOMAIN` | `.env` (tls) | The DNS name Caddy gets a certificate for. |
| `MAK4I_TLS_ISSUER` | `.env` (tls) | `acme` (default; Let's Encrypt) or `internal` (Caddy's own CA, for private networks; clients must trust `/data/caddy/pki/authorities/local/root.crt` from the `caddy` container). |
| `MAK4I_HTTP_BIND` | `.env` | Host address MAK4I's HTTP port is published on. `127.0.0.1` with TLS; a private address for private-http. **Never** a public address or `0.0.0.0` without a firewall in front. |
| `MAK4I_HTTP_PORT` | `.env` | Host port for MAK4I's HTTP listener (default `8080`). |
| `MAK4I_INSTANCE_NAME`, `MAK4I_ENVIRONMENT` | `.env` | How this installation names itself to AI apps (`mak4i_whoami`, project listings, denials). Set by `install --instance-name/--environment`; default `MAK4I Enterprise` / `enterprise`. Give each installation a distinct name, also when several run on one machine. |
| `MAK4I_OAUTH_ENABLED` | `.env` | `1` turns on sign-in ([section 3](#3-turn-on-sign-in)); default `0`. Requires an https `MAK4I_PUBLIC_ENDPOINT`. |
| `MAK4I_OAUTH_ISSUER` | `.env` | OAuth issuer. Default: `MAK4I_PUBLIC_ENDPOINT` without its trailing `/mcp`. Same origin as the endpoint. |
| `MAK4I_OAUTH_CIMD`, `MAK4I_OAUTH_DCR` | `.env` | Client registration modes: client ID metadata documents (default `1`; Claude Code, Grok) and dynamic client registration (default `0`). Turning on dynamic registration lets any app register itself; it's off by design. |
| `MAK4I_OAUTH_CIMD_ALLOWED_HOSTS` | `.env` | Optional comma-separated allowlist of hosts whose client metadata documents are accepted. |
| `MAK4I_OAUTH_ACCESS_TOKEN_TTL`, `MAK4I_OAUTH_REFRESH_IDLE_TTL`, `MAK4I_OAUTH_REFRESH_ABSOLUTE_TTL`, `MAK4I_OAUTH_CODE_TTL`, `MAK4I_OAUTH_SIGN_IN_CODE_TTL` | `.env` | Token policy in seconds; defaults 3600, 604800 (7 days), 2592000 (30 days), 60, 600. After the idle or absolute lifetime a person must sign in again. |
| `MAK4I_OAUTH_RATE_LIMIT_PER_MINUTE` | `.env` | Sign-in / token / revocation / registration attempts per client address per minute (default 20). |
| `MAK4I_TRUSTED_PROXIES` | `.env` | Reverse proxies whose `X-Forwarded-For` is trusted (default none). With the `tls` profile, set it to the Compose network (find it with `docker network inspect mak4i_default`) so rate limits see real client addresses. |
| `MAK4I_LOG_LEVEL` | `.env` | Log level (default `INFO`). |
| `MAK4I_CHANGE_FEED_RETENTION_DAYS` | `.env` | Days change-feed events are kept (default and minimum 7). |
| `MAK4I_BOOTSTRAP_PROFILE`, `MAK4I_INSTALL_STATE`, `MAK4I_INSTALLED_RELEASE` | `.env` | Bootstrap bookkeeping. Don't edit. |
| `MAK4I_TRANSPORT=http`, `MAK4I_HOST=0.0.0.0`, `MAK4I_TLS_TERMINATED_UPSTREAM=1`, `MAK4I_PORT=8080`, `MAK4I_STORE=local`, `MAK4I_LOCAL_STORE_DIR=/data/artifacts`, `MAK4I_CONTROL_PLANE_DB` | `compose.yaml` (fixed) | The container runtime settings. `MAK4I_HOST=0.0.0.0` applies **inside** the container only; host exposure is `MAK4I_HTTP_BIND`. |

Check the configuration the way the server will (no secrets printed):
`docker compose exec mak4i mak4i config check`.

The full runtime contract for running the container on another platform is
in [`DEPLOYMENT.md` → Runtime contract](DEPLOYMENT.md#runtime-contract).

> **Secrets.** A mode-600 `.env` on a single VM is acceptable for this
> Developer Preview. Production deployments keep the database URL in a
> secret manager ([`DEPLOYMENT.md` → Security](DEPLOYMENT.md#security)).

## 23. Bootstrap reference

```
./deploy/bootstrap/mak4i-enterprise <command> [options]      # --help for all options
```

| Command | Purpose | Changes state |
|---|---|---|
| `preflight` | Check host, Docker, network, configuration | No (`--check-inbound` briefly runs a probe container) |
| `install` | Configure, start, verify | Yes (`--dry-run` shows the plan) |
| `status` | Containers, version, health, readiness, schema, data, HTTPS | No |
| `admin-info` | Non-secret inventory: organizations, owners, projects, principals (incl. `agent_id`), grants, credential metadata. Use it to recover ids you didn't keep; tokens can't be recovered, only reissued | No |
| `restart` | Restart (or start after a stop) and verify health and data | Yes |
| `upgrade` | Back up, then `v0.1.0-rc.5`, `v0.1.0-rc.6` or `v2.0.0-rc.1` → `v2.1.0-rc.1` (`--from-version` if the stack is stopped; `--use-backup DIR` to verify and use an existing backup) | Yes |
| `backup` | Timestamped, verified backup (default folder `~/mak4i-backups`) | Writes the backup only |
| `restore` | Replace all data with a backup; revokes every OAuth authorization | Yes (confirmation required) |
| `rollback` | Prepare a rollback to the pre-upgrade release: take a recovery backup, stop MAK4I and empty the database (then restore with that release) | Yes (confirmation required) |
| `uninstall` | Remove containers; `--destroy-data` removes data too | Yes (destroy needs confirmation) |

Options never take secrets; `--non-interactive` never prompts (a command
that needs confirmation then fails unless its confirmation flag is given,
for example `--yes` for `restore`, `--destroy-data --yes-destroy-data` for
`uninstall`); `--dry-run` shows what a state-changing command would do;
`--cert-timeout` sets how long `install` waits for the certificate.

**Instances on one machine.** Give every MAK4I installation its own name
(`--instance-name`): AI apps tell connections apart by it.

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
| 8 | Backup or restore validation failed (including a backup folder that can't be created) |

## 24. What has been tested

As of 2026-10-23, for `v2.1.0-rc.1`:

| Path | How it was checked |
|---|---|
| Part 1 on a fresh Debian 12 VM on Google Cloud (2 vCPU, 4 GB): Docker from Docker's packages, preflight, install, sign-in, organization setup, first save and read, restart, reboot, backup, restore, uninstall with `--destroy-data` | run on a real server |
| Upgrade from `v2.0.0-rc.1`, with rollback; upgrade directly from `v0.1.0-rc.6` | run on a real server |
| Upgrade directly from `v0.1.0-rc.5` | accepted by the installer (same path as `v0.1.0-rc.6`); not repeated on a server for this guide |
| claude.ai and ChatGPT: register, sign in, read; ChatGPT also saved and read back | real apps, with a person |
| Per-app limits | automated tests, and real apps: with claude.ai limited to read, its write was refused by the server (`client_not_permitted`) while ChatGPT, allowed to write, saved |
| Claude Code (self-registration and sign-in) | real app |
| Grok (self-registration and sign-in) | real app connected and signed in; saving and reading not yet tested |
| Upgrades from `v0.1.0-rc.4` and earlier (the in-between steps) | from those releases' own guides; not repeated for this release |
| Ubuntu, Debian 13 | tested with earlier releases |
| AWS, Azure | not tested |
| A person without IT experience following this guide alone | not yet done |

## 25. Beyond this release

- **Not provided yet:** Terraform (or other infrastructure-as-code)
  modules for provisioning the VM, IP, DNS and firewall, and Kubernetes or
  Helm packaging. This release supports the single-VM Compose stack and
  its bootstrap only.
- **Windows:** the bootstrap is Linux-only; Enterprise Self-Hosted isn't
  supported on Windows or macOS hosts in this release.
- **Production:** managed PostgreSQL, object storage for artifacts, a
  secret manager and TLS at your load balancer: see
  [`DEPLOYMENT.md` → Production options](DEPLOYMENT.md#production-options).
- **Sign-in codes** are issued by an owner (or by a person with their own
  access key); users can't yet request one themselves.
