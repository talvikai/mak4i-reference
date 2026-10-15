# Administrative CLI reference

Every administrative task in the MAK4I Reference is a `mak4i` command run
on the host (MAK-0006 §8, "administration without a portal"). There is no
web console and no remote administration endpoint: the MCP server exposes
no administrative tool, and nothing administrative is reachable over HTTP.
This page lists every command `mak4i` accepts, who may run it, what it
records and what it prints. It is derived from `build_parser()` in
`src/mak4i/cli.py`; `mak4i <command> --help` shows the same flags.

For the step-by-step setup of a deployed installation, see
[Enterprise Self-Hosted §6](ENTERPRISE_SELF_HOSTED.md#6-create-your-organization-and-access).
The behaviour described here is exercised end to end, through the real
executable, by `tests/test_admin_cli_acceptance.py`.

## Contents

- [How the CLI authenticates](#how-the-cli-authenticates)
- [Who may run what](#who-may-run-what)
- [Outputs, exit codes and audit](#outputs-exit-codes-and-audit)
- [Administrative commands](#administrative-commands)
- [Record commands](#record-commands)
- [Known gaps](#known-gaps)

## How the CLI authenticates

The CLI talks to the control-plane database directly
(`MAK4I_CONTROL_PLANE_DB`) and to the artifact store. Whoever can run it
against that database is the **instance operator** (MAK-0006 §8.1). On top
of that, every organization-scoped command names the principal it acts
for:

| Mode | How | Recorded `auth_method` |
|---|---|---|
| Operator mode | `--actor <principal-id>` on administrative commands, `--principal <principal-id>` on record commands. The id is looked up, not authenticated: this is trusted local tooling on the host. The named principal's role and status are still checked on every command. | `operator` |
| Credential | Omit `--actor` and set `MAK4I_TOKEN` to the acting principal's credential (or, in a local `mak4i init` environment, let the CLI use the saved credential). The token is authenticated like any MCP request: a revoked or expired credential, a deactivated principal or a suspended organization is refused. | `credential` |
| Instance operator | Commands that take no `--actor` at all (organization lifecycle, inventory, instance-wide OAuth revocation, bootstrap). They act with the database access itself; an organization owner has no way to name itself on them. | `operator`, with `actor.principal_id` `null` |

Because operator mode trusts the id it is given, the database (and the
host or container that can reach it) must never be exposed. Hosted MCP
clients always authenticate with a credential or OAuth token; `--actor`
and `--principal` are never wired into the MCP server.

## Who may run what

| Who | Meaning in this page |
|---|---|
| **Owner** | An active principal with `role = owner`, acting on its own organization only (MAK-0006 §8.2). An owner of another organization, a member (whatever its project permissions, including `resolve`) and a deactivated owner are refused with `access denied`, and the refusal is recorded as a `denied` event in the target's organization. |
| **Self** | The principal acting for itself. |
| **Instance operator** | Whoever can run the CLI against the database; no `--actor` is accepted. |
| **Local operator** | A developer on a local `mak4i init` environment, or the operator running a record command in operator mode; access to records is still decided by the named principal's grants. |

## Outputs, exit codes and audit

- **stdout** carries only the command's result: JSON (indented, one
  document) or the human text noted below. Refusals and errors print
  nothing on stdout.
- **stderr** carries `error: <message>` on refusal, progress lines for the
  onboarding commands, and, unless `MAK4I_AUDIT_LOG` names a file, the
  structured audit events (one JSON object per line).
- **Exit codes:** `0` success; `1` refused or failed (`error: …` on
  stderr); `2` usage error (unknown or missing flag, including `--actor` on
  an instance-operator command).
- **Secrets** (credential tokens, sign-in codes, OAuth client secrets)
  appear exactly once, on the stdout of the command that created them.
  They are stored only as hashes; no list, show, inventory, audit event or
  log line ever contains a token, its hash, a sign-in code or a secret.
- **Durable administrative events** (MAK-0006 §8.5) are written to the
  control-plane database for every administrative mutation: `succeeded`,
  `denied` (not an owner of the target's organization), or `failed`
  (validation, not found, last owner; with the exception class in
  `detail`). The OAuth actions record `succeeded` and `denied`. Owners read
  them with `mak4i audit list`, newest first, in the
  `admin-event.schema.json` shape (event id, time, actor principal and
  auth method, action, targets, outcome, correlation id). Each event is
  also emitted to the audit log stream as an `"event": "ADMIN"` line.
  Read-only commands (list, show, inventory, audit list, config) record
  nothing.

## Administrative commands

"Records" is the `action` of the durable administrative event; "—" means
the command records none.

### Organizations

| Command | Purpose | Who | Required flags | Records | Output |
|---|---|---|---|---|---|
| `org create` | Create an organization and its first owner (bootstrap) | Instance operator | `--name`, `--owner-display-name` | `organization.create` | JSON `{organization, owner}` |
| `org list` | List every organization | Instance operator | — | — | JSON array |
| `org show` | Show one organization | Instance operator | `--organization-id` | — | JSON |
| `org suspend` | Suspend: no principal of it can authenticate, its projects are inaccessible; nothing is deleted | Instance operator | `--organization-id` | `organization.suspend` | JSON organization |
| `org reactivate` | Undo a suspension | Instance operator | `--organization-id` | `organization.reactivate` | JSON organization |

### Projects

| Command | Purpose | Who | Required flags | Records | Output |
|---|---|---|---|---|---|
| `project create` | Create a project | Owner | `--organization-id`, `--name` | `project.create` | JSON project |
| `project list` | List the organization's projects (with status) | Owner | — (`--organization-id` defaults to the actor's) | — | JSON array |
| `project show` | Show one project | Owner | `--project-id` | — | JSON project |
| `project archive` | Archive: every grant on it becomes ineffective; nothing is deleted | Owner | `--project-id` | `project.archive` | JSON project |

### Principals

| Command | Purpose | Who | Required flags | Records | Output |
|---|---|---|---|---|---|
| `principal create` | Create a `human`, `service` or `agent` principal (`--role member` by default, or `owner`) | Owner | `--organization-id`, `--type`, `--display-name`; `--agent-id` for `--type agent` (unique in the organization, permanent) | `principal.create` | JSON principal (`principal_type`, with `type` as an alias) |
| `principal list` | List the organization's principals | Owner | — (`--organization-id` defaults to the actor's) | — | JSON array |
| `principal show` | Show one principal | Owner | `--principal-id` | — | JSON principal |
| `principal rename` | Change the display name; ids and recorded history never change | Owner | `--principal-id`, `--display-name` | `principal.rename` | JSON principal |
| `principal deactivate` | Every credential and OAuth access of the principal stops at once; its OAuth authorizations are revoked; history is kept. The last active owner can't be deactivated. | Owner | `--principal-id` | `principal.deactivate` | JSON `{principal, oauth_authorizations_revoked}` |

### Grants

| Command | Purpose | Who | Required flags | Records | Output |
|---|---|---|---|---|---|
| `grant create` | Set a principal's permissions on a project of the same organization (`read`, `write`, `resolve`; replaces an existing grant) | Owner | `--principal-id`, `--project-id`, `--permissions` (comma-separated) | `grant.set` | JSON grant |
| `grant list` | List one principal's grants | Owner | `--principal-id` | — | JSON array |
| `grant revoke` | Remove a grant; effective on the next request | Owner | `--principal-id`, `--project-id` | `grant.revoke` | Text: `revoked grant for principal '…' on project '…'` |

`resolve` lets a principal settle record conflicts; no permission ever
confers administration (MAK-0006 §8.3).

### Credentials

| Command | Purpose | Who | Required flags | Records | Output |
|---|---|---|---|---|---|
| `credential issue` | Issue a bearer credential (optional `--display-name`, `--expires-at` ISO 8601, `--no-connection-help`) | Owner (also for itself; a member can't issue its own) | `--principal-id` | `credential.issue` (targets: principal, display name, credential id) | Text: `credential_id: …`, `Authorization: Bearer <token>` **once**, then client connection instructions unless `--no-connection-help` |
| `credential list` | List one principal's credentials with status, expiry and revocation time; never the token or its hash | Owner | `--principal-id` | — | JSON array |
| `credential revoke` | Revoke a credential; effective on the next request | Owner | `--credential-id` | `credential.revoke` | JSON credential summary (no hash) |

### OAuth

Every `oauth` command needs the OAuth service configured
(`MAK4I_OAUTH_ENABLED=1` and `MAK4I_PUBLIC_ENDPOINT`, the public MCP URL);
otherwise it exits 1 with a message saying so.

| Command | Purpose | Who | Required flags | Records | Output |
|---|---|---|---|---|---|
| `oauth sign-in-code` | One-time code for the OAuth sign-in page (single use, expires within 10 minutes; never a bearer token). Defaults to the actor itself. | Self, or Owner for any active principal of its organization | — (`--principal-id` for another principal) | `oauth_sign_in_code.issue` | Text: `principal_id`, `sign-in code` **once**, `expires_at` |
| `oauth client register` | Pre-register a client for the organization (public with PKCE, or `--confidential`) | Owner | `--name`, `--redirect-uri` (repeatable; https, or http on a loopback host) | `oauth_client.register` | Text: `client_id`, and `client_secret` **once** for a confidential client |
| `oauth client list` | List the organization's pre-registered clients; never secrets | Owner | — | — | JSON array |
| `oauth client disable` | Disable a client and revoke every authorization of it | Owner | `--client-id` | `oauth_client.disable` | JSON client |
| `oauth authorization list` | List OAuth authorizations (filter with `--principal-id` / `--client-id`); never token values | Owner | — | — | JSON array |
| `oauth authorization revoke` | Revoke one authorization (`--authorization-id`), or all of a principal's (`--principal-id`) or a client's (`--client-id`) in the organization, with their tokens | Owner; Self for one of its own authorizations by `--authorization-id` | one of `--authorization-id`, `--principal-id`, `--client-id` | `oauth_authorization.revoke` (one) or `oauth_authorization.revoke_all` (with `count`) | JSON authorization, or `{"revoked": n}` |

### Audit and instance tools

| Command | Purpose | Who | Required flags | Records | Output |
|---|---|---|---|---|---|
| `audit list` | The organization's administrative events, newest first, including denied attempts (`--limit`, default 200) | Owner | — (`--organization-id` defaults to the actor's) | — | JSON array of admin events |
| `admin inventory` | Non-secret inventory of the installation: organizations, owners, projects, principals, grants, credential metadata | Instance operator | — | — | JSON |
| `admin revoke-oauth` | Revoke every active OAuth authorization of every organization (after restoring a backup); works with OAuth disabled | Instance operator | `--all` (optional `--reason`) | `oauth_authorization.revoke_all_instance` (instance-level, see [Known gaps](#known-gaps)) | JSON `{"revoked": n}` |

### Onboarding, configuration and diagnostics

| Command | Purpose | Who | Required flags | Records | Output |
|---|---|---|---|---|---|
| `init` | Create a local environment in `.mak4i/`: organization, owner, project, read/write grant, credential (saved, never printed) | Local operator | none when interactive; `--org-name`, `--display-name`, `--project-name` otherwise (`--http-port`, `--force` optional) | the same events as the granular commands (`organization.create`, `project.create`, `grant.set`, `credential.issue`) | Text summary; progress on stderr |
| `serve` | Run the MCP server for the local environment (`--transport`, `--host`, `--port`) | Local operator | — | — | Server; banner on stderr (stdout is the stdio MCP stream) |
| `access provision` | One step for an external collaborator: a new organization (owner `<org name> (operator)`), a member principal, a project, a read/write grant and a credential. Runs non-interactively when every flag is given. | Instance operator | `--endpoint` or `MAK4I_PUBLIC_ENDPOINT`; `--display-name`, `--org-name`, `--project-name` when not interactive (optional `--output` file, mode 600) | `organization.create`, `principal.create`, `project.create`, `grant.set`, `credential.issue` | Text: endpoint, project and the token **once**; nothing is provisioned if the endpoint or a value is missing |
| `config check` | Validate the `MAK4I_*` configuration as the server would and print the effective settings; secrets only as references | Instance operator | — | — | JSON (exit 1 with the problems on stderr if invalid) |
| `config schema` | Print the configuration JSON Schema | Anyone | — | — | JSON |
| `doctor` | Check lineage integrity of a project's records | Local operator (`--principal` selects the organization; no grant is checked) | `--principal`, `--project` | — | Text: `OK — …` on stdout, or `FAILED — …` and exit 1 on stderr |

## Record commands

These read and write project records through the same engine, grants and
operational security events as MCP clients. They run in operator mode
(`--principal`, recorded with that principal and `auth_method` `operator`);
a refused write exits 1 and emits a `WRITE_REFUSED` event with its error
code (MAK-0006 §8.6). All print JSON.

| Command | Purpose | Permission needed | Required flags |
|---|---|---|---|
| `create` | Create a record | `write` | `--principal`, `--project`, `--artifact-id`, `--artifact-type`, `--title`, `--content` |
| `supersede` | Replace the active version of a record | `write` | `--principal`, `--project`, `--old-id`, `--content`, `--reason` |
| `search` | Raw candidate lookup | `read` | `--principal`, `--project` |
| `get-current` | Resolved current context | `read` | `--principal`, `--project` |
| `history` | A lineage's history, oldest first | `read` | `--principal`, `--project`, `--lineage-id` |
| `changes` | The project's change feed after a cursor (MAK-0008 §14.4) | `read` | `--principal`, `--project` (`--after`, `--limit`) |
| `conflict list` / `conflict show` | Subject conflicts | `read` | `--principal`, `--project` (`--conflict-id` for show) |
| `conflict resolve` | Resolve a conflict | `resolve` | `--principal`, `--project`, `--conflict-id`, `--candidates`, `--action`, `--reason` |

## Known gaps

Found by the acceptance tests and tracked there as expected failures:

- `grant create` with an unknown permission name (for example
  `--permissions read,admin`) is refused and recorded as a `failed`
  `grant.set` event, but prints a Python traceback instead of a one-line
  `error:` message.
- `admin revoke-oauth --all` records one instance-level event with no
  organization, so owners of the affected organizations don't see it in
  `audit list`. The instance operator finds it in the audit log stream.

Not offered by the CLI (by design or not yet): deleting anything
(MAK-0006 §8.4), reactivating a principal, and listing every principal
that holds a grant on one project (list grants per principal instead).
