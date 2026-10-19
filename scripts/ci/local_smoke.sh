#!/usr/bin/env bash
# Runs the macOS/Linux commands of docs/LOCAL_SETUP.md Part 1 on a CI
# runner, from an installed checkout (uv sync --no-editable already run):
# init, start the MAK4I server, a real MCP session, the one-server guard,
# and a restart after the server is killed.
set -euo pipefail
base=http://127.0.0.1:9090

fail() { echo "FAIL: $*"; exit 1; }
wait_ready() {
  for _ in $(seq 60); do
    curl -fsS "$base/ready" >/dev/null 2>&1 && return 0
    sleep 1
  done
  cat server.log || true
  fail "the MAK4I server never became ready"
}
mcp() { # mcp <session-or-empty> <json body>
  local h=()
  [[ -n "$1" ]] && h=(-H "Mcp-Session-Id: $1")
  curl -sS -D headers.txt ${h[@]+"${h[@]}"} -H "Authorization: Bearer $token" \
    -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
    -d "$2" "$base/mcp"
}

echo "== mak4i --version"; .venv/bin/mak4i --version
echo "== mak4i init"
.venv/bin/mak4i init --org-name "CI Org" --display-name "CI User" --project-name "CI Project" --http-port 9090

echo "== start the MAK4I server (3.1)"
.venv/bin/mak4i serve --transport http >server.log 2>&1 &
server=$!
wait_ready

echo "== a real MCP session with the local credential"
token=$(.venv/bin/python -c 'import json; print(json.load(open(".mak4i/credentials.json"))["token"])')
project=$(.venv/bin/python -c 'import json; print(json.load(open(".mak4i/config.json"))["project_id"])')
mcp "" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ci","version":"0"}}}' >/dev/null
session=$(tr -d '\r' <headers.txt | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')
[[ -n "$session" ]] || fail "no MCP session id"
mcp "$session" '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
mcp "$session" '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mak4i_whoami","arguments":{}}}' | grep -q 'MAK4I local (CI Org)' || fail whoami
mcp "$session" '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"mak4i_create","arguments":{"project":"'"$project"'","artifact_id":"db-choice","artifact_type":"database_decision","title":"Database","content":"Our app uses PostgreSQL as its database."}}}' | grep -q '"isError": *true' && fail create
mcp "$session" '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"mak4i_get_current","arguments":{"project":"'"$project"'"}}}' | grep -q PostgreSQL || fail get_current

echo "== a second server is refused"
set +e
.venv/bin/mak4i serve --transport http --port 9091 >second.log 2>&1
code=$?
set -e
[[ $code == 2 ]] || { cat second.log; fail "second server exit code $code"; }
grep -q "already running for this installation" second.log || { cat second.log; fail "no guard message"; }

echo "== kill the server; a new one starts (the lock went with the process)"
kill -9 "$server"; wait "$server" 2>/dev/null || true
.venv/bin/mak4i serve --transport http >server.log 2>&1 &
server=$!
wait_ready
kill "$server"; wait "$server" 2>/dev/null || true

echo "== sign-in turned on (4.2): server and oauth commands with the same two settings"
oauth=(MAK4I_OAUTH_ENABLED=1 MAK4I_PUBLIC_ENDPOINT=https://mak4i.example.com/mcp)
env "${oauth[@]}" .venv/bin/mak4i serve --transport http >server.log 2>&1 &
server=$!
wait_ready
curl -fsS -H 'Host: 127.0.0.1:9090' "$base/.well-known/oauth-protected-resource/mcp" | grep -q '"resource":"https://mak4i.example.com/mcp"' || fail discovery
env "${oauth[@]}" .venv/bin/mak4i oauth client register --name "Claude (claude.ai)" --redirect-uri https://claude.ai/api/mcp/auth_callback 2>/dev/null | grep -q 'client_id: mak4c_' || fail "client register"
env "${oauth[@]}" .venv/bin/mak4i oauth sign-in-code 2>/dev/null | grep -q 'Sign-in code created' || fail sign-in-code
kill "$server"; wait "$server" 2>/dev/null || true
out=$(.venv/bin/mak4i oauth sign-in-code 2>&1 || true)
grep -q 'OAuth is not enabled' <<<"$out" || fail "sign-in-code without the settings should fail"

echo "== back up (7, Step 4) and undo (restore the backup)"
backup=~/mak4i-backup-$(date +%Y-%m-%d-%H%M)
cp -R .mak4i "$backup"
ls "$backup" | grep -q control-plane.db || fail "backup contents"
mv .mak4i ~/mak4i-after-upgrade-$(date +%Y-%m-%d-%H%M)
cp -R "$backup" .mak4i
.venv/bin/mak4i serve --transport http >server.log 2>&1 &
server=$!
wait_ready
mcp "" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ci","version":"0"}}}' >/dev/null
session=$(tr -d '\r' <headers.txt | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')
mcp "$session" '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
mcp "$session" '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"mak4i_get_current","arguments":{"project":"'"$project"'"}}}' | grep -q PostgreSQL || fail "restored data"
kill "$server"; wait "$server" 2>/dev/null || true

echo "OK: Local smoke test passed"
