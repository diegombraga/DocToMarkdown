# Remove DocToMarkdown files on Windows. Does NOT remove winget-installed deps.

$ErrorActionPreference = "SilentlyContinue"

$RuntimeDir = Join-Path $env:LOCALAPPDATA "DocToMarkdown"
$BinDir     = Join-Path $env:LOCALAPPDATA "Programs\DocToMarkdown"
$Lnk        = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\DocToMarkdown.lnk"
$SkillDir   = Join-Path $env:USERPROFILE ".claude\skills\DocToMarkdown"

Write-Host "→ Removendo arquivos do DocToMarkdown…" -ForegroundColor Cyan
Remove-Item -Recurse -Force $RuntimeDir
Remove-Item -Recurse -Force $BinDir
Remove-Item -Force $Lnk

# Remove PATH entry
$binPath = Join-Path $BinDir "bin"
$UserPath = [System.Environment]::GetEnvironmentVariable("Path","User")
if ($UserPath -like "*$binPath*") {
    $NewPath = ($UserPath -split ';' | Where-Object { $_ -ne $binPath }) -join ';'
    [System.Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
}

if (Test-Path $SkillDir) {
    $ans = Read-Host "Remover também a skill do Claude Code? [y/N]"
    if ($ans.ToLower() -eq "y") {
        Remove-Item -Recurse -Force $SkillDir
        Write-Host "✓ skill removida" -ForegroundColor Green
    }
}

Write-Host "✓ DocToMarkdown removido. Deps do winget foram mantidas." -ForegroundColor Green
