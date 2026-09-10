# Token / Context Efficiency Measurement

One run per condition per task, against a deployed reference instance
holding a small, known project state (a current caching decision and a
current primary-database decision, each established earlier through the
demo flows). Per requirements §13: **no fixed token-saving percentage is
claimed here** — see "What this does and doesn't show" below. All figures
are from this one run; this is not a statistically robust sample, and a
different query shape (see task 1/2 below) measurably changes the result.

Client: Claude Code (`claude -p`, `--output-format json`), so all three
conditions use the same underlying model/client family — condition 1 ran
from a neutral, empty directory with no project files and MAK4I tools
explicitly disallowed; condition 3 used the deployed MAK4I server.
"Prompt size" is the exact character count of the prompt sent (a
client-independent measure); "output tokens" is the real, API-reported
`usage.output_tokens` for the response; condition 3's context size is the
real `context_result_bytes`/`context_tokens_estimate` from the audit
`INJECT` event (`measurement_mode: mak4i_context`), read from the audit
log, not estimated.

The project used here is referred to below as "the example project", and
its two current decisions as the caching decision (e.g. Redis) and the
database decision (e.g. MySQL) — the specific technologies don't affect
what is being measured.

## Task 1 — "What is the current caching technology decision for the example project?"

| Condition | Prompt | Context injected | Response (output tokens) | Correct? |
|---|---|---|---|---|
| (1) Fresh, no context | 74 chars | none | 1397 tokens | **Declined** — correctly reported it had no way to look this up, did not guess |
| (2) Manual context (operator pastes the known-correct decision) | 326 chars | n/a (part of prompt) | 1734 tokens | **Correct** (matched the current caching decision) |
| (3) MAK4I-selected | 110 chars | 3526 bytes / 880 tokens (audit-measured) | 997 tokens | **Correct**, with artifact/provenance citation |

Note: condition 3's tool call was unfiltered (`mak4i_get_current` with no
`tags` argument), so MAK4I returned **all currently-active artifacts in
the project** (caching, database, and a retracted-error artifact from the
conflict flow) — not just the one relevant to this task. An earlier
attempt in the same run queried too narrowly and got 0 results (928 bytes,
logged separately) before the model retried more broadly. The 880-token
figure reflects that broader, unfiltered result.

## Task 2 — "What is the current primary database decision for the example project?"

| Condition | Prompt | Context injected | Response (output tokens) | Correct? |
|---|---|---|---|---|
| (1) Fresh, no context | 72 chars | none | 1251 tokens | **Declined** — same as task 1 |
| (2) Manual context | 619 chars | n/a | 1205 tokens | **Correct** (matched the current database decision) |
| (3) MAK4I-selected | 108 chars | 3526 bytes / 880 tokens (audit-measured) | 945 tokens | **Correct**, with rationale citation |

Same unfiltered-query pattern as task 1 — identical injected-context
figure, since the same multi-artifact project state was returned both times.

## Task 3 — "Write a 2-3 sentence architecture summary (caching + database)"

| Condition | Prompt | Context injected | Response (output tokens) | Correct? |
|---|---|---|---|---|
| (1) Fresh, no context | 168 chars | none | 821 tokens | **Declined** — explicitly refused to invent the caching/database choices |
| (2) Manual context (both decisions pasted) | 640 chars | n/a | 314 tokens | **Correct** (both decisions cited) |
| (3) MAK4I-selected | 210 chars | 3526 bytes / 880 tokens (audit-measured) | 609 tokens | **Correct** (both decisions cited with lineage/rationale) |

## What this does and doesn't show

**Shown, with real measurements:**
- Condition 1 (no access to MAK4I) never hallucinated across 3 tasks — it
  consistently declined rather than guessing. This is a small sample and
  not a guarantee, but it's the actual observed behavior.
- Conditions 2 and 3 both answered correctly across all 3 tasks.
- Condition 3 requires no prior knowledge of the fact from the
  operator/user — condition 2 only worked because the operator already
  knew and typed in the exact correct current answer, which is precisely
  the repeated-context burden MAK4I exists to remove. That's a real
  difference condition 2's smaller prompt size doesn't reflect.

**Explicitly not shown, and not claimed:**
- **MAK4I-selected context was not smaller than manual context in this
  run** — 880 tokens injected vs. 82–160 tokens of hand-written context in
  condition 2, because the unfiltered query returned the whole project's
  active knowledge rather than the one relevant artifact. A tag-filtered
  query (e.g. `tags=['caching']`) would very likely change this, but that
  wasn't the query shape measured here, so it isn't claimed. Whether
  task-relevant retrieval reduces context size in general is genuinely
  task- and query-shape-dependent and would need more runs across more
  query patterns to characterize — out of scope for this MVP-level
  measurement.
- No percentage, ratio, or general "MAK4I saves N% of tokens" claim is
  made anywhere in this document, per requirements §13.
