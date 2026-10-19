# Runs the Windows (PowerShell) commands of docs/LOCAL_SETUP.md Part 1 on a
# CI runner, from an installed checkout (uv sync --no-editable already run):
# init, start the MAK4I server, a real MCP session, the one-server guard,
# and a restart after the server is killed. Written for Windows PowerShell
# 5.1 (the default shell), so no `&&` between PowerShell commands.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ci/local_smoke.ps1
$ErrorActionPreference = "Stop"
$mak4i = ".\.venv\Scripts\mak4i.exe"
$base = "http://127.0.0.1:9090"

function Fail([string]$message) {
    Write-Host "FAIL: $message"
    exit 1
}

function Wait-Ready {
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing "$base/ready" -TimeoutSec 2
            if ($r.StatusCode -eq 200) { return }
        } catch { }
        Start-Sleep -Seconds 1
    }
    Get-Content server.out, server.err -ErrorAction SilentlyContinue
    Fail "the MAK4I server never became ready"
}

function Start-Server {
    Start-Process -FilePath $mak4i -ArgumentList "serve", "--transport", "http" -PassThru -NoNewWindow `
        -RedirectStandardOutput server.out -RedirectStandardError server.err
}

function Invoke-Mak4i([string[]]$arguments) {
    # Windows PowerShell 5.1 turns a native command's redirected stderr into
    # errors, which "Stop" would throw on; MAK4I's CLI writes audit events
    # to stderr, so collect both streams with errors not stopping the script.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return (& $mak4i @arguments 2>&1 | Out-String) } finally { $ErrorActionPreference = $previous }
}

function Invoke-Mcp([string]$session, [string]$body) {
    $headers = @{ Authorization = "Bearer $script:token"; Accept = "application/json, text/event-stream" }
    if ($session) { $headers["Mcp-Session-Id"] = $session }
    Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$base/mcp" -Headers $headers -ContentType "application/json" -Body $body
}

Write-Host "== mak4i --version"
& $mak4i --version
if ($LASTEXITCODE -ne 0) { Fail "mak4i --version" }

Write-Host "== mak4i init"
& $mak4i init --org-name "CI Org" --display-name "CI User" --project-name "CI Project" --http-port 9090
if ($LASTEXITCODE -ne 0) { Fail "mak4i init" }

Write-Host "== start the MAK4I server (3.1)"
$server = Start-Server
Wait-Ready

Write-Host "== a real MCP session with the local credential"
$script:token = (Get-Content .mak4i\credentials.json | ConvertFrom-Json).token
$project = (Get-Content .mak4i\config.json | ConvertFrom-Json).project_id
$r = Invoke-Mcp "" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ci","version":"0"}}}'
$session = @($r.Headers["Mcp-Session-Id"])[0]
if (-not $session) { Fail "no MCP session id" }
Invoke-Mcp $session '{"jsonrpc":"2.0","method":"notifications/initialized"}' | Out-Null
$r = Invoke-Mcp $session '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mak4i_whoami","arguments":{}}}'
if ($r.Content -notmatch "MAK4I local \(CI Org\)") { Fail "whoami: $($r.Content)" }
$create = '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"mak4i_create","arguments":{"project":"' + $project + '","artifact_id":"db-choice","artifact_type":"database_decision","title":"Database","content":"Our app uses PostgreSQL as its database."}}}'
$r = Invoke-Mcp $session $create
if ($r.Content -match '"isError"\s*:\s*true') { Fail "create: $($r.Content)" }
$r = Invoke-Mcp $session ('{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"mak4i_get_current","arguments":{"project":"' + $project + '"}}}')
if ($r.Content -notmatch "PostgreSQL") { Fail "get_current: $($r.Content)" }

Write-Host "== a second server is refused"
$second = Start-Process -FilePath $mak4i -ArgumentList "serve", "--transport", "http", "--port", "9091" -PassThru -NoNewWindow -Wait `
    -RedirectStandardOutput second.out -RedirectStandardError second.err
if ($second.ExitCode -ne 2) { Get-Content second.out, second.err; Fail "second server exit code $($second.ExitCode)" }
if (-not (Select-String -Path second.err -Pattern "already running for this installation" -Quiet)) { Get-Content second.err; Fail "no guard message" }

Write-Host "== kill the server; a new one starts (the lock went with the process)"
Stop-Process -Id $server.Id -Force
$server.WaitForExit()
$server = Start-Server
Wait-Ready
Stop-Process -Id $server.Id -Force
$server.WaitForExit()

Write-Host "== sign-in turned on (4.2): server and oauth commands with the same two settings"
$env:MAK4I_OAUTH_ENABLED = "1"
$env:MAK4I_PUBLIC_ENDPOINT = "https://mak4i.example.com/mcp"
$server = Start-Server
Wait-Ready
$prm = Invoke-WebRequest -UseBasicParsing "$base/.well-known/oauth-protected-resource/mcp"
if ($prm.Content -notmatch '"resource":"https://mak4i.example.com/mcp"') { Fail "discovery: $($prm.Content)" }
$out = Invoke-Mak4i @("oauth", "client", "register", "--name", "Claude (claude.ai)", "--redirect-uri", "https://claude.ai/api/mcp/auth_callback")
if ($out -notmatch "client_id: mak4c_") { Fail "client register" }
$out = Invoke-Mak4i @("oauth", "sign-in-code")
if ($out -notmatch "Sign-in code created") { Fail "sign-in-code" }
Stop-Process -Id $server.Id -Force
$server.WaitForExit()
Remove-Item Env:MAK4I_OAUTH_ENABLED, Env:MAK4I_PUBLIC_ENDPOINT
$out = Invoke-Mak4i @("oauth", "sign-in-code")
if ($out -notmatch "OAuth is not enabled") { Fail "sign-in-code without the settings should fail" }

Write-Host "== back up (7, Step 4) and undo (restore the backup)"
$backup = "$HOME\mak4i-backup-$(Get-Date -Format yyyy-MM-dd-HHmm)"
Copy-Item -Recurse .\.mak4i $backup
$listing = (Get-ChildItem $backup | ForEach-Object Name) -join ","
if ($listing -notmatch "control-plane.db" -or $listing -notmatch "credentials.json") { Fail "backup contents: $listing" }
Move-Item .\.mak4i "$HOME\mak4i-after-upgrade-$(Get-Date -Format yyyy-MM-dd-HHmm)"
Copy-Item -Recurse $backup .\.mak4i
$server = Start-Server
Wait-Ready
$r = Invoke-Mcp "" '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"ci","version":"0"}}}'
$session = @($r.Headers["Mcp-Session-Id"])[0]
Invoke-Mcp $session '{"jsonrpc":"2.0","method":"notifications/initialized"}' | Out-Null
$r = Invoke-Mcp $session ('{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"mak4i_get_current","arguments":{"project":"' + $project + '"}}}')
if ($r.Content -notmatch "PostgreSQL") { Fail "restored data: $($r.Content)" }
Stop-Process -Id $server.Id -Force
$server.WaitForExit()

Write-Host "OK: Windows Local smoke test passed"
