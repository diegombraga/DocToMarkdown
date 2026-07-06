# DocToMarkdown -- Windows installer.
#
# Fully rewritten in v0.4.2 after two field reports (Kayla + Nathalia)
# uncovered a cascade of Windows-specific breakage. Every string in this
# file uses ASCII only so PowerShell 5.1 parses it correctly even when
# read with the system ANSI codepage (fixes the 'Token unexpected /
# unterminated string' failures on non-en-US machines).
#
# What this script does, in order:
#   1. Install system deps via winget (Python 3.12, Tesseract, FFmpeg,
#      Git if missing), each with proper $LASTEXITCODE checks.
#   2. Resolve the *real* Python interpreter (skip Microsoft Store alias).
#   3. Ensure Portuguese Tesseract language data is available even when
#      the winget package skipped it, without needing admin rights.
#   4. Install Ghostscript from the Artifex release (no reliable winget
#      package) so ocrmypdf can produce PDF/A.
#   5. Copy the app source to %LOCALAPPDATA%\DocToMarkdown\src, avoiding
#      the self-copy bug of the earlier version.
#   6. Create the venv, run pip install, and pip install ocrmypdf.
#   7. Add both the CLI wrapper dir and the venv Scripts dir to the
#      user PATH so `markitdown`, `ocrmypdf` etc. resolve from any shell.
#   8. Drop a Start Menu shortcut pointing to pythonw.exe (no console).
#   9. Optionally install the Claude Code skill and register the MCP
#      server with Claude Desktop.
#  10. Boot the app once and probe /health so any missing dep is
#      reported immediately, not on first user click.

param(
    [switch]$Ci
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$CiMode = $Ci -or ($env:CI -eq "true") -or ($env:CI -eq "1")

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
$LogPath = Join-Path $env:TEMP "DocToMarkdown-install.log"
try {
    "" | Out-File -FilePath $LogPath -Encoding UTF8 -Force
} catch {}
function Log-Write ($line) {
    try { $line | Out-File -FilePath $LogPath -Encoding UTF8 -Append } catch {}
}
function Say-Log ($msg, $color) {
    Write-Host $msg -ForegroundColor $color
    Log-Write $msg
}
function Log  ($m) { Say-Log "-> $m" Cyan }
function Ok   ($m) { Say-Log "[ok] $m" Green }
function Warn ($m) { Say-Log "[!]  $m" Yellow }
function Die  ($m) {
    Say-Log "[X]  $m" Red
    Say-Log "Log de instalacao: $LogPath" Red
    exit 1
}

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
$RuntimeDir = Join-Path $env:LOCALAPPDATA "DocToMarkdown"
$BinDir     = Join-Path $env:LOCALAPPDATA "Programs\DocToMarkdown\bin"
$StartMenu  = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$LnkPath    = Join-Path $StartMenu "DocToMarkdown.lnk"
$TessData   = Join-Path $RuntimeDir "tessdata"

if ($PSVersionTable.Platform -eq "Unix") { Die "Este script eh apenas para Windows." }

Log "Instalacao DocToMarkdown -- log em: $LogPath"

# -----------------------------------------------------------------------------
# Locate repo source (must NOT be under $SrcDest -- avoids self-copy no-op)
# -----------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

$SrcDest = Join-Path $RuntimeDir "src"
if ($RepoRoot -ieq $SrcDest) {
    # Should never happen with the new install.ps1 wrapper, but guard anyway:
    # if the repo was cloned directly into the runtime dir, move it aside.
    $tempClone = Join-Path $env:TEMP ("DocToMarkdown-src-" + [System.Guid]::NewGuid().ToString("N"))
    Log "Repositorio esta dentro do runtime dir -- movendo para $tempClone"
    Move-Item -Path $RepoRoot -Destination $tempClone
    $RepoRoot = $tempClone
}

if (-not (Test-Path (Join-Path $RepoRoot "app.py"))) {
    Die "Codigo-fonte nao encontrado em $RepoRoot"
}

# -----------------------------------------------------------------------------
# winget dependencies
# -----------------------------------------------------------------------------
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Die "winget nao encontrado. Instale App Installer via Microsoft Store (requer Windows 10 21H1+)."
}

