# MAK4I on your own computer (Local Setup)

> **Developer Preview.** This is a release candidate (`v2.1.0-rc.1`) for
> evaluation and feedback, not a production-supported release.

## What this guide covers

This guide shows you how to install the free MAK4I on your own computer,
connect your AI apps to it, check that it works, and keep it up to date.

- **Your AI apps:** apps on this computer, such as Claude Code, connect
  directly. Cloud apps such as **claude.ai** and **ChatGPT** can connect
  too, through a secure tunnel or HTTPS proxy that you set up
  ([section 4](#4-use-mak4i-from-claudeai-or-chatgpt)).
- **Your data:** MAK4I stores everything in a folder on this computer.
  When an AI app reads from MAK4I, what it reads is sent to that app, and
  to its provider if the app runs in the cloud. If you use a tunnel, your
  MAK4I traffic also passes through the tunnel provider.
- **Systems:** macOS, Linux and Windows. Every step has commands for
  macOS and Linux, and for Windows PowerShell.

**Running MAK4I for a team?** The
[Enterprise Self-Hosted guide](ENTERPRISE_SELF_HOSTED.md) installs one
shared MAK4I on a Linux server, with HTTPS and sign-in built in. You don't
need it for yourself, including for claude.ai or ChatGPT.

## How MAK4I runs, and how your AI apps connect

Read this before you install anything.

**One MAK4I at a time.** Only one copy of MAK4I may run on an
installation at any moment. A second copy refuses to start, with the
message `another MAK4I server is already running for this installation`
(for example, when two Claude Code windows each try to start their own
MAK4I). So there are two ways to run it:

| | **The MAK4I server** (recommended) | **Started by Claude Code** |
|---|---|---|
| How it works | You start MAK4I in a terminal window and leave that window open. Your apps connect to the address `http://127.0.0.1:9090/mcp`. | Claude Code starts MAK4I by itself when it needs it. |
| Use it with | Claude Code (any number of windows), other apps on this computer, and claude.ai or ChatGPT through a tunnel | **one** Claude Code window at a time, and no other app |
| Set up in | [3. Start MAK4I and connect your AI app](#3-start-mak4i-and-connect-your-ai-app) | [3.4 Simpler alternative](#34-simpler-alternative-let-claude-code-start-mak4i) |

What each AI app needs:

| Your AI app | What you need | Section |
|---|---|---|
| **Claude Code** | the MAK4I server | [3.2](#32-connect-claude-code) |
| **Another app on this computer** that connects to an address (URL) | the MAK4I server | [3.3](#33-connect-other-apps-on-this-computer) |
| **claude.ai** or **ChatGPT** | the MAK4I server, an HTTPS tunnel or proxy you set up with a provider you choose, and a one-time sign-in | [4](#4-use-mak4i-from-claudeai-or-chatgpt) |

## Installation at a glance

1. [Before you start](#1-before-you-start): make sure Git and uv are
   installed.
2. [Install MAK4I](#2-install-mak4i-step-by-step): download it, install it,
   and create your organization and first project.
3. [Start MAK4I and connect your AI app](#3-start-mak4i-and-connect-your-ai-app).
4. Only for claude.ai or ChatGPT:
   [set up access through a tunnel](#4-use-mak4i-from-claudeai-or-chatgpt).
5. [Check it works](#5-check-it-works).

You type commands into a terminal window. Every step shows exactly what to
copy and what you should see afterwards.

## Find your section

| I want to… | Go to |
|---|---|
| Install MAK4I for the first time | [1. Before you start](#1-before-you-start), then [2. Install MAK4I](#2-install-mak4i-step-by-step) |
| Connect Claude Code or another app on this computer | [3. Start MAK4I and connect your AI app](#3-start-mak4i-and-connect-your-ai-app) |
| Use MAK4I from claude.ai or ChatGPT | [4. Use MAK4I from claude.ai or ChatGPT](#4-use-mak4i-from-claudeai-or-chatgpt) |
| Check that MAK4I works | [5. Check it works](#5-check-it-works) |
| Start or stop MAK4I | [6. Start and stop](#6-start-and-stop) |
| Upgrade to a new version | [7. Upgrade](#7-upgrade) |
| Make a backup, or restore one | [8. Back up and restore](#8-back-up-and-restore) |
| Fix a problem | [9. Fix common problems](#9-fix-common-problems) |
| Remove MAK4I | [10. Uninstall](#10-uninstall) |
| Developer options: server details, network access without a tunnel, OAuth for local apps, manual setup, settings | [Part 2: Advanced](#part-2-advanced) |

**Follow one section from start to finish.** You don't need to read the
other sections first.

### Contents

**Part 1: Everyday use**

1. [Before you start](#1-before-you-start)
2. [Install MAK4I, step by step](#2-install-mak4i-step-by-step)
3. [Start MAK4I and connect your AI app](#3-start-mak4i-and-connect-your-ai-app)
4. [Use MAK4I from claude.ai or ChatGPT](#4-use-mak4i-from-claudeai-or-chatgpt)
5. [Check it works](#5-check-it-works)
6. [Start and stop](#6-start-and-stop)
7. [Upgrade](#7-upgrade)
8. [Back up and restore](#8-back-up-and-restore)
9. [Fix common problems](#9-fix-common-problems)
10. [Uninstall](#10-uninstall)

**[Part 2: Advanced](#part-2-advanced)** (for developers)

11. [Server details](#11-server-details)
12. [Network access without Host rewriting](#12-network-access-without-host-rewriting)
13. [OAuth sign-in for apps on your computer](#13-oauth-sign-in-for-apps-on-your-computer)
14. [Manual and advanced setup](#14-manual-and-advanced-setup)
15. [Configuration reference](#15-configuration-reference)
16. [Credential recovery](#16-credential-recovery)
17. [Health and readiness checks](#17-health-and-readiness-checks)

---

# Part 1: Everyday use

**Commands for your system.** Where the commands differ, each step has a
box for **macOS and Linux** and a box for **Windows** (PowerShell). Copy
the box for your system only, exactly as shown. The Windows commands for
installing, connecting, sign-in, backup and undo are run automatically on
Windows on every change to MAK4I; the uninstall commands aren't.

**Always start in the MAK4I folder.** MAK4I keeps its data in the folder you
install it into. Whenever you open a new terminal window to run a MAK4I
command, go to that folder first:

macOS and Linux:

```bash
cd ~/mak4i-reference
```

Windows:

```powershell
Set-Location ~\mak4i-reference
```

## 1. Before you start

You need:
- a computer with macOS, Linux or Windows 10/11;
- an internet connection;
- about 300 MB of free disk space;
- the AI app you want to use, for example
  [Claude Code](https://code.claude.com/docs/en/overview), installed and
  signed in.

You don't need to install Python: uv (below) downloads the right version by
itself.

### Step 1: Open a terminal

- **macOS:** press Cmd+Space, type `Terminal`, press Enter.
- **Windows:** press the Windows key, type `PowerShell`, press Enter.
- **Linux:** open your Terminal app.

### Step 2: Check for Git, and install it if needed

Git downloads MAK4I. Type:

```bash
git --version
```

**You should see** a line starting with `git version`, which means Git is
installed.

If you see "command not found":
- **macOS:** a window offers to install the "command line developer
  tools". Click **Install**, wait until it finishes, then run
  `git --version` again.
- **Windows:** download Git from <https://git-scm.com/download/win>, run the
  installer with the default options, then close PowerShell and open it
  again.
- **Linux (Debian or Ubuntu):** run `sudo apt install git`.

### Step 3: Check for uv, and install it if needed

uv installs MAK4I and the Python it needs. Type:

```bash
uv --version
```

**You should see** a line starting with `uv`, which means uv is installed.
If a later step fails with a uv error, update it with `uv self update`.

If you see "command not found" (or a message that uv isn't recognized),
install uv:

macOS and Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then **close the terminal and open a new one**, and run `uv --version`
again. (More options: [uv installation](https://docs.astral.sh/uv/getting-started/installation/).)

## 2. Install MAK4I, step by step

### Step 1: Go to your home folder

MAK4I is installed in a folder named `mak4i-reference` inside your home
folder.

macOS and Linux:

```bash
cd ~
```

Windows:

```powershell
Set-Location ~
```

### Step 2: Download MAK4I

The command is the same on every system:

```bash
git clone --branch v2.1.0-rc.1 --depth 1 https://github.com/talvikai/mak4i-reference.git
```

**You should see** `Cloning into 'mak4i-reference'...`. It may be followed
by a longer note about "detached HEAD", or a warning that the tag "is not a
commit". Both are normal.

### Step 3: Install it

macOS and Linux:

```bash
cd mak4i-reference
uv sync --no-editable
```

Windows:

```powershell
Set-Location mak4i-reference
uv sync --no-editable
```

The first time, this downloads Python and MAK4I's libraries. It can take a
few minutes, and longer on a Mac with an Intel processor, where one library
is built on your computer.

**You should see** a line like `Installed 50 packages`, followed by a list
of package names, each starting with `+`.

### Step 4: Check the installation

macOS and Linux:

```bash
.venv/bin/mak4i --version
```

Windows:

```powershell
.\.venv\Scripts\mak4i.exe --version
```

**You should see** `mak4i 2.1.0rc1`.

### Step 5: Create your organization and first project

macOS and Linux:

```bash
.venv/bin/mak4i init
```

Windows:

```powershell
.\.venv\Scripts\mak4i.exe init
```

It asks four questions:

| Question | What to type |
|---|---|
| Organization name | your company's name, or your own name |
| Your display name | your name, as AI apps should show it |
| First project name | the project you'll use MAK4I for, for example the app you're building |
| Local HTTP port | just press Enter (this keeps the address `http://127.0.0.1:9090/mcp`) |

**You should see** a few lines starting with `{"event": "ADMIN"` (a log of
what was created; you can ignore them), six lines starting with `✓`, then
`MAK4I is ready.` with your organization, principal and project IDs, and
two connection options. **Ignore the connection commands it prints**:
section 3 shows the ones to use.

MAK4I created a hidden folder named `.mak4i` inside `mak4i-reference`. It
holds all your MAK4I data, including your private access key. Don't delete
it, and don't share it.

Next: [3. Start MAK4I and connect your AI app](#3-start-mak4i-and-connect-your-ai-app).

## 3. Start MAK4I and connect your AI app

### 3.1 Start the MAK4I server

Open a terminal window, go to the MAK4I folder, and start MAK4I:

macOS and Linux:

```bash
cd ~/mak4i-reference
.venv/bin/mak4i serve --transport http
```

Windows:

```powershell
Set-Location ~\mak4i-reference
.\.venv\Scripts\mak4i.exe serve --transport http
```

**You should see**, ending with:

```
MCP endpoint: http://127.0.0.1:9090/mcp
...
MCP server ready.
```

**Leave this window open.** MAK4I runs as long as it's open. Use a new
terminal window for the next steps. To stop MAK4I, press Ctrl+C in this
window.

MAK4I listens only on this computer: other computers can't reach it. If
you see `another MAK4I server is already running for this installation`
instead, MAK4I is already running: use that one.

### 3.2 Connect Claude Code

This makes MAK4I available in **all** your Claude Code projects and
windows. In a new terminal window:

macOS and Linux:

```bash
cd ~/mak4i-reference
claude mcp add --transport http -s user mak4i http://127.0.0.1:9090/mcp --header "Authorization: Bearer $(.venv/bin/python -c 'import json; print(json.load(open(".mak4i/credentials.json"))["token"])')"
```

Windows:

```powershell
Set-Location ~\mak4i-reference
claude mcp add --transport http -s user mak4i http://127.0.0.1:9090/mcp --header "Authorization: Bearer $((Get-Content .mak4i\credentials.json | ConvertFrom-Json).token)"
```

This reads your private access key from `.mak4i/` and saves it in Claude
Code's settings on this computer.

**You should see** `Added HTTP MCP server mak4i with URL: http://127.0.0.1:9090/mcp to user config`. If it
says a server named `mak4i` already exists, remove the old one first with
`claude mcp remove -s user mak4i` (and `claude mcp remove mak4i`), then run
the command above again.

Check the connection (the MAK4I server from 3.1 must be running):

```bash
claude mcp get mak4i
```

**You should see:**

```
  Scope: User config (available in all your projects)
  Status: ✔ Connected
  Type: http
```

### 3.3 Connect other apps on this computer

Apps that connect to an MCP server by address (URL) use:
- **address:** `http://127.0.0.1:9090/mcp`
- **header:** `Authorization: Bearer <your access key>`. Your access key is
  the `token` value in `.mak4i/credentials.json`.

Each app's exact steps:
[DEMO.md → Connecting a client](DEMO.md#connecting-a-client).

### 3.4 Simpler alternative: let Claude Code start MAK4I

If you use MAK4I **only from Claude Code, and only in one Claude Code
window at a time**, Claude Code can start MAK4I by itself. You then don't
need the MAK4I server from 3.1. Don't use both at once.

macOS and Linux:

```bash
cd ~/mak4i-reference
claude mcp add -s user mak4i -- sh -c "cd \"$PWD\" && exec .venv/bin/mak4i serve"
```

Windows:

```powershell
Set-Location ~\mak4i-reference
claude mcp add -s user mak4i -- cmd /c "cd /d `"$PWD`" && .venv\Scripts\mak4i.exe serve"
```

**You should see** `Added stdio MCP server mak4i … to user config`, and
`claude mcp get mak4i` shows `Status: ✔ Connected`.

With this setup, each Claude Code window starts its own copy of MAK4I, and
only one copy can run: a second Claude Code window shows MAK4I as failed
to connect, and the MAK4I server (3.1) refuses to start while Claude Code
runs MAK4I. Quit the first Claude Code window first, or use the MAK4I
server (3.1) instead. If you move or rename the `mak4i-reference` folder, run the
command again from the new location.

## 4. Use MAK4I from claude.ai or ChatGPT

claude.ai and ChatGPT run in the cloud, so they can't reach
`127.0.0.1` on your computer. To use your local MAK4I, you give it a
public HTTPS address with a **tunnel** or **HTTPS reverse proxy** that you
set up yourself, with a provider you choose. Setting up the tunnel follows
your provider's own documentation; this section lists what MAK4I needs
from it.

**This setup hasn't been tested end to end** with a particular tunnel
provider and claude.ai or ChatGPT. The MAK4I side was checked on its own:
the address it advertises, the Host header handling and the sign-in
commands.

### 4.1 What your tunnel or proxy must do

1. **Give you a public `https://` address** that forwards to
   `http://127.0.0.1:9090` on this computer (your Local HTTP port). Your
   apps use that address followed by `/mcp`, for example
   `https://mak4i.example.com/mcp`.
2. **Rewrite the Host header to `127.0.0.1:9090`.** MAK4I listens only on
   this computer and rejects any other Host header with
   `421 Invalid Host header`. Providers call this setting "host header" or
   similar; Cloudflare Tunnel, for example, calls it
   [`httpHostHeader`](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/).
   If your provider can't rewrite it, see
   [12. Network access without Host rewriting](#12-network-access-without-host-rewriting).
3. **Stream responses without buffering, and allow long requests.** MAK4I
   answers every request as a stream of server-sent events (SSE). A
   tunnel that buffers or doesn't support SSE won't work. For example,
   Cloudflare's temporary
   [Quick Tunnels don't support SSE](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).
4. **Keep the same address.** Sign-ins from claude.ai and ChatGPT are tied
   to the address. If it changes, see
   [4.3](#43-if-your-public-address-changes).

**Availability.** claude.ai and ChatGPT can use MAK4I only while your
computer is on, the MAK4I server (3.1) is running and the tunnel is
connected.

**Privacy.** Everything claude.ai or ChatGPT reads from MAK4I is sent to
Anthropic or OpenAI, and passes through your tunnel provider.

### 4.2 Steps

Replace `https://mak4i.example.com` with your own public address in every
command below.

**Step 1: Set up your tunnel or proxy** following your provider's
documentation and the requirements in 4.1.

**Step 2: Restart MAK4I with sign-in turned on.** In the window where the
MAK4I server runs, press Ctrl+C, then start it again with your address:

macOS and Linux:

```bash
cd ~/mak4i-reference
MAK4I_OAUTH_ENABLED=1 MAK4I_PUBLIC_ENDPOINT=https://mak4i.example.com/mcp .venv/bin/mak4i serve --transport http
```

Windows (the two `$env:` settings last until you close this PowerShell
window):

```powershell
Set-Location ~\mak4i-reference
$env:MAK4I_OAUTH_ENABLED = "1"
$env:MAK4I_PUBLIC_ENDPOINT = "https://mak4i.example.com/mcp"
.\.venv\Scripts\mak4i.exe serve --transport http
```

Use these two settings every time you start the MAK4I server from now on.

**Step 3: Check your public address.** In a web browser, open
`https://mak4i.example.com/.well-known/oauth-protected-resource/mcp`.

**You should see** text starting with
`{"resource":"https://mak4i.example.com/mcp"`. If you see an error, check
the tunnel against 4.1.

**Step 4: Register the app.** Open a **new** terminal window. Every MAK4I
sign-in command needs the same two settings as the server.

macOS and Linux (the settings are part of each command):

```bash
cd ~/mak4i-reference
MAK4I_OAUTH_ENABLED=1 MAK4I_PUBLIC_ENDPOINT=https://mak4i.example.com/mcp .venv/bin/mak4i oauth client register --name "Claude (claude.ai)" --redirect-uri https://claude.ai/api/mcp/auth_callback
MAK4I_OAUTH_ENABLED=1 MAK4I_PUBLIC_ENDPOINT=https://mak4i.example.com/mcp .venv/bin/mak4i oauth client register --name "ChatGPT" --redirect-uri https://chatgpt.com/connector_platform_oauth_redirect
```

Windows (set them once in this new PowerShell window):

```powershell
Set-Location ~\mak4i-reference
$env:MAK4I_OAUTH_ENABLED = "1"
$env:MAK4I_PUBLIC_ENDPOINT = "https://mak4i.example.com/mcp"
.\.venv\Scripts\mak4i.exe oauth client register --name "Claude (claude.ai)" --redirect-uri https://claude.ai/api/mcp/auth_callback
.\.venv\Scripts\mak4i.exe oauth client register --name "ChatGPT" --redirect-uri https://chatgpt.com/connector_platform_oauth_redirect
```

Run only the line for the app you use (claude.ai, ChatGPT, or both).

**You should see** `OAuth client registered.` and a `client_id:` starting
with `mak4c_`. Keep it for the next step.

**Step 5: Add MAK4I in the app**, with the address
`https://mak4i.example.com/mcp` and the client ID from Step 4. The screens
for each app:
[DEMO.md → Claude.ai / Cowork](DEMO.md#claudeai--cowork) and
[DEMO.md → ChatGPT](DEMO.md#chatgpt).

**Step 6: Sign in.** The app opens a MAK4I sign-in page that asks for a
sign-in code. In the terminal window from Step 4, run:

macOS and Linux:

```bash
cd ~/mak4i-reference
MAK4I_OAUTH_ENABLED=1 MAK4I_PUBLIC_ENDPOINT=https://mak4i.example.com/mcp .venv/bin/mak4i oauth sign-in-code
```

Windows (in the window from Step 4, where the two settings are set):

```powershell
.\.venv\Scripts\mak4i.exe oauth sign-in-code
```

**You should see** `Sign-in code created.` and a `sign-in code:` line.
Enter that code on the sign-in page, then approve the consent page. A code
works once, for 10 minutes. Never share it.

After that, new conversations reuse the connection. Sign in again (Step 6)
after 7 days without use, after 30 days in total, or after the address
changes.

### 4.3 If your public address changes

Some tunnels give you a new address every time they restart. Then:

1. Restart the MAK4I server with the new address in
   `MAK4I_PUBLIC_ENDPOINT` (Step 2).
2. In claude.ai or ChatGPT, change the connector's address to the new one.
3. Sign in again (Step 6). Sign-ins made for the old address stop working.

Apps on this computer (section 3) aren't affected. A tunnel with a fixed
address avoids all of this.

## 5. Check it works

1. Make sure the MAK4I server is running (3.1), unless you chose the
   alternative in 3.4.
2. Open your AI app. For Claude Code, open it in any project folder.
3. Ask: **"Use MAK4I to show who I am and list my projects."**

   **You should see** your organization, the connection
   `MAK4I local (<your organization>)`, and your project with the
   permissions `read` and `write`. The app may ask your permission to use
   the MAK4I tools the first time; allow it.
4. Ask: **"Save this in my MAK4I project: our app uses PostgreSQL as its
   database."**
5. Start a **new** conversation and ask: **"According to MAK4I, which
   database does my project use?"**

   **You should see** PostgreSQL. MAK4I remembered it across
   conversations.

In Claude Code, `/mcp` also lists `mak4i` as connected.

**What your AI app can do with MAK4I:**

| Tool | Purpose |
|---|---|
| `mak4i_whoami` | which MAK4I connection this is (name, environment, version), your organization and principal |
| `mak4i_list_projects` | the projects your credential can act on, with permissions and connection |
| `mak4i_search` | raw candidate lookup by type, tags and status |
| `mak4i_get_current` | the resolved current knowledge (conflict- and integrity-checked) |
| `mak4i_create` | write new durable knowledge (optional `subject_key` names the subject it decides) |
| `mak4i_supersede` | replace current knowledge, keeping lineage and history (`release_subject_key=true` gives up the lineage's subject claim, except while it is in conflict) |
| `mak4i_history` | every version in a lineage, oldest first |
| `mak4i_list_conflicts`, `mak4i_get_conflict` | subjects claimed by more than one lineage, with every candidate and its author |
| `mak4i_resolve_conflict` | resolve a conflict the user has decided (needs the `resolve` permission, which `mak4i init` doesn't give: see [14.1](#141-what-mak4i-init-creates)) |
| `mak4i_changes` | what changed in a project after a cursor (`mak4i changes` on the CLI) |

**Several MAK4I connections.** A local MAK4I names itself
`MAK4I local (<organization>)`, environment `local`. AI apps show this
name in `mak4i_whoami`, project lists and access denials. When an app is
connected to more than one MAK4I, check where it proposes to write: one
MAK4I can't control what an app does on another.

## 6. Start and stop

**The MAK4I server** (3.1) runs while its terminal window is open.
- **Stop:** press Ctrl+C in that window.
- **Start:** run the command from 3.1 again (with the two sign-in settings
  from 4.2 if you use claude.ai or ChatGPT).
- **After restarting your computer**, start it again before you use your
  AI apps.

**Started by Claude Code** (3.4): Claude Code starts MAK4I when it needs it,
and MAK4I stops when you quit Claude Code.

Stopping MAK4I never deletes anything, and your access key stays valid.

## 7. Upgrade

Upgrading keeps your data and your app connections. You need the version
number of the new release, for example `v2.1.0-rc.1`; the current one is
shown at the top of [`README.md`](../README.md).

**Which releases can upgrade?** Every Local release, from `v0.1.0-rc.1`
on, upgrades **directly** to the new one with the steps below: there are
no in-between versions to install. MAK4I updates its data by itself the
first time the new version runs. (Tested from `v0.1.0-rc.1`,
`v0.1.0-rc.4` and `v2.0.0-rc.1`.)

### Step 1: Go to the MAK4I folder

macOS and Linux:

```bash
cd ~/mak4i-reference
```

Windows:

```powershell
Set-Location ~\mak4i-reference
```

### Step 2: Find your current version

The command is the same on every system:

```bash
git describe --tags --always
```

**Write down what it prints**: you need it if you ever want to go back
([undo the upgrade](#undo-the-upgrade)). What it means:

| It prints | You're on |
|---|---|
| a version, for example `v2.0.0-rc.1` | that release |
| a version with more after it, for example `v2.0.0-rc.1-12-g1a2b3c4` | a development version 12 changes after that release |
| only letters and numbers, for example `1a2b3c4` | a development version (you downloaded MAK4I without a version tag) |

To see the version MAK4I itself reports, run `.venv/bin/mak4i --version`
(Windows: `.\.venv\Scripts\mak4i.exe --version`). `mak4i 2.0.0rc1` means
`v2.0.0-rc.1`, and `mak4i 0.1.0rc4` means `v0.1.0-rc.4`. The first two
releases print `0.1.0` or don't know `--version` at all. Every case
upgrades the same way.

### Step 3: Stop MAK4I and close your AI apps

Nothing may change MAK4I's data while you copy it.
- Stop the MAK4I server: press Ctrl+C in its window.
- Quit Claude Code in every window (type `/exit`, or close the window),
  and close any other app that uses MAK4I on this computer.

### Step 4: Make a backup

macOS and Linux:

```bash
cp -R .mak4i ~/mak4i-backup-$(date +%Y-%m-%d-%H%M)
ls ~/mak4i-backup-*
```

Windows:

```powershell
Copy-Item -Recurse .\.mak4i "$HOME\mak4i-backup-$(Get-Date -Format yyyy-MM-dd-HHmm)"
Get-ChildItem "$HOME\mak4i-backup-*"
```

**You should see** `artifacts`, `config.json`, `control-plane.db` and
`credentials.json`: the contents of the new backup folder, named like
`mak4i-backup-2026-10-17-1430` in your home folder. (If you have several
backups, each folder's name appears above its contents.) The backup
contains your private access key: keep it private.

### Step 5: Download the new version

Replace `v2.1.0-rc.1` with the new version number if it's different. The
commands are the same on every system:

```bash
git fetch --depth 1 origin tag v2.1.0-rc.1
git checkout v2.1.0-rc.1
```

**You should see** `HEAD is now at …`. A note about "detached HEAD" is
normal.

### Step 6: Install it

```bash
uv sync --no-editable
```

**You should see** lines like `Uninstalled 8 packages` and
`Installed 7 packages`.

### Step 7: Start MAK4I and check

macOS and Linux:

```bash
.venv/bin/mak4i --version
```

Windows:

```powershell
.\.venv\Scripts\mak4i.exe --version
```

**You should see** the new version, for example `mak4i 2.1.0rc1`. Then
start the MAK4I server (3.1, or 4.2 Step 2 if you use claude.ai or
ChatGPT), open your AI app and ask: **"Use MAK4I to show who I am and list
my projects."** You should see the same organization and projects as
before.

MAK4I updates its database by itself the first time the new version runs.
There's nothing else to do.

**If you connected Claude Code with an older command** that only worked in
one folder (`claude mcp add mak4i -- mak4i serve`), connect it again with
[3.2](#32-connect-claude-code).

### What's new in v2.1.0-rc.1

- AI apps can ask what changed in a project since they last looked
  (`mak4i_changes`), and `mak4i_whoami` reports protocol version `0.3`.
- An owner can limit what each AI app may do on a project (client
  policies). Nothing changes until you set one: see
  [ADMIN_CLI.md](ADMIN_CLI.md#client-policies-client-scoped-access-mak-0006-57).
- MAK4I runs on Windows, and refuses to start a second copy on the same
  installation.

Full list: [`CHANGELOG.md`](../CHANGELOG.md).

### Undo the upgrade

Use this only if the new version doesn't work for you. Anything you saved
in MAK4I after the upgrade is lost.

1. Stop MAK4I and close your AI apps (Step 3).
2. Go to the MAK4I folder (Step 1).
3. Put the current data aside and restore your backup. Replace
   `mak4i-backup-<date>` with your backup's folder name:

   macOS and Linux:

   ```bash
   mv .mak4i ~/mak4i-after-upgrade-$(date +%Y-%m-%d-%H%M)
   cp -R ~/mak4i-backup-<date> .mak4i
   ```

   Windows:

   ```powershell
   Move-Item .\.mak4i "$HOME\mak4i-after-upgrade-$(Get-Date -Format yyyy-MM-dd-HHmm)"
   Copy-Item -Recurse "$HOME\mak4i-backup-<date>" .\.mak4i
   ```

4. Go back to what you wrote down in Step 2 (replace `<old-version>`, for
   example with `v2.0.0-rc.1`), and install it. The commands are the same
   on every system:

   ```bash
   git fetch --depth 1 origin tag <old-version>
   git checkout <old-version>
   uv sync --no-editable
   ```

   If what you wrote down isn't a version (for example `1a2b3c4`, or
   `v2.0.0-rc.1-12-g1a2b3c4`), leave out the `git fetch` line and use
   `git checkout <what you wrote down>`; for the longer form, use the part
   after `-g` (`1a2b3c4`).

5. Check it as in Step 7. You should see your old version.

A version from before v2.0.0-rc.1 can't read what v2.0.0-rc.1 or later
saved, which is why going back always restores the backup.

## 8. Back up and restore

**Back up** whenever you like, and always before an upgrade. Stop MAK4I
and close your AI apps first, so the copy is complete and consistent:
[7. Upgrade → Step 3](#step-3-stop-mak4i-and-close-your-ai-apps) and
[Step 4](#step-4-make-a-backup).

**Restore** a backup:
[7. Upgrade → Undo the upgrade](#undo-the-upgrade), steps 1 to 3. Then
start MAK4I again.

A backup contains your private access key (in `credentials.json`). Keep it
private.

## 9. Fix common problems

| What you see | What to do |
|---|---|
| `command not found: mak4i`, or `mak4i` isn't recognized | Go to the MAK4I folder, then use `.venv/bin/mak4i` (Windows: `.\.venv\Scripts\mak4i.exe`) instead of `mak4i`. |
| `command not found: uv` right after installing uv | Close the terminal and open a new one. |
| `fatal: not a git repository` | You're not in the MAK4I folder. Run `cd ~/mak4i-reference` (Windows: `Set-Location ~\mak4i-reference`). |
| `uv sync` reports files in use (Windows) | Stop MAK4I, quit every AI app that uses it and close other terminal windows, then run it again. |
| "MAK4I has not been initialized locally." | You're not in the MAK4I folder, or you haven't done [2. Install, Step 5](#step-5-create-your-organization-and-first-project) yet. |
| `claude mcp get mak4i` shows `✘ Failed to connect` | Start the MAK4I server (3.1) and try again. If it still fails, remove the connection with `claude mcp remove -s user mak4i` and redo [3.2](#32-connect-claude-code). |
| Claude Code finds MAK4I in one folder but not in another | MAK4I was connected with an older command that works in one folder only. Redo [3.2](#32-connect-claude-code). |
| `ERROR: MAK4I could not start. 127.0.0.1:9090 is already in use.` | MAK4I (or another program) is already running on that port. Use the MAK4I that's already running, or stop it first. |
| `MAK4I could not start: another MAK4I server is already running for this installation` | Only one copy of MAK4I can run. Use the one that's running, or stop it first: press Ctrl+C in the MAK4I server window, or quit the Claude Code window that started MAK4I (3.4). |
| A second Claude Code window shows MAK4I as failed to connect | With the alternative in 3.4, only one Claude Code window can use MAK4I at a time. Use the MAK4I server (3.1, 3.2) for several windows. |
| claude.ai or ChatGPT can't connect | Check the address ends in `/mcp`, the MAK4I server and your tunnel are running, and your tunnel meets [4.1](#41-what-your-tunnel-or-proxy-must-do). |
| `421 Invalid Host header` through a tunnel | Your tunnel doesn't rewrite the Host header ([4.1](#41-what-your-tunnel-or-proxy-must-do), item 2). |
| claude.ai or ChatGPT worked, then stopped after the tunnel restarted | The address changed: [4.3](#43-if-your-public-address-changes). |
| `error: OAuth is not enabled for this installation` | Add the two settings to the command, as in [4.2](#42-steps). |
| "Local credential file missing" | See [16. Credential recovery](#16-credential-recovery). |
| `access denied … on MAK4I connection '…'` | You have no permission (or only `read`) on that project here. Don't write the same thing to another MAK4I connection unless you choose it deliberately. |

**Developer setups ([Part 2](#part-2-advanced)):**

| What you see | What to do |
|---|---|
| `curl: (7) Failed to connect` | The server isn't running, or the port is wrong: check the banner's `Port:` line. |
| `401 {"error":"unauthorized"}` | Missing, wrong, expired or revoked credential. See [16. Credential recovery](#16-credential-recovery). |
| A working credential suddenly returns `401` after changing directories | `mak4i serve` uses the `.mak4i/` in its current directory. Two directories mean two separate environments with different credentials (their banners can look identical). |
| `MAK4I could not start: refusing plain HTTP on non-loopback address` | See [12](#12-network-access-without-host-rewriting): set `MAK4I_TLS_TERMINATED_UPSTREAM=1` only when HTTPS really terminates in front of MAK4I. |

## 10. Uninstall

Pick what you need. Stop MAK4I and close your AI apps first. (The Windows
commands in this section aren't run in MAK4I's automatic Windows checks.)

| Operation | Deletes | Keeps |
|---|---|---|
| [Disconnect your AI apps](#101-disconnect-your-ai-apps) | the app connections only | all MAK4I data |
| [Reinstall MAK4I's software](#102-reinstall-mak4is-software) | the installed software (`.venv/`) only | all data and connections |
| [Delete your MAK4I data](#103-delete-your-mak4i-data) | `.mak4i/`: all your MAK4I data | the software |
| [Remove everything](#104-remove-everything) | the whole `mak4i-reference` folder, including your data | backups you made outside it |

### 10.1 Disconnect your AI apps

Claude Code:

```bash
claude mcp remove -s user mak4i
```

Other apps (use the name you registered):

```bash
codex mcp remove mak4i                                # Codex CLI
grok mcp remove mak4i                                 # Grok CLI (add -s <scope> for one scope)
npx -y @google/gemini-cli mcp remove -s user mak4i    # Gemini CLI, if added with -s user
```

Gemini CLI removes from the project scope unless you pass the scope you
added with. claude.ai and Cowork: remove the connector in
**Customize → Connectors**. ChatGPT: remove the connector in its settings.

### 10.2 Reinstall MAK4I's software

Keeps all your data. From the MAK4I folder:

macOS and Linux:

```bash
rm -rf .venv
uv sync --no-editable
```

Windows:

```powershell
Remove-Item -Recurse -Force .\.venv
uv sync --no-editable
```

### 10.3 Delete your MAK4I data

> **Permanently deletes** `.mak4i/`: your organization, projects, access
> keys, and everything saved in MAK4I with its history. Make a backup
> first ([8](#8-back-up-and-restore)) if there's any chance you'll need it.

From the MAK4I folder:

macOS and Linux:

```bash
rm -rf .mak4i
.venv/bin/mak4i init
```

Windows:

```powershell
Remove-Item -Recurse -Force .\.mak4i
.\.venv\Scripts\mak4i.exe init
```

`init` creates a new, empty organization and project with a new access
key. Connect Claude Code again with [3.2](#32-connect-claude-code) (it
saved the old key), and give the new key to any other app (3.3). With the
alternative in [3.4](#34-simpler-alternative-let-claude-code-start-mak4i),
nothing needs to change. claude.ai and ChatGPT need [4.2](#42-steps) from
Step 4.

### 10.4 Remove everything

> **Permanently deletes** the whole `mak4i-reference` folder, including
> **your MAK4I data**. Make a backup first to keep the data, and disconnect
> your AI apps first ([10.1](#101-disconnect-your-ai-apps)).

macOS and Linux:

```bash
cd ~
rm -rf mak4i-reference
```

Windows (close every window using the folder first):

```powershell
Set-Location ~
Remove-Item -Recurse -Force .\mak4i-reference
```

---

# Part 2: Advanced

For developers and special setups. Part 1 doesn't need any of it.

**The developer environment.** Part 1 runs MAK4I as `.venv/bin/mak4i`. To
type `mak4i` directly, and to run the tests, install the development extras
and activate the environment in each new terminal:

```bash
uv sync --extra dev --no-editable
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
uv run --no-sync pytest            # optional: all green; the GCS integration tests skip by design
```

**Why `--no-editable`.** `uv`'s editable install marks its generated
`.pth` file hidden on macOS, and Python silently skips hidden `.pth`
files, which intermittently breaks `import mak4i`. A non-editable install
has no `.pth` file. Two consequences:

1. A bare `uv run <cmd>` re-syncs to an editable install. Activate the
   environment and call `mak4i` directly, or keep passing `--no-editable`.
2. After adding a source file, `git add` it and run
   `uv sync --extra dev --no-editable --reinstall-package mak4i`
   (the build only includes version-controlled files).

The commands below assume the environment is active (`mak4i`); without it,
use `.venv/bin/mak4i` as in Part 1.

## 11. Server details

All modes serve the same `.mak4i/` environment: the same organization,
project, credential and stored context. `mak4i serve` uses the `.mak4i/`
in its current directory (or `$MAK4I_HOME`), so run it from the repository
directory.

**One server process per installation.** Only one `mak4i serve` (stdio or
HTTP) runs against a `.mak4i/` at a time ([DEPLOYMENT.md → Replicas](DEPLOYMENT.md#replicas)).
Every `serve` start-up finishes work a previous process left pending, so
before it reads or changes anything it takes an OS file lock
(`.mak4i/artifacts/.mak4i-server.lock`). A second server exits with code 2
and `MAK4I could not start: another MAK4I server is already running for
this installation`. The OS releases the lock when the server exits, even
after a crash. CLI commands such as `mak4i create` don't take it and may
run alongside the one server.

| You're connecting | Mode | Command |
|---|---|---|
| a local MCP client that launches MAK4I itself | [stdio](#111-stdio) | `mak4i serve` |
| local MCP clients, and cloud clients through a Host-rewriting tunnel | [local HTTP](#112-local-http) | `mak4i serve --transport http` |
| clients through a TLS proxy that can't rewrite the Host header | [network HTTP](#12-network-access-without-host-rewriting) | `MAK4I_TLS_TERMINATED_UPSTREAM=1 mak4i serve --transport http --host 0.0.0.0` |

### 11.1 stdio

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
port and no URL: the client starts `mak4i serve` itself
([3.4](#34-simpler-alternative-let-claude-code-start-mak4i)). Running
`serve` before `init` fails clearly; it never initializes on its own.
`.mak4i/` is authoritative: a stale `MAK4I_TOKEN` or
`MAK4I_CONTROL_PLANE_DB` in your shell is ignored, with a one-line notice.

### 11.2 Local HTTP

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
**loopback only**, so only this machine can connect. Every `POST /mcp`
is answered as a `text/event-stream` (server-sent events).

On loopback, MAK4I's DNS-rebinding protection accepts only `Host` headers
naming `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`, and
answers anything else with `421 Invalid Host header`. The list isn't
configurable. A tunnel or proxy in front of a loopback server must
therefore rewrite `Host` ([4.1](#41-what-your-tunnel-or-proxy-must-do)).

| To | Do |
|---|---|
| use another port once | `mak4i serve --transport http --port 9100` (the saved port is unchanged) |
| override from the environment | `MAK4I_PORT=9200 mak4i serve --transport http` |
| change it permanently | edit `"http_port"` in `.mak4i/config.json` |

Precedence: `--port` → `MAK4I_PORT` → the saved port → 9090. `mak4i serve`
ignores the generic `PORT` variable. If the port is in use, `serve` stops
with a clear message instead of silently picking another port (clients
are configured with a fixed URL).

**Connecting a client by URL:** endpoint `http://127.0.0.1:<port>/mcp`
with the header `Authorization: Bearer <token>`. The token is in
`.mak4i/credentials.json`. Prefer clients that read it from a file or
environment variable, so it doesn't end up in your shell history. Each
client's exact steps: [`DEMO.md` → Connecting a client](DEMO.md#connecting-a-client).

## 12. Network access without Host rewriting

If the HTTPS proxy or tunnel in front of MAK4I can't rewrite the `Host`
header, MAK4I must listen on a non-loopback address, where it doesn't
check `Host`:

```bash
MAK4I_TLS_TERMINATED_UPSTREAM=1 mak4i serve --transport http --host 0.0.0.0
```

> **Only with real HTTPS in front.** MAK4I serves plain HTTP. On a
> non-loopback address it **refuses to start** unless
> `MAK4I_TLS_TERMINATED_UPSTREAM=1` declares that a TLS-terminating proxy
> or tunnel sits in front of it (MAK-0008 §1.2):
>
> ```
> MAK4I could not start: refusing plain HTTP on non-loopback address '0.0.0.0' (MAK-0008 §1.2). …
> ```
>
> Set it only when that's true. It doesn't make plain HTTP safe:
> - `0.0.0.0` listens on **every network interface**. Block the port with
>   a host firewall so that only the proxy or tunnel connector can reach
>   it, and never give clients `http://<your-ip>:<port>`. Your credential
>   travels in every request.
> - DNS-rebinding protection is off on non-loopback addresses; credential
>   or OAuth authentication is the boundary.
> - Prefer binding to the one interface the proxy uses over `0.0.0.0`.

The proxy must still stream responses without buffering and allow
long-lived requests ([4.1](#41-what-your-tunnel-or-proxy-must-do),
[DEPLOYMENT.md → Reverse proxy requirements](DEPLOYMENT.md#reverse-proxy-requirements)).
With OAuth, also set `MAK4I_OAUTH_ENABLED=1` and `MAK4I_PUBLIC_ENDPOINT`
as in [4.2](#42-steps).

## 13. OAuth sign-in for apps on your computer

By default local clients use the credential from `.mak4i/credentials.json`.
A client on this machine that supports MCP OAuth, such as Claude Code, can
sign in with a one-time code instead, so no token is copied into its
configuration. (For claude.ai and ChatGPT, see
[4](#4-use-mak4i-from-claudeai-or-chatgpt).)

**OAuth settings aren't saved in `.mak4i/`.** Every command that uses
OAuth needs `MAK4I_OAUTH_ENABLED=1` and the same `MAK4I_PUBLIC_ENDPOINT`:
the server and every `mak4i oauth …` command, in every terminal. Without
them, `mak4i oauth` commands fail with `OAuth is not enabled for this
installation`. Set them once per terminal:

```bash
export MAK4I_OAUTH_ENABLED=1
export MAK4I_PUBLIC_ENDPOINT=http://127.0.0.1:9090/mcp    # the exact local URL, including the port
```

1. Start the server (in a terminal with the settings above):

   ```bash
   mak4i serve --transport http
   ```

   Check it: `curl -s http://127.0.0.1:9090/.well-known/oauth-protected-resource/mcp`
   returns JSON naming your `MAK4I_PUBLIC_ENDPOINT`.
2. Add the server by URL, without a header, then authenticate:

   ```bash
   claude mcp add --transport http mak4i-local http://127.0.0.1:9090/mcp
   ```

   In Claude Code run `/mcp` → `mak4i-local` → **Authenticate**. A browser
   opens the MAK4I sign-in page.
3. Get a sign-in code in another terminal (with the same two settings; the
   local credential authenticates you, so no `--actor` is needed) and
   enter it:

   ```bash
   mak4i oauth sign-in-code
   ```

   A code works once and for 10 minutes. Approve the consent page.

After that, new conversations reuse the connection without a new code.
Access tokens last 1 hour and refresh silently. Sign in again after 7 days
without use, after 30 days in total, or after `mak4i oauth authorization
revoke`.

Notes:
- Plain `http` is accepted for `MAK4I_PUBLIC_ENDPOINT` only on loopback;
  anything else must be `https`.
- Authorizations are bound to `MAK4I_PUBLIC_ENDPOINT`. Changing it (and
  restarting the server, which reads it once at start-up) makes existing
  OAuth sign-ins fail; clients sign in again. Credential-header clients
  are unaffected.
- Client policies (`mak4i client-policy`, [ADMIN_CLI.md](ADMIN_CLI.md#client-policies-client-scoped-access-mak-0006-57))
  work the same locally. Claude Code's client ID is
  `https://claude.ai/oauth/claude-code-client-metadata`.

## 14. Manual and advanced setup

### 14.1 What `mak4i init` creates

`mak4i init` asks for an organization name, your display name, a first
project name and the Local HTTP port (press Enter for 9090), or takes them
as flags (needed when it isn't run in an interactive terminal):

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

Set `MAK4I_HOME` to keep `.mak4i/` somewhere else. The database and
artifact paths are saved relative to the directory `init` ran in, so run
`mak4i serve` from that directory.

The grant is `read,write`. To resolve conflicts with
`mak4i_resolve_conflict`, add `resolve`:
`mak4i grant create --actor <owner_principal_id> --principal-id <owner_principal_id> --project-id <project_id> --permissions read,write,resolve`
(the IDs are in `.mak4i/config.json`).

**Running `init` again** doesn't overwrite anything: it prints the
existing environment (including your owner principal ID) and exits.

**`mak4i init --force` doesn't reuse, repair or reset your environment.**
It runs a full first-time setup again: a **new** organization, owner
principal, project, grant and credential in the same local database, and
it rewrites `.mak4i/config.json` and `credentials.json` to point at them.
Nothing is deleted, but the new identity can't see your existing projects,
so their context disappears from view. To recover a credential, see
[16. Credential recovery](#16-credential-recovery); to start over, see
[10.3 Delete your MAK4I data](#103-delete-your-mak4i-data).

**The Local HTTP port** is saved as `http_port` in `.mak4i/config.json`,
and every later `mak4i serve --transport http` uses it. It has no effect on
stdio. A config from before this setting existed uses 9090.

### 14.2 Without `mak4i init`

`mak4i init` is a convenience wrapper over the same control-plane
commands, which remain the right tool for several organizations, extra
principals, scripting or understanding the model. Each command's output
feeds the next:

```bash
# 1. Organization and its owner (instance-operator command, no --actor).
#    Prints {"organization": {...}, "owner": {...}}.
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

**Owner commands.** They take `--actor <owner_principal_id>` (or, without
it, act as the principal of the local credential) and are scoped to the
owner's own organization:

| Command | Purpose |
|---|---|
| `mak4i principal create --organization-id <id> --type human\|service\|agent --display-name <name> [--role member\|owner]` | add a principal |
| `mak4i grant revoke --principal-id <id> --project-id <id>` | remove a grant |
| `mak4i credential revoke --credential-id <id>` | revoke a credential immediately |
| `mak4i project list --organization-id <id>` / `project show --project-id <id>` | projects |
| `mak4i principal list --organization-id <id>` / `principal show --principal-id <id>` | principals |
| `mak4i grant list --principal-id <id>` | a principal's grants |
| `mak4i credential list --principal-id <id>` | a principal's credentials (never the token or its hash) |

**Instance-operator commands.** They take **no** `--actor`: whoever can run
the CLI against this database is the instance operator.

| Command | Purpose |
|---|---|
| `mak4i org create` / `org list` / `org show --organization-id <id>` | organizations |
| `mak4i org suspend` / `org reactivate` | suspend or reactivate an organization |
| `mak4i admin inventory`, `mak4i admin revoke-oauth --all` | instance inventory; revoke every OAuth authorization |

Full reference: [ADMIN_CLI.md](ADMIN_CLI.md).

`credential issue` also prints ready-to-copy connect instructions
(`--no-connection-help` suppresses them).

**Operator artifact commands.** Without a running server or a credential,
using `--principal <id>` (a trusted, local-only mode, recorded with
`auth_method: "operator"` and never exposed over MCP):

```bash
mak4i doctor      --principal <id> --project <project_id>
mak4i create      --principal <id> --project <project_id> --artifact-id … --artifact-type … --title … --content …
mak4i supersede   --principal <id> --project <project_id> --old-id … --content … --reason …
mak4i search      --principal <id> --project <project_id> --tags …
mak4i get-current --principal <id> --project <project_id> --tags …
mak4i history     --principal <id> --project <project_id> --lineage-id …
```

After `mak4i init`, the IDs are in `.mak4i/config.json`.

## 15. Configuration reference

The one table of Local settings. `mak4i serve` sets the backend variables
from `.mak4i/`, so you normally set none of them.

| Variable | Meaning |
|---|---|
| `MAK4I_HOME` | where `.mak4i/` lives (default: `./.mak4i`) |
| `MAK4I_TRANSPORT` | `stdio` (default) or `http` / `streamable-http`; `--transport` wins |
| `MAK4I_HOST` | HTTP bind address (default `127.0.0.1`); `--host` wins. See [12](#12-network-access-without-host-rewriting) before using anything else. |
| `MAK4I_TLS_TERMINATED_UPSTREAM` | `1` only when a TLS-terminating proxy or tunnel sits in front of MAK4I. Without it, MAK4I refuses to serve HTTP on any non-loopback address (MAK-0008 §1.2). |
| `MAK4I_PORT` | HTTP port for one run; `--port` wins; otherwise the saved port, then 9090 |
| `MAK4I_OAUTH_ENABLED`, `MAK4I_PUBLIC_ENDPOINT` | turn on OAuth sign-in and name the public MCP URL ([4.2](#42-steps), [13](#13-oauth-sign-in-for-apps-on-your-computer)). Not saved in `.mak4i/`: set them for the server and every `mak4i oauth` command. Other `MAK4I_OAUTH_*` settings: [ENTERPRISE_SELF_HOSTED.md → Configuration reference](ENTERPRISE_SELF_HOSTED.md#12-configuration-reference). |
| `MAK4I_INSTANCE_NAME`, `MAK4I_ENVIRONMENT` | how this server names itself to AI clients (default `MAK4I local (<organization>)` / `local`) |
| `MAK4I_AUDIT_LOG` | where CLI commands write audit and security events: `stderr` (default; stdout stays machine-readable) or a file path. Security events can't be switched off (MAK-0006 §8.6). |
| `MAK4I_TOKEN` | the credential for `python -m mak4i.mcp_server` over stdio (manual setup); ignored by `mak4i serve` |
| `MAK4I_CONTROL_PLANE_DB`, `MAK4I_STORE`, `MAK4I_LOCAL_STORE_DIR`, `MAK4I_CONTROL_PLANE_CREATE_TABLES` | backends for the manual setup ([14.2](#142-without-mak4i-init)); ignored by `mak4i serve` |

## 16. Credential recovery

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

## 17. Health and readiness checks

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
