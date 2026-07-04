# DocToMarkdown — Windows installer wrapper.
# Delegates to platform/windows/install.ps1.
# For macOS/Linux use install.sh.

param(
    [switch]$Ci
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PlatformScript = Join-Path $ScriptDir "platform\windows\install.ps1"

if (-not (Test-Path $PlatformScript)) {
    # Being invoked via `iwr | iex` — need to fetch the repo first
    $Tmp = Join-Path $env:TEMP ("DocToMarkdown-" + [System.Guid]::NewGuid().ToString("N"))
    Write-Host "→ Baixando DocToMarkdown para $Tmp …" -ForegroundColor Cyan
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "✗ Preciso do 'git' pra clonar o projeto. Instale (winget install Git.Git) e rode de novo." -ForegroundColor Red
        exit 1
    }
    & git clone --depth 1 https://github.com/diegombraga/DocToMarkdown $Tmp
    $PlatformScript = Join-Path $Tmp "platform\windows\install.ps1"
}

$argsList = @()
if ($Ci) { $argsList += "-Ci" }
& $PlatformScript @argsList
