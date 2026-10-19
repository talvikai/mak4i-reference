#!/usr/bin/env bash
# Checks the Claude Code connection commands of docs/LOCAL_SETUP.md
# (3.2: by URL with the credential header; 3.4: Claude Code starts MAK4I)
# in an isolated Claude Code configuration (CLAUDE_CONFIG_DIR). Needs the
# `claude` CLI and an initialized checkout (run after local_smoke.sh).
set -euo pipefail
export CLAUDE_CONFIG_DIR="$PWD/claude-ci-config"
mkdir -p "$CLAUDE_CONFIG_DIR" "${RUNNER_TEMP:-/tmp}/another-project"
fail() { echo "FAIL: $*"; exit 1; }
status() { (cd "${RUNNER_TEMP:-/tmp}/another-project" && claude mcp get mak4i 2>&1) | tee status.txt; }

echo "== 3.2: by URL, with the MAK4I server running"
.venv/bin/mak4i serve --transport http >server.log 2>&1 &
server=$!
for _ in $(seq 60); do curl -fsS http://127.0.0.1:9090/ready >/dev/null 2>&1 && break; sleep 1; done
claude mcp add --transport http -s user mak4i http://127.0.0.1:9090/mcp --header "Authorization: Bearer $(.venv/bin/python -c 'import json; print(json.load(open(".mak4i/credentials.json"))["token"])')"
status; grep -q Connected status.txt || fail "3.2 connection"; grep -q "User config" status.txt || fail "3.2 scope"
kill "$server"; wait "$server" 2>/dev/null || true
claude mcp remove -s user mak4i

echo "== 3.4: Claude Code starts MAK4I (no server running)"
claude mcp add -s user mak4i -- sh -c "cd \"$PWD\" && exec .venv/bin/mak4i serve"
status; grep -q Connected status.txt || fail "3.4 connection"
claude mcp remove -s user mak4i

echo "OK: Claude Code registration commands work"