function Winget-Install ($id) {
    Log "winget install $id"
    $out = winget install --id $id `
        --accept-source-agreements --accept-package-agreements `
        --silent -e 2>&1
    $exit = $LASTEXITCODE
    $out | Out-String | Out-File -FilePath $LogPath -Encoding UTF8 -Append
    if ($exit -ne 0 -and $exit -ne -1978335189) {
        # -1978335189 = APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE (already installed)
        Warn "winget install $id retornou $exit (verifique $LogPath)"
    } else {
        Ok "$id ok"
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Winget-Install "Git.Git" }
Winget-Install "Python.Python.3.12"
Winget-Install "UB-Mannheim.TesseractOCR"
Winget-Install "Gyan.FFmpeg"

# Refresh PATH in the current session so newly installed tools are visible
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" `
          + [System.Environment]::GetEnvironmentVariable("Path","User")

# -----------------------------------------------------------------------------
# Real Python interpreter (skip Microsoft Store alias)
# -----------------------------------------------------------------------------
function Find-RealPython {
    # 1. Explicit winget install location
    $wingetPy = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $wingetPy) { return $wingetPy }

    # 2. `py -3.12` launcher, if installed
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $resolved = & py -3.12 -c "import sys, os; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved -and (Test-Path $resolved)) {
                # Only accept if it isn't the Store alias
                if ($resolved -notmatch "\\WindowsApps\\") { return $resolved }
            }
        } catch {}
    }

    # 3. Every `python` on PATH, filter out Store aliases
    $all = Get-Command python -All -ErrorAction SilentlyContinue
    foreach ($cmd in $all) {
        if ($cmd.Source -and $cmd.Source -notmatch "\\WindowsApps\\") {
            return $cmd.Source
        }
    }
    return $null
}

$PythonBin = Find-RealPython
if (-not $PythonBin) {
    Die "Nao encontrei um interpretador Python real (so o alias da Microsoft Store). Reinicie o terminal e rode de novo, ou instale Python 3.12 manualmente."
}
Ok "Python: $PythonBin"

# -----------------------------------------------------------------------------
# Copy source into runtime dir (from the safe $RepoRoot -- NEVER the same path)
# -----------------------------------------------------------------------------
Log "Preparando $RuntimeDir"
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $SrcDest    | Out-Null

foreach ($item in @('app.py','mcp_server.py','desktop_app.py','templates','static','video','requirements.txt')) {
    $s = Join-Path $RepoRoot $item
    $d = Join-Path $SrcDest $item
    if (-not (Test-Path $s)) { Warn "faltando no repo: $item"; continue }
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
    Copy-Item -Recurse -Force $s $d
}
Ok "Codigo copiado para $SrcDest"

# -----------------------------------------------------------------------------
# venv (tolerating benign stderr from `python -m venv` about redirects)
# -----------------------------------------------------------------------------
$VenvDir  = Join-Path $RuntimeDir ".venv"
$VenvPy   = Join-Path $VenvDir "Scripts\python.exe"
$VenvPyw  = Join-Path $VenvDir "Scripts\pythonw.exe"
$VenvPip  = Join-Path $VenvDir "Scripts\pip.exe"
$VenvBin  = Join-Path $VenvDir "Scripts"

if (-not (Test-Path $VenvPy)) {
    Log "Criando venv em $VenvDir"
    # $ErrorActionPreference="Stop" would abort on any stderr from native
    # commands. `python -m venv` prints a benign warning about junctions
    # even when it succeeds -- so we run it with stderr redirected to
    # stdout, check the exit code ourselves, and don't trip the trap.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonBin -m venv $VenvDir 2>&1 | Out-File -FilePath $LogPath -Encoding UTF8 -Append
    $venvExit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($venvExit -ne 0 -or -not (Test-Path $VenvPy)) {
        Die "python -m venv falhou (exit=$venvExit). Veja $LogPath"
    }
}

Log "Instalando pacotes Python (pode levar ~2 min na primeira vez)"
& $VenvPy -m pip install --quiet --upgrade pip *>> $LogPath
& $VenvPip install --quiet --upgrade -r (Join-Path $SrcDest "requirements.txt") *>> $LogPath
if ($LASTEXITCODE -ne 0) { Die "pip install falhou. Veja $LogPath" }
# ocrmypdf has proper Windows wheels via pip -- winget package isn't reliable
& $VenvPip install --quiet --upgrade ocrmypdf pikepdf *>> $LogPath
if ($LASTEXITCODE -ne 0) { Die "pip install ocrmypdf/pikepdf falhou. Veja $LogPath" }
Ok "Ambiente Python pronto"

