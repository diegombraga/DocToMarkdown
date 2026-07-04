# Windows installer for DocToMarkdown.
# - Installs system deps via winget (Python 3.12, Tesseract, FFmpeg)
# - Installs ocrmypdf via pip (has Windows wheels)
# - Copies source into %LOCALAPPDATA%\DocToMarkdown\src\
# - Creates isolated venv + installs Python deps
# - Places .lnk in Start Menu (+ Desktop optionally)
# - Adds doc2md.cmd to a user PATH dir
# - Optionally installs the Claude Code skill
# Idempotent: safe to re-run.

param(
    [switch]$Ci
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$CiMode = $Ci -or ($env:CI -eq "true") -or ($env:CI -eq "1")

$RuntimeDir  = Join-Path $env:LOCALAPPDATA "DocToMarkdown"
$BinDir      = Join-Path $env:LOCALAPPDATA "Programs\DocToMarkdown\bin"
$StartMenu   = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$LnkPath     = Join-Path $StartMenu "DocToMarkdown.lnk"

function Log  ($m) { Write-Host "→ $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "✓ $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "! $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "✗ $m" -ForegroundColor Red; exit 1 }

if ($PSVersionTable.Platform -eq "Unix") { Die "Este script é somente para Windows." }

# Locate repo — if this script's parent parent has app.py, we're running from a clone.
# Otherwise download the repo.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir "..\..") -ErrorAction SilentlyContinue
if (-not $RepoRoot -or -not (Test-Path (Join-Path $RepoRoot "app.py"))) {
    Log "Baixando DocToMarkdown para $RuntimeDir\src …"
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    if (Test-Path (Join-Path $RuntimeDir "src\.git")) {
        git -C (Join-Path $RuntimeDir "src") pull --ff-only
    } else {
        if (Test-Path (Join-Path $RuntimeDir "src")) { Remove-Item -Recurse -Force (Join-Path $RuntimeDir "src") }
        git clone --depth 1 https://github.com/diegombraga/DocToMarkdown (Join-Path $RuntimeDir "src")
    }
    $RepoRoot = Join-Path $RuntimeDir "src"
}

Log "Instalando DocToMarkdown para Windows"

# ---------- winget ----------
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Die "winget não encontrado. Requer Windows 10 21H1+ ou instale App Installer via Microsoft Store."
}

# ---------- System deps ----------
$WingetArgs = @('--accept-source-agreements','--accept-package-agreements','--silent','-e')
Log "Instalando Python 3.12…"
winget install --id Python.Python.3.12 @WingetArgs *> $null
Log "Instalando Tesseract OCR (UB-Mannheim build, inclui idiomas)…"
winget install --id UB-Mannheim.TesseractOCR @WingetArgs *> $null
Log "Instalando FFmpeg…"
winget install --id Gyan.FFmpeg @WingetArgs *> $null
Ok "Dependências de sistema prontas"

# Refresh PATH in current session
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# Locate Python (prefer freshly installed 3.12)
$PythonBin = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonBin) { $PythonBin = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $PythonBin) { Die "Python não achado no PATH mesmo após instalação. Reinicie o terminal e rode de novo." }

# ---------- Runtime + source ----------
Log "Preparando $RuntimeDir"
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
$SrcDest = Join-Path $RuntimeDir "src"
New-Item -ItemType Directory -Force -Path $SrcDest | Out-Null
foreach ($item in @('app.py','templates','static','video','requirements.txt')) {
    $s = Join-Path $RepoRoot $item
    $d = Join-Path $SrcDest $item
    if (Test-Path $s) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d }
        Copy-Item -Recurse -Force $s $d
    }
}

# ---------- venv ----------
$VenvDir = Join-Path $RuntimeDir ".venv"
if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    Log "Criando venv em $VenvDir"
    & $PythonBin -m venv $VenvDir
}
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"
$VenvPyw = Join-Path $VenvDir "Scripts\pythonw.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

Log "Instalando pacotes Python (pode demorar ~1 min)…"
& $VenvPy -m pip install --upgrade pip *> $null
& $VenvPip install --upgrade -r (Join-Path $SrcDest "requirements.txt") *> $null
# ocrmypdf via pip (has Windows wheels — winget package doesn't exist reliably)
& $VenvPip install --upgrade ocrmypdf *> $null
Ok "Ambiente Python pronto"

# ---------- CLI wrapper ----------
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
@"
@echo off
"$VenvPy" "$SrcDest\app.py" %*
"@ | Set-Content -Path (Join-Path $BinDir "doc2md.cmd") -Encoding ASCII
Ok "CLI 'doc2md' criado em $BinDir"

# Add to user PATH if missing
$UserPath = [System.Environment]::GetEnvironmentVariable("Path","User")
if ($UserPath -notlike "*$BinDir*") {
    [System.Environment]::SetEnvironmentVariable("Path", "$UserPath;$BinDir", "User")
    Ok "$BinDir adicionado ao PATH (efeito após próximo terminal)"
}

# ---------- Start Menu shortcut ----------
Log "Criando atalho no Start Menu…"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($LnkPath)
$Shortcut.TargetPath       = $VenvPyw   # pythonw = no console window
$Shortcut.Arguments        = "`"$SrcDest\app.py`""
$Shortcut.WorkingDirectory = $SrcDest
$Shortcut.IconLocation     = Join-Path $RepoRoot "platform\windows\Resources\AppIcon.ico"
$Shortcut.Description      = "Convert any file to Markdown — locally and free"
$Shortcut.Save()
Ok "Atalho criado: $LnkPath"

# ---------- Skill (opcional) ----------
$SkillSrc = Join-Path $RepoRoot "skill"
if (Test-Path $SkillSrc) {
    $SkillDest = Join-Path $env:USERPROFILE ".claude\skills\DocToMarkdown"
    $doIt = $CiMode
    if (-not $CiMode) {
        $ans = Read-Host "Instalar skill do Claude Code em ~/.claude/skills/? [Y/n]"
        if ($ans -eq "" -or $ans.ToLower() -eq "y") { $doIt = $true }
    }
    if ($doIt) {
        New-Item -ItemType Directory -Force -Path $SkillDest | Out-Null
        Copy-Item -Recurse -Force (Join-Path $SkillSrc "*") $SkillDest
        Ok "Skill instalada em $SkillDest"
    }
}

Write-Host ""
Ok "Tudo pronto. Para abrir a interface:"
Write-Host "   • Start Menu:  procure 'DocToMarkdown'"
Write-Host "   • Terminal:    doc2md arquivo.pdf  (num terminal novo)"
Write-Host ""

if (-not $CiMode) {
    Start-Process $LnkPath | Out-Null
}
