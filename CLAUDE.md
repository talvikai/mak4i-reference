# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## What this repo is

The MAK4I reference implementation: a small, technology-agnostic engine that
lets durable project knowledge (architecture decisions, documentation facts,
implementation state) created or changed by one connected AI client become
discoverable and usable by another, without the user repeating themselves.
See `docs/MVP_ARCHITECTURE.md` for the design and
<https://github.com/talvikai/mak4i-protocol> for the MAK4I protocol/spec.

## Core rule: keep the engine technology-agnostic

`src/mak4i/models/`, `store/`, `discovery/`, `resolution/`, `context/`, and
`audit/` must contain **no scenario-specific logic** — no `if artifact_type ==
"database_decision"` branches, no field named after a technology, no
discovery/resolution rule that only fires for a particular `tags` value. Any
two artifacts of the same shape (a Redis caching decision, a MySQL database
decision, a plain documentation fact) must pass through identical code. If a
change only makes sense for one demo scenario, it does not belong in the core.

## Before architecture-sensitive implementation work

Call `mak4i_get_current` (via the MCP server — local stdio or a deployed
HTTP endpoint) before writing or changing code that depends on a
project-level architecture decision. If what you find in the repository
(a connection string, an ORM dialect, a config value, a dependency) disagrees
with the current MAK4I artifact, **say so explicitly in your output** —
surface the discrepancy and ask how to proceed. Do not silently assume the
repo is authoritative, and do not silently trust MAK4I over visible code
without flagging the mismatch.

## Do not leak the dynamic fact under test

This repo's own instructions (this file) must stay stable and must never
contain the actual current value of a demo decision (e.g. which cache or
database is currently chosen). That value must always come from a live
`mak4i_get_current`/`mak4i_history` call, not from anything committed here —
otherwise a cross-client demo (Claude.ai writes, ChatGPT reads) would be
proving nothing.

## Engine boundary

The MAK4I engine (`src/mak4i/api.py`) must remain callable directly by tests
and the CLI without going through MCP. Add MCP tool wiring in
`src/mak4i/mcp_server.py` only as a thin adapter over that engine — do not put
protocol logic in the MCP layer.

## Testing discipline

Every resolver decision must be explainable in audit output. When adding a
feature that touches discovery, resolution, or supersession, add a test using
at least two distinct artifact types (e.g. a caching artifact and a database
artifact) exercising the same code path, not just one demo technology.
