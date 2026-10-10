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
structured JSON line to stdout:

```json
{"event": "CREATE", "principal_id": "prn_…", "organization_id": "org_…", "project_id": "prj_…", "artifact_id": "…", "correlation_id": "…"}
```

Whatever collects your deployment's logs is where you read these. The
event names and fields are the same regardless of the log backend.

---

## Connecting a client

Every client authenticates the same way: a static
`Authorization: Bearer <token>` request header, where `<token>` is your
per-principal credential. The endpoint URL must include the `/mcp` path.
Your identity, and everything you're allowed to do, is derived from that
credential — there is no separate "user" or "actor" argument on any tool.

**Two things to check first if a connection fails:**

- **URL must end in `/mcp`.** The bare server root is not the MCP
  endpoint and returns `404`. A `401`→`404` shift between attempts means
  auth started passing but the path is wrong.
- **Header value must include the literal `Bearer ` prefix** (the word,
  then a space, then the token). Without it the request is rejected in a
  couple of milliseconds — it never gets past the credential middleware.

### Claude.ai / Cowork

**Customize → Connectors → Add custom connector**:

- **URL**: your endpoint, including `/mcp`
- **Authentication**: None
- **Request header**: name `authorization`, value `Bearer <your token>`,
  marked Required

(Claude.ai's static-header connector support is a per-organization beta;
an enterprise-managed Cowork/Claude Desktop deployment instead carries
the header through a `managedMcpServers` config entry set by whoever
administers it.)

### Claude Code

```bash
claude mcp add --transport http mak4i <endpoint-url> \
  --header "Authorization: Bearer <your token>"
claude mcp get mak4i     # health-checks the connection
```

### Gemini CLI

```bash
npx -y @google/gemini-cli mcp add --transport http mak4i <endpoint-url> \
  --header "Authorization: Bearer <your token>" -s user
npx -y @google/gemini-cli mcp list     # lists the discovered tools
```

Gemini CLI needs its own, separate authentication to Google's models —
unrelated to the MAK4I credential.

### ChatGPT — known limitation

ChatGPT's custom-connector UI (**Settings → Apps → Advanced → Developer
mode**) supports only OAuth or no-authentication connectors; per OpenAI's
own docs it has *"no field to set a Bearer token (or arbitrary
headers)."* Such clients need MAK4I's OAuth sign-in
([Enterprise → OAuth sign-in](ENTERPRISE_SELF_HOSTED.md#71-oauth-sign-in)).
Whether a given client has been verified end to end against it is
recorded in the release notes; until then treat it as untested.

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
    "conflicts": [ { "artifact_type": "decision",
                     "subject_key": "session-cache",
                     "lineage_ids": ["caching-decision", "caching-decision-memcached"],
                     "artifacts": [ …Redis…, …Memcached… ] } ],
    "integrity_errors": [] }
```

`artifacts` is empty and `conflicts` holds both competing decisions —
MAK4I never picks one. To resolve it in favor of Redis, supersede the
losing lineage and **explicitly release** its claim on the subject:

```
mak4i_supersede(project="prj_a1…", old_id="caching-decision-memcached",
  content="Memcached option rejected; Redis selected for session-cache.",
  reason="Resolved the session-cache conflict in favor of Redis.",
  release_subject_key=true)
→ { …, "subject_key": null, "released_subject_key": "session-cache", … }
```

The Memcached lineage stays readable (its history still shows it competed
for `session-cache`) but no longer claims the subject; the Redis lineage
is untouched and is now the only current claim. A normal supersede never
changes a lineage's `subject_key`: attempting it is rejected. Both steps
are audited (`CONFLICT` on detection; `SUPERSEDE` with
`released_subject_key` on resolution). A broken lineage (zero or multiple
active versions, a dangling pointer) surfaces the same way but as
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