# -----------------------------------------------------------------------------
# Tesseract language data (por + configs) in a user-writable location
# The UB-Mannheim silent install only ships English. We ship a private
# tessdata dir under %LOCALAPPDATA% and point TESSDATA_PREFIX at it so
# no admin rights are needed.
# -----------------------------------------------------------------------------
Log "Preparando tessdata em $TessData"
New-Item -ItemType Directory -Force -Path $TessData | Out-Null

# Seed from the system Tesseract install (has osd, eng, configs, tessconfigs)
$sysTess = "C:\Program Files\Tesseract-OCR\tessdata"
if (Test-Path $sysTess) {
    foreach ($f in Get-ChildItem $sysTess -Force) {
        $dest = Join-Path $TessData $f.Name
        if (-not (Test-Path $dest)) {
            Copy-Item -Recurse -Force -LiteralPath $f.FullName -Destination $dest
        }
    }
}

# por.traineddata -- fetch if missing
$porFile = Join-Path $TessData "por.traineddata"
if (-not (Test-Path $porFile)) {
    Log "Baixando por.traineddata (idioma portugues)"
    try {
        Invoke-WebRequest -UseBasicParsing `
            -Uri "https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata" `
            -OutFile $porFile
        Ok "por.traineddata pronto"
    } catch {
        Warn "Nao consegui baixar por.traineddata: $_"
    }
}

# Set TESSDATA_PREFIX for the user so any Tesseract invocation finds our dir
[System.Environment]::SetEnvironmentVariable("TESSDATA_PREFIX", $TessData, "User")
$env:TESSDATA_PREFIX = $TessData
Ok "TESSDATA_PREFIX = $TessData"

# -----------------------------------------------------------------------------
# Ghostscript (needed for ocrmypdf PDF/A output)
# No reliable winget package -- pull from the Artifex release directly.
# -----------------------------------------------------------------------------
$gsProbe = Get-Command gswin64c -ErrorAction SilentlyContinue
if (-not $gsProbe) {
    Log "Instalando Ghostscript (Artifex release)"
    try {
        $api = Invoke-RestMethod -UseBasicParsing `
            -Uri "https://api.github.com/repos/ArtifexSoftware/ghostpdl-downloads/releases/latest"
        $asset = $api.assets | Where-Object { $_.name -match "^gs\d+w64\.exe$" } | Select-Object -First 1
        if ($asset) {
            $gsInstaller = Join-Path $env:TEMP $asset.name
            Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $gsInstaller
            $gsProc = Start-Process -FilePath $gsInstaller `
                -ArgumentList "/S" -PassThru -Wait
            if ($gsProc.ExitCode -eq 0) {
                Ok "Ghostscript instalado"
            } else {
                Warn "Ghostscript installer exit=$($gsProc.ExitCode) -- OCR sem PDF/A pode falhar silenciosamente"
            }
            Remove-Item -Force $gsInstaller -ErrorAction SilentlyContinue
        } else {
            Warn "Ghostscript: nao encontrei asset no release mais recente"
        }
    } catch {
        Warn "Ghostscript: instalacao falhou ($_) -- OCR pode degradar silenciosamente"
    }
} else {
    Ok "Ghostscript ja instalado"
}

# -----------------------------------------------------------------------------
# CLI wrapper + PATH
# -----------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
@"
@echo off
"$VenvPy" "$SrcDest\app.py" %*
"@ | Set-Content -Path (Join-Path $BinDir "doc2md.cmd") -Encoding ASCII
Ok "CLI doc2md em $BinDir"

