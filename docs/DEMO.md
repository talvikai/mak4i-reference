# MAK4I demo walkthrough

A hands-on tour of MAK4I from a connected AI client: connect, list the
projects you can act on, create a decision, read it back, evolve it,
inspect its history, and see what an unauthorized request looks like.
Every step notes the audit events it produces, because the audit trail is
how you prove an answer came from MAK4I and not from a model's memory.

**The honest-test discipline.** The point of MAK4I is that a fact you
never told the current client still reaches it. When you try this
yourself, keep the fact under test (which cache, which database, …) out
of everything the client could otherwise see — system prompts, project
knowledge, pasted context, earlier turns in the conversation. If it can
only have come from a `mak4i_*` tool call, the demo means something.

**The audit format.** The server writes each audit event as one
structured JSON line to **stderr**. With Compose that's the container
log: `docker compose logs mak4i`. The CLI also writes to stderr, or to
the file named by `MAK4I_AUDIT_LOG`. See
[DEPLOYMENT.md → Operational logs](DEPLOYMENT.md#operational-logs).

```json
{"ts": "2026-10-15T12:00:00+00:00", "event": "CREATE", "correlation_id": "…", "actor": "prn_…", "principal_id": "prn_…", "organization_id": "org_…", "project_id": "prj_…", "auth_method": "oauth", "artifact_id": "…", "lineage_id": "…", "version": "1.0"}
```

Whatever collects your deployment's logs is where you read these. The
event names and fields are the same regardless of the log backend.

---

## Connecting a client

There are two ways to authenticate. Your identity, and everything you're
allowed to do, come from what you authenticated with. No tool has a
separate "user" or "actor" argument.

| Method | Use it for | What the client stores |
|---|---|---|
| **OAuth sign-in** ([Enterprise §3, §5](ENTERPRISE_SELF_HOSTED.md#3-turn-on-sign-in); locally [LOCAL_SETUP §4](LOCAL_SETUP.md#4-use-mak4i-from-claudeai-or-chatgpt) and [§13](LOCAL_SETUP.md#13-oauth-sign-in-for-apps-on-your-computer)) | claude.ai, ChatGPT, Claude Code: any client with MCP OAuth | nothing you paste; you sign in once with a one-time code |
| **Credential header** `Authorization: Bearer <token>` | Claude Code, Gemini CLI, scripts: any client that can send a header | the credential issued with `mak4i credential issue` |

OAuth needs the server's OAuth service enabled. Hosted clients (claude.ai,
ChatGPT) also need a public HTTPS endpoint: an Enterprise Self-Hosted
installation, or a Local installation behind an HTTPS tunnel or proxy you
set up ([LOCAL_SETUP §4](LOCAL_SETUP.md#4-use-mak4i-from-claudeai-or-chatgpt)).
On a Local installation, run the `mak4i oauth …` commands below with the
same `MAK4I_OAUTH_ENABLED=1` and `MAK4I_PUBLIC_ENDPOINT` as the server; a
Local owner can leave out `--actor`.

**Two things to check first if a connection fails:**

- **URL must end in `/mcp`.** The bare server root is not the MCP
  endpoint and returns `404`. A `401`→`404` shift between attempts means
  auth started passing but the path is wrong. ChatGPT without `/mcp` signs
  in and then reports *action discovery failed*.
- **A header value must include the literal `Bearer ` prefix** (the word,
  then a space, then the token). Without it the request is rejected
  before it reaches any tool.

### Signing in with OAuth

When a client opens the MAK4I sign-in page, an organization owner issues
a one-time code (or you issue one for yourself with your own credential):

```bash
mak4i oauth sign-in-code --actor <owner-principal-id> --principal-id <your-principal-id>
```

Enter it, then approve the consent page. A code works once and for 10
minutes. After that, new conversations reuse the connection. Sign in
again after 7 days unused, 30 days in total, or when the authorization is
revoked.

### Claude.ai / Cowork

**OAuth (recommended).** An owner registers Claude once and gives you the
printed client ID:

```bash
mak4i oauth client register --actor <owner> --name "Claude (claude.ai)" \
  --redirect-uri https://claude.ai/api/mcp/auth_callback
```

Then **Settings → Connectors → Add custom connector**:
- **URL**: your endpoint, including `/mcp`;
- **Advanced settings → OAuth Client ID**: the printed `mak4c_…` ID, no secret.

Connect and sign in with a code. The sign-in page opens in a pop-up;
allow pop-ups for claude.ai.

**Static header (alternative).** Where your organization has Claude.ai's
static-header connector beta, use **Authentication: None** with the
request header `authorization` = `Bearer <your token>`, marked Required.
An enterprise-managed Cowork/Claude Desktop deployment can carry the
header through a `managedMcpServers` entry.

### ChatGPT

ChatGPT connectors support only OAuth or no authentication, so use OAuth.
An owner registers ChatGPT once:

```bash
mak4i oauth client register --actor <owner> --name "ChatGPT" \
  --redirect-uri https://chatgpt.com/connector_platform_oauth_redirect
```

Then:
1. **Settings → Apps & Connectors → Advanced settings**: turn on
   **Developer mode**.
2. **Create** a connector with URL `https://<your-host>/mcp`.
3. Choose **OAuth → user-defined OAuth**: Client ID `mak4c_…`, no secret,
   scopes `mak4i:read mak4i:write` (add `mak4i:resolve` only if this
   connection should resolve conflicts).
4. Sign in with a code.

ChatGPT also offers a client metadata document, but MAK4I accepts those
only for public clients, so use pre-registration.

### Claude Code

```bash
# OAuth: no token in your configuration
claude mcp add --transport http mak4i <endpoint-url>
# then /mcp → mak4i → Authenticate, and sign in with a code

# or a credential header
claude mcp add --transport http mak4i <endpoint-url> \
  --header "Authorization: Bearer <your token>"
claude mcp get mak4i     # health-checks the connection
```

Over OAuth, Claude Code identifies itself with its client metadata
document (`https://claude.ai/oauth/claude-code-client-metadata`). Nothing
needs registering.

### Gemini CLI

```bash
npx -y @google/gemini-cli mcp add --transport http mak4i <endpoint-url> \
  --header "Authorization: Bearer <your token>" -s user
npx -y @google/gemini-cli mcp list     # lists the discovered tools
```

Gemini CLI needs its own, separate authentication to Google's models —
unrelated to the MAK4I credential.

Which clients have been verified end to end is recorded in the release
notes. Treat any other client as untested.

---

## A full walkthrough

Assumes you've connected a client and Talvik (or your own operator) has
given you an endpoint, a credential, and at least one `project_id`. The
tool calls below are shown as `tool(args)`; run them however your client
runs tools.

### 1. `mak4i_list_projects` — see what you can act on

```
mak4i_list_projects()
→ [ { "project_id": "prj_a1…", "name": "Example Project",
      "organization_id": "org_x…", "permissions": ["read", "write"] } ]
```

No arguments. It returns exactly the projects your credential is granted
on, each with your permissions. Projects you have no grant on — including
every project in every other organization — are simply absent; the result
never hints at their existence.

### 2. `mak4i_create` — write a decision

```
mak4i_create(
  project      = "prj_a1…",
  artifact_id  = "primary-database",
  artifact_type= "decision",
  title        = "Primary application database",
  content      = "The primary application database is PostgreSQL.",
  rationale    = "Team familiarity; existing ops tooling.",
  tags         = ["database", "infrastructure"],
)
→ artifact { "artifact_id": "primary-database", "version": "1.0",
             "status": "active", "created_by": "prn_you…", … }
```

`created_by` is **your authenticated principal id**. There is no
`created_by` or `actor` parameter — provenance can't be spoofed by the
caller.

*Audit:* `ACCESS_GRANTED (permission=write)` then `CREATE`, both carrying
your `principal_id` and the `project_id`.

### 3. `mak4i_search` and `mak4i_get_current` — read it back

```
mak4i_search(project="prj_a1…", tags=["database"])
→ [ the artifact you just created ]

mak4i_get_current(project="prj_a1…", tags=["database"])
→ { "artifacts": [ primary-database v1.0 ],
    "conflicts": [], "integrity_errors": [] }
```

`search` is a raw deterministic candidate lookup. `get_current` runs the
full resolution on top: it applies lineage, and it checks for conflicts
and integrity errors before returning the current applicable set.

*Audit:* `ACCESS_GRANTED (permission=read)` then `SEARCH`, or for
`get_current`, `GET_CURRENT` followed by `DISCOVER` → `RESOLVE` →
`INJECT` (the resolver's candidate list, resolved set, and the context
package it built).

### 4. `mak4i_supersede` — record a change

```
mak4i_supersede(
  project = "prj_a1…",
  old_id  = "primary-database",
  content = "The primary application database is MySQL.",
  reason  = "Moving from PostgreSQL to MySQL for managed-hosting availability.",
  tags    = ["database", "infrastructure"],
)
→ artifact { "artifact_id": "primary-database-v2", "version": "2.0",
             "supersedes": "primary-database", "status": "active", … }
```

*Audit:* `ACCESS_GRANTED (write)` then `SUPERSEDE` with `old_id` and
`new_id`.

### 5. `mak4i_get_current` again — the old version is no longer current

```
mak4i_get_current(project="prj_a1…", tags=["database"])
→ { "artifacts": [ primary-database-v2 v2.0 (MySQL) ],
    "conflicts": [], "integrity_errors": [] }
```

The v1.0 PostgreSQL artifact still exists and is still readable — it's
just not what "current" resolves to anymore.

### 6. `mak4i_history` — the whole chain, with reasons

```
mak4i_history(project="prj_a1…", lineage_id="primary-database")
→ [ { version: "1.0", status: "superseded", content: "…PostgreSQL." },
    { version: "2.0", status: "active", content: "…MySQL.",
      supersedes: "primary-database",
      rationale: "Moving from PostgreSQL to MySQL for managed-hosting availability." } ]
```

Oldest first, nothing dropped — the supersede reason is carried as the
new version's `rationale`. This is what lets a later client explain *why*
the current decision is what it is, not just what it is.

### 7. An unauthorized project — explicit denial

```
mak4i_get_current(project="prj_someone_elses…", tags=["database"])
→ Tool error: "access denied: this principal has no read permission on the
  requested project on MAK4I connection 'MAK4I Enterprise' (environment:
  enterprise). This denial is final for this connection. Do not retry this
  operation on another MAK4I connection, organization or project unless the
  user explicitly selects and confirms that exact destination."
```

The denial names the connection that refused but never echoes the
requested project. If the client has several MAK4I connections,
`mak4i_whoami` on each shows which installation, organization and principal
it is.

*Audit:* a single `ACCESS_DENIED` event (`permission=read`, the target
`project_id`) — and **nothing else**: no `DISCOVER`, `RESOLVE`, or
`INJECT`, because the request never reaches the store. The denial looks
identical whether that project belongs to another organization or doesn't
exist at all, so a denial can never be used to probe what projects exist.
(Regression-tested in
`tests/test_security_dev_preview.py::test_8_denial_and_search_leak_no_unauthorized_project_metadata`.)

---

## Cross-client: a decision made in one AI, used by another

This is the whole point of MAK4I, and it's worth doing with two genuinely
separate client processes that share no context.

**Write leg** — in client 1, prompt naturally: *"We've decided to use
PostgreSQL for our primary application database."* A well-behaved client
checks MAK4I first (`mak4i_search` / `mak4i_get_current`), finds nothing,
judges the decision durable, and calls `mak4i_create`. Later, *"We're
switching that database to MySQL for managed-hosting availability"* →
`mak4i_supersede`.

**Read leg** — in a fresh client 2 (new process, no mention of
PostgreSQL, MySQL, or the earlier conversation), prompt: *"What's our
primary application database, and why? Check MAK4I."* The client's own
model calls `mak4i_get_current` and `mak4i_history` and answers:

> "MySQL. The team moved from PostgreSQL for better managed-hosting
> availability."

*Audit for the read:* `DISCOVER` (both lineage versions as candidates) →
`RESOLVE` (`resolved_ids` = the v2 MySQL artifact, `conflict_count=0`) →
`INJECT` (the context package). The correct current answer and the
correct rationale reached client 2 without it ever seeing the write
conversation — which is exactly what the audit trail proves.

This has been run end to end with real, separate clients (Claude.ai and
Claude Code as writers, Gemini CLI as an independent reader).

---

## Same person, different AI clients: client-scoped access

MAK4I can give the same person different access to a project depending on
the **verified client** they connect with (MAK-0006 §5.7). This scenario
gives Claude read-only and ChatGPT read and write.

> These steps are covered by the automated suite (`tests/test_client_policies.py`).
> Their run against real claude.ai and ChatGPT connectors is recorded in
> the release notes once it has been done. Until then, treat this as the
> procedure, not as evidence.

**Setup (owner, CLI).** You need both clients registered (see above), the
person's principal ID, and a grant with `read,write` on the project.

```bash
mak4i oauth client list --actor "$OWNER"            # note the Claude and ChatGPT client IDs
mak4i client-policy set --actor "$OWNER" --principal-id "$PERSON" --project-id "$PROJECT" \
  --oauth-client-id "$CLAUDE_CLIENT_ID" --permissions read
mak4i client-policy set --actor "$OWNER" --principal-id "$PERSON" --project-id "$PROJECT" \
  --oauth-client-id "$CHATGPT_CLIENT_ID" --permissions read,write
mak4i project client-restrictions --actor "$OWNER" --project-id "$PROJECT"   # review who gets what
```

**1. Check the verified client in each app.** Ask each client to call
`mak4i_whoami`. Both show the same `principal`. `client` differs, and it
comes from the sign-in, not from the app's self-reported name:

```
Claude : "client": {"kind": "oauth_client", "id": "<CLAUDE_CLIENT_ID>",  "registration": "pre_registered"}
ChatGPT: "client": {"kind": "oauth_client", "id": "<CHATGPT_CLIENT_ID>", "registration": "pre_registered"}
```

**2. Compare project access.** `mak4i_list_projects` shows
`"permissions": ["read"]` in Claude and `["read", "write"]` in ChatGPT
for the same project.

**3. Write from Claude: denied.** Ask Claude to create a decision:

```
mak4i_create(project=..., artifact_id="cache-choice", ...)
→ isError: true
  {"error": {"code": "access_denied", "reason": "client_not_permitted",
             "retryable": false, "message": "access denied: this AI client may not use write permission …"}}
```

Nothing is written. The message names no other client. *Audit:*
`ACCESS_DENIED` and `WRITE_REFUSED`, both with `reason:
client_not_permitted` and Claude's `oauth_client_id`.

**4. Write from ChatGPT: allowed.** The same request succeeds. The
record's `provenance.oauth_client_id` is ChatGPT's client ID.

**5. Read it in Claude.** `mak4i_get_current` in Claude returns the
record ChatGPT wrote. Read access is unchanged.

**6. Change takes effect immediately.** `mak4i client-policy set …
--oauth-client-id "$CLAUDE_CLIENT_ID" --permissions read,write`, then retry
step 3 in the same Claude conversation. It now succeeds without signing in
again. Removing a policy, or switching `project client-restrictions --on`,
also applies on the next request.

---

## Conflicts are surfaced, never resolved for you

Two independent lineages conflict only when they claim the **same
subject**: the same `artifact_type` and the same `subject_key`, a stable
machine-readable key such as `session-cache`. Tags are for search only and
never create a conflict, so any number of independent artifacts (for
example separate requirements documents) can share tags and all stay
current.

Record a *second, independent* decision for the same subject — not a
supersede:

```
mak4i_create(project="prj_a1…", artifact_id="caching-decision",
  artifact_type="decision", subject_key="session-cache",
  title="Session cache", content="Use Redis.", tags=["caching"])
mak4i_create(project="prj_a1…", artifact_id="caching-decision-memcached",
  artifact_type="decision", subject_key="session-cache",
  title="Session cache", content="Use Memcached.", tags=["caching"])

mak4i_get_current(project="prj_a1…", tags=["caching"])
→ { "artifacts": [],
    "conflicts": [ { "conflict_id": "cfl_3f…", "state": "open",
                     "artifact_type": "decision", "subject_key": "session-cache",
                     "identical_content": false,
                     "candidates": [ { "artifact_id": "caching-decision",
                                       "content": "Use Redis.",
                                       "provenance": { "principal_id": "prn_…", "agent_id": null, … } },
                                     { "artifact_id": "caching-decision-memcached",
                                       "content": "Use Memcached.", … } ] } ],
    "integrity_errors": [] }
```

`artifacts` is empty and `conflicts` holds every competing decision with
who wrote it — MAK4I never picks one, not even the newest, and a tag filter
can't hide one side. Once the user decides, a principal with the `resolve`
permission resolves it explicitly, naming the candidates it showed the
user:

```
mak4i_resolve_conflict(project="prj_a1…", conflict_id="cfl_3f…",
  candidate_artifact_ids=["caching-decision", "caching-decision-memcached"],
  action="select_winner", winner_artifact_id="caching-decision",
  reason="Redis selected for session-cache.")
→ { "state": "completed", "effects": [ …unchanged…, …withdrawn… ], … }
```

The three actions are `select_winner` (the others are withdrawn),
`merge` (one new authoritative version, the others withdrawn) and
`separate_subjects` (every candidate gets its own subject key). Nothing is
deleted: withdrawn versions keep their content and their claim in
history, and the resolution record (who, when, why, what changed) stays
retrievable with `mak4i_get_conflict` / `mak4i_list_conflicts`. If the
candidates changed after the user looked, the call fails with
`conflict_changed` instead of acting on stale information. A new
contribution after a resolution starts a new conflict. Releasing a
subject key with `mak4i_supersede` is refused while the subject is in
conflict. The CLI equivalents are `mak4i conflict list|show|resolve`.

Detection and resolution are audited (`CONFLICT`, `RESOLUTION_STARTED`,
`RESOLUTION_COMPLETED`). A broken lineage (zero or multiple active
versions, a dangling pointer) surfaces the same way but as
`integrity_errors`, a distinct category.

Whether a retracted-but-still-readable artifact should influence a
generated document is left to the calling client reading the content —
MAK4I surfaces it as data, it doesn't filter.

---

## Integrity checking (CLI)

```bash
mak4i doctor --principal <your principal id> --project <project_id>
→ OK — no integrity errors found in project '<project_id>'.
```

A fail-closed lineage integrity report. CLI-only and never model-callable
— it's an operator check, not a tool.

---

## What the walkthrough demonstrates

| Behavior | Where you saw it |
|---|---|
| Project is the authorization boundary; you see only what you're granted | step 1 |
| Provenance is principal-derived, not caller-supplied | step 2 (`created_by`) |
| Deterministic discovery, then resolution with conflict/integrity checks | step 3 |
| Supersession hides the old version as "current" but keeps it and its rationale | steps 4–6 |
| Denials are explicit, audited, and leak no metadata | step 7 |
| A decision crosses from one AI client to another with zero shared context | cross-client section |
| Competing decisions are surfaced, never auto-resolved | conflicts section |
| Every operation leaves an attributable audit event | throughout |
