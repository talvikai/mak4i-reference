# MAK4I on your own computer (Local Setup)

> **Developer Preview.** This is a release candidate (`v2.1.0-rc.1`) for
> evaluation and feedback, not a production-supported release.

## What this guide covers

This guide shows you how to install MAK4I on your own computer, connect it
to an AI app on the same computer, check that it works, and keep it up to
date. Everything MAK4I stores stays on your computer. Nothing is shared
with anyone.

**Use the [Enterprise Self-Hosted guide](ENTERPRISE_SELF_HOSTED.md) instead if:**
- a team should share one MAK4I, or
- you want to use MAK4I from **claude.ai** or **ChatGPT**.

## How your AI app connects to MAK4I

Read this before you install anything. The AI app you use decides what you
do after the installation.

| Your AI app | Where the app runs | What you do | Where it's explained |
|---|---|---|---|
| **Claude Code** | on your computer | Nothing to keep running. Claude Code starts MAK4I by itself whenever it needs it. | [3. Connect your AI app](#3-connect-your-ai-app) |
| **An app that connects to an address (URL)** on your computer | on your computer | Keep MAK4I running in a terminal window while you use the app, with `mak4i serve --transport http`. The app connects to `http://127.0.0.1:9090/mcp`. | [10. Run MAK4I as a local server](#10-run-mak4i-as-a-local-server) |
| **claude.ai** or **ChatGPT** | in the provider's cloud | These apps can't reach your computer. Use the [Enterprise Self-Hosted guide](ENTERPRISE_SELF_HOSTED.md), which gives MAK4I a secure public address. | [Enterprise Self-Hosted](ENTERPRISE_SELF_HOSTED.md) |

Other apps (for example Gemini CLI) connect to the address in the second
row. Their exact steps are in
[DEMO.md → Connecting a client](DEMO.md#connecting-a-client).

## Installation at a glance

| Step | What happens | Time |
|---|---|---|
| [1. Before you start](#1-before-you-start) | Install two free tools, Git and uv, if you don't have them | about 10 minutes, once |
| [2. Install MAK4I](#2-install-mak4i-step-by-step) | Download MAK4I, install it, and create your organization and first project | about 5 minutes |
| [3. Connect your AI app](#3-connect-your-ai-app) | Make MAK4I available in Claude Code, in all your projects | 1 minute |
| [4. Check it works](#4-check-it-works) | Ask Claude Code to save and find something in MAK4I | 2 minutes |

You type commands into a terminal window. Every step shows exactly what to
copy and what you should see afterwards.

## Find your section

| I want to… | Go to |
|---|---|
| Install MAK4I for the first time | [1. Before you start](#1-before-you-start), then [2. Install MAK4I](#2-install-mak4i-step-by-step) |
| Connect Claude Code (or another AI app) | [3. Connect your AI app](#3-connect-your-ai-app) |
| Check that MAK4I works | [4. Check it works](#4-check-it-works) |
| Start or stop MAK4I | [5. Start and stop](#5-start-and-stop) |
| Upgrade to a new version | [6. Upgrade](#6-upgrade) |
| Make a backup, or restore one | [7. Back up and restore](#7-back-up-and-restore) |
| Fix a problem | [8. Fix common problems](#8-fix-common-problems) |
| Remove MAK4I | [9. Uninstall](#9-uninstall) |
| Developer options: local server, network access, OAuth sign-in, manual setup, settings | [Part 2: Advanced](#part-2-advanced) |

**Follow one section from start to finish.** You don't need to read the
other sections first.

### Contents

**Part 1: Everyday use**

1. [Before you start](#1-before-you-start)
2. [Install MAK4I, step by step](#2-install-mak4i-step-by-step)
3. [Connect your AI app](#3-connect-your-ai-app)
4. [Check it works](#4-check-it-works)
5. [Start and stop](#5-start-and-stop)
6. [Upgrade](#6-upgrade)
7. [Back up and restore](#7-back-up-and-restore)
8. [Fix common problems](#8-fix-common-problems)
9. [Uninstall](#9-uninstall)

**[Part 2: Advanced](#part-2-advanced)** (for developers)

10. [Run MAK4I as a local server](#10-run-mak4i-as-a-local-server)
11. [Network access and tunnels](#11-network-access-and-tunnels)
12. [OAuth sign-in on your computer](#12-oauth-sign-in-on-your-computer)
13. [Manual and advanced setup](#13-manual-and-advanced-setup)
14. [Configuration reference](#14-configuration-reference)
15. [Credential recovery](#15-credential-recovery)
16. [Health and readiness checks](#16-health-and-readiness-checks)

---

# Part 1: Everyday use

**Commands for your system.** Each step has a box for **macOS and Linux**
and a box for **Windows** (PowerShell). Copy the box for your system only.
The Windows commands are written for PowerShell but haven't been tested on
Windows yet.

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
- a computer with macOS or Linux. Windows 10 or 11 should work but isn't
  tested yet;
- an internet connection;
- about 300 MB of free disk space;
- [Claude Code](https://code.claude.com/docs/en/overview)
  installed and signed in, if you want to use MAK4I with Claude Code.

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

**You should see** a line like `git version 2.39.5`. Any version works.

If you see "command not found" (or a message that Git isn't recognized):
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

**You should see** a line like `uv 0.9.5`. Any version works.

If you see "command not found", install uv:

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

The first time, this downloads Python and MAK4I's libraries, which can take
a few minutes. On an older Mac with an Intel processor, one library is
built on your computer, which adds a few more minutes.

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
| Local HTTP port | just press Enter |

**You should see** a few lines starting with `{"event": "ADMIN"` (a log of
what was created; you can ignore them), six lines starting with `✓`, then
`MAK4I is ready.` with your organization, principal and project IDs, and
two connection options. **Ignore the `claude mcp add` command it prints**:
the one in section 3 works in all your projects.

MAK4I created a hidden folder named `.mak4i` inside `mak4i-reference`. It
holds all your MAK4I data, including your private access key. Don't delete
it, and don't share it.

Next: [3. Connect your AI app](#3-connect-your-ai-app).

## 3. Connect your AI app

### Claude Code

This makes MAK4I available in **all** your Claude Code projects. Claude
Code starts MAK4I by itself whenever it needs it, so there's nothing to
keep running.

Run this from the `mak4i-reference` folder.

macOS and Linux:

```bash
claude mcp add -s user mak4i -- sh -c "cd \"$PWD\" && exec .venv/bin/mak4i serve"
```

Windows:

```powershell
claude mcp add -s user mak4i -- cmd /c "cd /d `"$PWD`" && .venv\Scripts\mak4i.exe serve"
```

**You should see** `Added stdio MCP server mak4i … to user config`.
If it says a server named `mak4i` already exists, remove the old one with
`claude mcp remove mak4i`, then run the command above again.

Check the connection:

```bash
claude mcp get mak4i
```

**You should see:**

```
  Scope: User config (available in all your projects)
  Status: ✔ Connected
```

**Why this command.** It records where you installed MAK4I, so Claude Code
can start it from any project folder. If you move or rename the
`mak4i-reference` folder, run it again from the new location.

### Other AI apps

- **claude.ai or ChatGPT:** they can't reach your computer. Use the
  [Enterprise Self-Hosted guide](ENTERPRISE_SELF_HOSTED.md).
- **Apps that connect to an address (URL):** see
  [10. Run MAK4I as a local server](#10-run-mak4i-as-a-local-server), and
  each app's steps in [DEMO.md → Connecting a client](DEMO.md#connecting-a-client).

## 4. Check it works

1. Open Claude Code in any project folder.
2. Ask: **"Use MAK4I to show who I am and list my projects."**

   **You should see** your organization, the connection
   `MAK4I local (<your organization>)`, and your project with the
   permissions `read` and `write`. Claude Code may ask your permission to
   use the MAK4I tools the first time; allow it.
3. Ask: **"Save this in my MAK4I project: our app uses PostgreSQL as its
   database."**
4. Start a **new** conversation and ask: **"According to MAK4I, which
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
| `mak4i_resolve_conflict` | resolve a conflict the user has decided (needs the `resolve` permission, which `mak4i init` doesn't give: see [13](#13-manual-and-advanced-setup)) |
| `mak4i_changes` | what changed in a project after a cursor (`mak4i changes` on the CLI) |

**Several MAK4I connections.** A local MAK4I names itself
`MAK4I local (<organization>)`, environment `local`. AI apps show this
name in `mak4i_whoami`, project lists and access denials. When an app is
connected to more than one MAK4I, check where it proposes to write: one
MAK4I can't control what an app does on another.

## 5. Start and stop

**With Claude Code** there is nothing to start or stop. Claude Code starts
MAK4I when it needs it, and MAK4I stops when you quit Claude Code. After
restarting your computer, nothing needs to be done.

**With an app that connects to an address**, MAK4I runs only while
`mak4i serve --transport http` is running in a terminal window. Stop it
with Ctrl+C, and start it again with the same command
([10. Run MAK4I as a local server](#10-run-mak4i-as-a-local-server)).

Stopping MAK4I never deletes anything, and your access key stays valid.

## 6. Upgrade

Upgrading keeps your data and your Claude Code connection. It takes about
5 minutes. You need the version number of the new release, for example
`v2.1.0-rc.1`; the current one is shown at the top of
[`README.md`](../README.md).

### Step 1: Go to the MAK4I folder

macOS and Linux:

```bash
cd ~/mak4i-reference
```

Windows:

```powershell
Set-Location ~\mak4i-reference
```

### Step 2: Write down your current version

```bash
git describe --tags
```

**You should see** your current version, for example `v2.0.0-rc.1`. Write
it down: you need it if you ever want to go back
([undo the upgrade](#undo-the-upgrade)).

### Step 3: Make a backup

macOS and Linux:

```bash
cp -R .mak4i ~/mak4i-backup-$(date +%Y-%m-%d-%H%M)
ls ~ | grep mak4i-backup
```

Windows:

```powershell
Copy-Item -Recurse .\.mak4i "$HOME\mak4i-backup-$(Get-Date -Format yyyy-MM-dd-HHmm)"
Get-ChildItem ~ -Filter mak4i-backup*
```

**You should see** a folder named like `mak4i-backup-2026-10-17-1430` in
your home folder. It contains your private access key: keep it private.

### Step 4: Close your AI apps

Quit Claude Code in every window (type `/exit`, or close the window). If
`mak4i serve` is running in a terminal window, stop it with Ctrl+C.

### Step 5: Download the new version

Replace `v2.1.0-rc.1` with the new version number if it's different.

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
`Installed 7 packages`. This can take a few minutes on an older Intel Mac.

### Step 7: Check the new version

macOS and Linux:

```bash
.venv/bin/mak4i --version
```

Windows:

```powershell
.\.venv\Scripts\mak4i.exe --version
```

**You should see** the new version, for example `mak4i 2.1.0rc1`. Then
open Claude Code and ask: **"Use MAK4I to show who I am and list my
projects."** You should see the same organization and projects as before.

MAK4I updates its database by itself the first time the new version runs.
There's nothing else to do.

**If you connected Claude Code with an older command**
(`claude mcp add mak4i -- mak4i serve`), redo
[3. Connect your AI app](#3-connect-your-ai-app) once, so MAK4I works in
all your projects.

### What's new in v2.1.0-rc.1

- AI apps can ask what changed in a project since they last looked
  (`mak4i_changes`), and `mak4i_whoami` reports protocol version `0.3`.
- An owner can limit what each AI app may do on a project (client
  policies). Nothing changes until you set one: see
  [ADMIN_CLI.md](ADMIN_CLI.md#client-policies-client-scoped-access-mak-0006-57).

Full list: [`CHANGELOG.md`](../CHANGELOG.md).

### Undo the upgrade

Use this only if the new version doesn't work for you. Anything you saved
in MAK4I after the upgrade is lost.

1. Quit your AI apps (Step 4).
2. Go to the MAK4I folder (Step 1).
3. Put the current data aside and restore your backup. Replace
   `mak4i-backup-<date>` with your backup's folder name.

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

4. Go back to the version you wrote down in Step 2 (replace
   `<old-version>`, for example with `v2.0.0-rc.1`), and install it:

   ```bash
   git fetch --depth 1 origin tag <old-version>
   git checkout <old-version>
   uv sync --no-editable
   ```

5. Check it as in Step 7. You should see your old version.

A version from before v2.0.0-rc.1 can't read what v2.0.0-rc.1 or later
saved, which is why going back always restores the backup.

## 7. Back up and restore

**Back up** whenever you like, and always before an upgrade:
[6. Upgrade → Step 3](#step-3-make-a-backup) shows the commands. Quit your
AI apps first, so nothing is being written while you copy.

**Restore** a backup:
[6. Upgrade → Undo the upgrade](#undo-the-upgrade), steps 1 to 3.

A backup contains your private access key (in `credentials.json`). Keep it
private.

## 8. Fix common problems

| What you see | What to do |
|---|---|
| `command not found: mak4i`, or `mak4i` isn't recognized | Go to the MAK4I folder, then use `.venv/bin/mak4i` (Windows: `.\.venv\Scripts\mak4i.exe`) instead of `mak4i`. |
| `command not found: uv` right after installing uv | Close the terminal and open a new one. |
| `fatal: not a git repository` | You're not in the MAK4I folder. Run `cd ~/mak4i-reference` (Windows: `Set-Location ~\mak4i-reference`). |
| "MAK4I has not been initialized locally." | You're not in the MAK4I folder, or you haven't done [2. Install, Step 5](#step-5-create-your-organization-and-first-project) yet. |
| `claude mcp get mak4i` shows `✘ Failed to connect` | Go to the MAK4I folder and check that `.venv/bin/mak4i --version` works. Then remove the connection with `claude mcp remove mak4i` and redo [3. Connect your AI app](#3-connect-your-ai-app). |
| Claude Code finds MAK4I in one folder but not in another | MAK4I was connected with an older command that works in one folder only. Redo [3. Connect your AI app](#3-connect-your-ai-app). |
| claude.ai or ChatGPT can't connect | They can't reach your computer. Use the [Enterprise Self-Hosted guide](ENTERPRISE_SELF_HOSTED.md). |
| "Local credential file missing" | See [15. Credential recovery](#15-credential-recovery). |
| `access denied … on MAK4I connection '…'` | You have no permission (or only `read`) on that project here. Don't write the same thing to another MAK4I connection unless you choose it deliberately. |
| `uv sync` reports files in use (Windows) | Quit every AI app and close other terminal windows, then run it again. |

**Developer setups ([Part 2](#part-2-advanced)):**

| What you see | What to do |
|---|---|
| `curl: (7) Failed to connect` | The server isn't running, or the port is wrong: check the banner's `Port:` line. |
| `401 {"error":"unauthorized"}` | Missing, wrong, expired or revoked credential. See [15. Credential recovery](#15-credential-recovery). |
| A working credential suddenly returns `401` after changing directories | `mak4i serve` uses the `.mak4i/` in its current directory. Two directories mean two separate environments with different credentials (their banners can look identical). |
| `421 Misdirected Request` while tunneling | The server is on loopback; restart with `--host 0.0.0.0` after reading its [security warning](#11-network-access-and-tunnels). |
| A cloud client asks how the server "signs in" | That client uses OAuth. MAK4I's OAuth sign-in needs a public HTTPS endpoint the client can reach (see [Enterprise → OAuth sign-in](ENTERPRISE_SELF_HOSTED.md#71-oauth-sign-in)); on a local install, use the client's custom-header setup with `Authorization: Bearer <credential>` instead. Clients on this machine can use local OAuth ([12](#12-oauth-sign-in-on-your-computer)). |
| `ERROR: MAK4I could not start. 127.0.0.1:<port> is already in use.` | Stop the other process, or use `--port`. |

## 9. Uninstall

Pick what you need. Quit your AI apps first.

| Operation | Deletes | Keeps |
|---|---|---|
| [Disconnect Claude Code](#91-disconnect-claude-code) | the Claude Code connection only | all MAK4I data |
| [Reinstall MAK4I's software](#92-reinstall-mak4is-software) | the installed software (`.venv/`) only | all data and connections |
| [Delete your MAK4I data](#93-delete-your-mak4i-data) | `.mak4i/`: all your MAK4I data | the software |
| [Remove everything](#94-remove-everything) | the whole `mak4i-reference` folder, including your data | backups you made outside it |

**Windows: close everything first.** Windows won't delete files in use:
stop `mak4i serve`, quit every AI app that uses MAK4I, close other
terminals using MAK4I, and move out of the folder before deleting it.

### 9.1 Disconnect Claude Code

```bash
claude mcp remove -s user mak4i
```

Other apps (use the name you registered; the commands are the same in
every shell):

```bash
codex mcp remove mak4i                                # Codex CLI
grok mcp remove mak4i                                 # Grok CLI (add -s <scope> for one scope)
npx -y @google/gemini-cli mcp remove -s user mak4i    # Gemini CLI, if added with -s user
```

Gemini CLI removes from the project scope unless you pass the scope you
added with. Claude.ai and Cowork: remove the connector in
**Customize → Connectors**.

### 9.2 Reinstall MAK4I's software

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

### 9.3 Delete your MAK4I data

> **Permanently deletes** `.mak4i/`: your organization, projects, access
> keys, and everything saved in MAK4I with its history. Make a backup
> first ([7](#7-back-up-and-restore)) if there's any chance you'll need it.

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

`init` creates a new, empty organization and project. AI apps connected as
in section 3 keep working, because they read the new access key from
`.mak4i/`. Apps you gave the old key to (section 10) need the new one.

### 9.4 Remove everything

> **Permanently deletes** the whole `mak4i-reference` folder, including
> **your MAK4I data**. Make a backup first to keep the data, and disconnect
> your AI apps first ([9.1](#91-disconnect-claude-code)).

macOS and Linux:

```bash
cd ~
rm -rf mak4i-reference
```

Windows:

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

## 10. Run MAK4I as a local server

All modes serve the same `.mak4i/` environment: the same organization,
project, credential and stored context. Stop one (Ctrl+C) and start
another at any time; nothing needs re-initializing.

| You're connecting | Mode | Command |
|---|---|---|
| a local MCP client that launches MAK4I itself | [stdio](#101-stdio) | `mak4i serve` |
| local MCP clients that connect by URL | [local HTTP](#102-local-http) | `mak4i serve --transport http` |
| clients on other machines, or a cloud AI client through a tunnel | [network HTTP](#11-network-access-and-tunnels) | `mak4i serve --transport http --host 0.0.0.0` |

`mak4i serve` uses the `.mak4i/` in its current directory (or
`$MAK4I_HOME`), so run it from the repository directory.

### 10.1 stdio

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
([3](#3-connect-your-ai-app)). Running `serve` before `init` fails clearly;
it never initializes on its own. `.mak4i/` is authoritative: a stale
`MAK4I_TOKEN` or `MAK4I_CONTROL_PLANE_DB` in your shell is ignored, with a
one-line notice.

### 10.2 Local HTTP

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
**loopback only**, so only this machine can connect.

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

## 11. Network access and tunnels

A cloud-hosted AI client (Claude.ai, Cowork, Gemini and similar) runs on
someone else's infrastructure and can't reach `127.0.0.1` on your machine.
To reach a local server from elsewhere, it must listen beyond loopback:

```bash
mak4i serve --transport http --host 0.0.0.0
```

> **Security warning.** `--host 0.0.0.0` exposes MAK4I on **every network
> interface** of your machine over **plain, unencrypted HTTP**, and your
> bearer credential travels in every request. Binding to `0.0.0.0`
> provides no security by itself; credential authentication still applies,
> but the traffic isn't protected.
> - Use it only on a trusted network, or with a host firewall that blocks
>   the port from other machines.
> - Give remote clients only an HTTPS URL (a tunnel or TLS proxy), never
>   `http://<your-ip>:<port>`.
> - Stop the server, or restart it without `--host`, when you're done.
> - For anything beyond a short test, use
>   [Enterprise Self-Hosted](ENTERPRISE_SELF_HOSTED.md), which puts MAK4I
>   behind HTTPS.

**HTTPS tunnel for a cloud client (development and testing only).** For
example, Cloudflare's account-less Quick Tunnel, alongside the server
above:

```bash
cloudflared tunnel --url http://127.0.0.1:9090    # your Local HTTP port
```

It prints a temporary `https://….trycloudflare.com` hostname; configure
your cloud client with `https://<hostname>/mcp` and the bearer header.
Details that matter:

1. **`--host 0.0.0.0` is required while tunneling.** On loopback, MAK4I
   keeps DNS-rebinding protection on, which accepts only `Host` headers
   naming `127.0.0.1`/`localhost`. A tunnel forwards its own public
   hostname, which then fails with `421 Misdirected Request`.
2. **The tunnel's `--url` is `http://127.0.0.1:<port>`, with no path.** It
   is where the tunnel connects, not what MAK4I listens on; a path there
   gets appended a second time.
3. **The Quick Tunnel hostname changes on every tunnel restart.** Update
   the client's URL; nothing changes on the MAK4I side, and the credential
   keeps working.

Tunnels aren't a MAK4I dependency or a production architecture. For a
stable address, use a named tunnel, your own TLS reverse proxy, or
Enterprise Self-Hosted.

## 12. OAuth sign-in on your computer

By default local clients use the credential from `.mak4i/credentials.json`.
A client on this machine that supports MCP OAuth, such as Claude Code, can
sign in with a one-time code instead, so no token is copied into its
configuration.

1. Start the server with OAuth on. `MAK4I_PUBLIC_ENDPOINT` must be the exact
   local URL, including the port:

   ```bash
   MAK4I_OAUTH_ENABLED=1 MAK4I_PUBLIC_ENDPOINT=http://127.0.0.1:9090/mcp \
     mak4i serve --transport http
   ```

   Check it: `curl -s http://127.0.0.1:9090/.well-known/oauth-protected-resource/mcp`
   returns JSON.
2. Add the server by URL, without a header, then authenticate:

   ```bash
   claude mcp add --transport http mak4i-local http://127.0.0.1:9090/mcp
   ```

   In Claude Code run `/mcp` → `mak4i-local` → **Authenticate**. A browser
   opens the MAK4I sign-in page.
3. Get a sign-in code in another terminal (the local credential
   authenticates you, so no `--actor` is needed) and enter it:

   ```bash
   mak4i oauth sign-in-code
   ```

   A code works once and for 10 minutes. Approve the consent page.

After that, new conversations reuse the connection without a new code.
Access tokens last 1 hour and refresh silently. Sign in again after 7 days
without use, after 30 days in total, or after `mak4i oauth authorization
revoke`.

Limits:
- Hosted clients (claude.ai, ChatGPT) run in the cloud and can't reach
  `127.0.0.1`. Use them with an Enterprise install on a public HTTPS name
  ([Enterprise → OAuth sign-in](ENTERPRISE_SELF_HOSTED.md#71-oauth-sign-in)),
  or with a tunnel ([11](#11-network-access-and-tunnels)) and
  `MAK4I_PUBLIC_ENDPOINT` set to the tunnel's `https://…/mcp`.
- Plain `http` is accepted only on loopback.
- Client policies (`mak4i client-policy`, [ADMIN_CLI.md](ADMIN_CLI.md#client-policies-client-scoped-access-mak-0006-57))
  work the same locally. Claude Code's client ID is
  `https://claude.ai/oauth/claude-code-client-metadata`.

## 13. Manual and advanced setup

### 13.1 What `mak4i init` creates

`mak4i init` asks for an organization name, your display name, a first
project name and the Local HTTP port (press Enter for 9090), or takes them
as flags:

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

Set `MAK4I_HOME` to keep `.mak4i/` somewhere else.

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
[15. Credential recovery](#15-credential-recovery); to start over, see
[9.3 Delete your MAK4I data](#93-delete-your-mak4i-data).

**The Local HTTP port** is saved as `http_port` in `.mak4i/config.json`,
and every later `mak4i serve --transport http` uses it. It has no effect on
stdio. A config from before this setting existed uses 9090.

### 13.2 Without `mak4i init`

`mak4i init` is a convenience wrapper over the same control-plane
commands, which remain the right tool for several organizations, extra
principals, scripting or understanding the model. Each command's output
feeds the next:

```bash
# 1. Organization and its owner. Prints {"organization": {...}, "owner": {...}}.
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

More commands, all with `--actor <owner_principal_id>`, owner-only and
scoped to your own organization:

| Command | Purpose |
|---|---|
| `mak4i principal create --organization-id <id> --type human\|service\|agent --display-name <name> [--role member\|owner]` | add a principal |
| `mak4i grant revoke --principal-id <id> --project-id <id>` | remove a grant |
| `mak4i credential revoke --credential-id <id>` | revoke a credential immediately |
| `mak4i org list` / `org show --organization-id <id>` | organizations (trusted-operator scope) |
| `mak4i project list --organization-id <id>` / `project show --project-id <id>` | projects |
| `mak4i principal list --organization-id <id>` / `principal show --principal-id <id>` | principals |
| `mak4i grant list --principal-id <id>` | a principal's grants |
| `mak4i credential list --principal-id <id>` | a principal's credentials (never the token or its hash) |

`credential issue` also prints ready-to-copy connect instructions
(`--no-connection-help` suppresses them).

**Operator artifact commands.** Without a running server or a credential,
using `--principal <id>` (a trusted, local-only mode, audited as
`operator_impersonation` and never exposed over MCP):

```bash
mak4i doctor      --principal <id> --project <project_id>
mak4i create      --principal <id> --project <project_id> --artifact-id … --artifact-type … --title … --content …
mak4i supersede   --principal <id> --project <project_id> --old-id … --content … --reason …
mak4i search      --principal <id> --project <project_id> --tags …
mak4i get-current --principal <id> --project <project_id> --tags …
mak4i history     --principal <id> --project <project_id> --lineage-id …
```

After `mak4i init`, the IDs are in `.mak4i/config.json`.

## 14. Configuration reference

The one table of Local settings. `mak4i serve` sets the backend variables
from `.mak4i/`, so you normally set none of them.

| Variable | Meaning |
|---|---|
| `MAK4I_HOME` | where `.mak4i/` lives (default: `./.mak4i`) |
| `MAK4I_TRANSPORT` | `stdio` (default) or `http` / `streamable-http`; `--transport` wins |
| `MAK4I_HOST` | HTTP bind address (default `127.0.0.1`); `--host` wins. See the [security warning](#11-network-access-and-tunnels) before using anything else. |
| `MAK4I_TLS_TERMINATED_UPSTREAM` | `1` only when a TLS-terminating proxy sits in front of MAK4I. Without it, MAK4I refuses to serve HTTP on any non-loopback address (MAK-0008 §1.2). |
| `MAK4I_PORT` | HTTP port for one run; `--port` wins; otherwise the saved port, then 9090 |
| `MAK4I_INSTANCE_NAME`, `MAK4I_ENVIRONMENT` | how this server names itself to AI clients (default `MAK4I local (<organization>)` / `local`) |
| `MAK4I_AUDIT_LOG` | where CLI commands write audit and security events: `stderr` (default; stdout stays machine-readable) or a file path. Security events can't be switched off (MAK-0006 §8.6). |
| `MAK4I_TOKEN` | the credential for `python -m mak4i.mcp_server` over stdio (manual setup); ignored by `mak4i serve` |
| `MAK4I_CONTROL_PLANE_DB`, `MAK4I_STORE`, `MAK4I_LOCAL_STORE_DIR`, `MAK4I_CONTROL_PLANE_CREATE_TABLES` | backends for the manual setup ([13.2](#132-without-mak4i-init)); ignored by `mak4i serve` |

## 15. Credential recovery

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

## 16. Health and readiness checks

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