# Add BOTH the wrapper dir and the venv Scripts dir to user PATH
$UserPath = [System.Environment]::GetEnvironmentVariable("Path","User")
$toAdd = @()
if ($UserPath -notlike "*$BinDir*")  { $toAdd += $BinDir }
if ($UserPath -notlike "*$VenvBin*") { $toAdd += $VenvBin }
if ($toAdd.Count -gt 0) {
    $NewUserPath = ($UserPath, ($toAdd -join ";")) -join ";"
    [System.Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
    # Propagate to current session too
    $env:Path = "$env:Path;$($toAdd -join ';')"
    Ok "PATH atualizado (efeito imediato em terminais novos): $($toAdd -join '; ')"
}

# -----------------------------------------------------------------------------
# Start Menu shortcut (pythonw.exe = no console window)
# -----------------------------------------------------------------------------
Log "Criando atalho no Start Menu"
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($LnkPath)
$Shortcut.TargetPath       = $VenvPyw
$Shortcut.Arguments        = "`"$SrcDest\desktop_app.py`""
$Shortcut.WorkingDirectory = $SrcDest
$IconIco = Join-Path $RepoRoot "platform\windows\Resources\AppIcon.ico"
if (Test-Path $IconIco) { $Shortcut.IconLocation = $IconIco }
$Shortcut.Description      = "Convert any file to Markdown -- locally and free"
$Shortcut.Save()
Ok "Atalho: $LnkPath"

# -----------------------------------------------------------------------------
# Claude Code skill (optional)
# -----------------------------------------------------------------------------
$SkillSrc = Join-Path $RepoRoot "skill"
if (Test-Path $SkillSrc) {
    $SkillDest = Join-Path $env:USERPROFILE ".claude\skills\DocToMarkdown"
    $doIt = $CiMode
    if (-not $CiMode) {
        try {
            $ans = Read-Host "Instalar skill do Claude Code em ~\.claude\skills\? [Y/n]"
            if ($ans -eq "" -or $ans.ToLower() -eq "y") { $doIt = $true }
        } catch {
            # Non-interactive host: default to yes
            $doIt = $true
        }
    }
    if ($doIt) {
        New-Item -ItemType Directory -Force -Path $SkillDest | Out-Null
        Copy-Item -Recurse -Force (Join-Path $SkillSrc "*") $SkillDest
        Ok "Skill instalada em $SkillDest"
    }
}

# -----------------------------------------------------------------------------
# MCP server registration (Claude Desktop + best-effort Claude Code)
# -----------------------------------------------------------------------------
$McpDoIt = $CiMode
if (-not $CiMode) {
    try {
        $ansMcp = Read-Host "Registrar servidor MCP no Claude Desktop? [Y/n]"
        if ($ansMcp -eq "" -or $ansMcp.ToLower() -eq "y") { $McpDoIt = $true }
    } catch {
        $McpDoIt = $true
    }
}
if ($McpDoIt) {
    Log "Registrando MCP no Claude Desktop"
    try {
        & $VenvPy (Join-Path $SrcDest "mcp_server.py") "--install-claude-desktop" *>> $LogPath
        if ($LASTEXITCODE -eq 0) {
            Ok "MCP registrado (reinicie o Claude Desktop)"
        } else {
            Warn "MCP install retornou $LASTEXITCODE (veja $LogPath)"
        }
    } catch {
        Warn "MCP install falhou: $_"
    }

    # Best-effort: try `claude mcp add` for Claude Code if the CLI is present
    $claudeCli = Get-Command claude -ErrorAction SilentlyContinue
    if ($claudeCli) {
        Log "Registrando MCP no Claude Code CLI"
        try {
            & claude mcp add doctomarkdown -- $VenvPy (Join-Path $SrcDest "mcp_server.py") *>> $LogPath
            if ($LASTEXITCODE -eq 0) { Ok "MCP tambem registrado no Claude Code" }
        } catch { Warn "claude mcp add falhou (nao critico): $_" }
    }
}

# -----------------------------------------------------------------------------
# Post-install health check
# -----------------------------------------------------------------------------
Log "Verificando instalacao (subindo o app e chamando /health)"
$proc = Start-Process -FilePath $VenvPyw `
    -ArgumentList "`"$SrcDest\app.py`"" -PassThru -WindowStyle Hidden
try {
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5555/health -TimeoutSec 2
            if ($r.StatusCode -eq 200) {
                $ok = $true
                $health = $r.Content | ConvertFrom-Json
                Ok "app respondeu /health"
                foreach ($k in @('markitdown','ocrmypdf','ffmpeg','tesseract')) {
                    if ($health.$k) { Ok "  $k : OK" }
                    else            { Warn "  $k : FALTANDO" }
                }
                break
            }
        } catch {}
    }
    if (-not $ok) {
        Warn "app nao respondeu em 30s -- veja $LogPath e tente rodar manualmente"
    }
} finally {
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
}

Write-Host ""
Ok "Tudo pronto."
Write-Host "   Start Menu:  procure 'DocToMarkdown'"
Write-Host "   Terminal:    doc2md arquivo.pdf   (abra um terminal novo)"
Write-Host "   Log:         $LogPath"
Write-Host ""

if (-not $CiMode) {
    Start-Process $LnkPath | Out-Null
}
