# DocToMarkdown -- Windows installer wrapper.
#
# Two supported invocation modes:
#   1. Direct: `iwr -useb https://.../install.ps1 | iex`
#      Downloads the repo to a temp dir and runs platform/windows/install.ps1.
#   2. Cloned: `.\install.ps1` inside a working clone of the repo.
#      Detects the sibling platform script and runs it in place.
#
# Kept intentionally free of non-ASCII characters so PowerShell 5.1 with
# an ANSI codepage parses it correctly even when the file was saved
# UTF-8 without BOM.

param(
    [switch]$Ci
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$ScriptDir = $null
if ($MyInvocation.MyCommand.Path) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
} elseif ($MyInvocation.MyCommand.Definition -and (Test-Path $MyInvocation.MyCommand.Definition -ErrorAction SilentlyContinue)) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
}

$PlatformScript = $null
if ($ScriptDir) {
    $candidate = Join-Path $ScriptDir "platform\windows\install.ps1"
    if (Test-Path $candidate) { $PlatformScript = $candidate }
}

if (-not $PlatformScript) {
    $Tmp = Join-Path $env:TEMP ("DocToMarkdown-" + [System.Guid]::NewGuid().ToString("N"))
    Write-Host "-> Downloading DocToMarkdown to $Tmp" -ForegroundColor Cyan
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "[X] git is required to clone the project." -ForegroundColor Red
        Write-Host "    Install with: winget install Git.Git" -ForegroundColor Red
        exit 1
    }
    & git clone --depth 1 https://github.com/diegombraga/DocToMarkdown $Tmp
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] git clone failed." -ForegroundColor Red
        exit 1
    }
    $PlatformScript = Join-Path $Tmp "platform\windows\install.ps1"
}

$argsList = @()
if ($Ci) { $argsList += "-Ci" }
& $PlatformScript @argsList
exit $LASTEXITCODE
