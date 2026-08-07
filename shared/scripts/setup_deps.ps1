# BrandDeck AI dependency installer for Windows PowerShell
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

Write-Host "=== BrandDeck AI: dependency setup ===" -ForegroundColor Cyan

$Python = $null
foreach ($cmd in @("python", "python3")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $Python = $cmd
        break
    }
}
if (-not $Python) {
    Write-Host "ERROR: Python 3.10+ is required." -ForegroundColor Red
    exit 1
}

Write-Host "Python: $(& $Python --version)"
& $Python -m pip install -e "${ProjectRoot}[api,documents]"
if ($LASTEXITCODE -ne 0) { exit 3 }

& $Python -m slide_agent --json doctor
if ($LASTEXITCODE -ne 0) { exit 4 }

Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "Node.js is optional and only required for the legacy PPTXGenJS runner."
