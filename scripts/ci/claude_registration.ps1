# Checks the Claude Code connection commands of docs/LOCAL_SETUP.md
# (3.2: by URL with the credential header; 3.4: Claude Code starts MAK4I)
# in an isolated Claude Code configuration (CLAUDE_CONFIG_DIR). Needs the
# `claude` CLI and an initialized checkout (run after local_smoke.ps1).
$ErrorActionPreference = "Stop"
$mak4i = ".\.venv\Scripts\mak4i.exe"
$repo = (Get-Location).Path
$env:CLAUDE_CONFIG_DIR = Join-Path $repo "claude-ci-config"
New-Item -ItemType Directory -Force $env:CLAUDE_CONFIG_DIR | Out-Null
$elsewhere = Join-Path $env:TEMP "another-project"
New-Item -ItemType Directory -Force $elsewhere | Out-Null

function Fail([string]$message) {
    Write-Host "FAIL: $message"
    exit 1
}

function Get-Status {
    Push-Location $elsewhere
    try { $out = (claude mcp get mak4i 2>&1 | Out-String) } finally { Pop-Location }
    Write-Host $out
    return $out
}

Write-Host "== 3.2: by URL, with the MAK4I server running"
$server = Start-Process -FilePath $mak4i -ArgumentList "serve", "--transport", "http" -PassThru -NoNewWindow `
    -RedirectStandardOutput server.out -RedirectStandardError server.err
for ($i = 0; $i -lt 60; $i++) {
    try { if ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:9090/ready" -TimeoutSec 2).StatusCode -eq 200) { break } } catch { }
    Start-Sleep -Seconds 1
}
claude mcp add --transport http -s user mak4i http://127.0.0.1:9090/mcp --header "Authorization: Bearer $((Get-Content .mak4i\credentials.json | ConvertFrom-Json).token)"
$out = Get-Status
if ($out -notmatch "Connected") { Fail "3.2 connection" }
if ($out -notmatch "User config") { Fail "3.2 scope" }
Stop-Process -Id $server.Id -Force
$server.WaitForExit()
claude mcp remove -s user mak4i

Write-Host "== 3.4: Claude Code starts MAK4I (no server running)"
claude mcp add -s user mak4i -- cmd /c "cd /d `"$PWD`" && .venv\Scripts\mak4i.exe serve"
$out = Get-Status
if ($out -notmatch "Connected") { Fail "3.4 connection" }
claude mcp remove -s user mak4i

Write-Host "OK: Claude Code registration commands work on Windows"
