#!/usr/bin/env bash
# Remove DocToMarkdown files. Does NOT remove system deps (brew/apt packages).

set -euo pipefail

UNAME="$(uname -s)"

log() { printf "→ %s\n" "$*"; }
ok()  { printf "✓ %s\n" "$*"; }

case "$UNAME" in
  Darwin)
    RUNTIME_DIR="$HOME/Library/Application Support/DocToMarkdown"
    APP_PATH="/Applications/DocToMarkdown.app"
    ;;
  Linux)
    RUNTIME_DIR="$HOME/.local/share/DocToMarkdown"
    DESKTOP_FILE="$HOME/.local/share/applications/DocToMarkdown.desktop"
    ;;
  *)
    echo "✗ SO não suportado: $UNAME" >&2
    exit 1
    ;;
esac

BIN_FILE="$HOME/.local/bin/doc2md"
SKILL_DIR="$HOME/.claude/skills/DocToMarkdown"

log "Removendo arquivos do DocToMarkdown…"
[[ -n "${APP_PATH:-}" ]] && rm -rf "$APP_PATH" 2>/dev/null || true
[[ -n "${DESKTOP_FILE:-}" ]] && rm -f "$DESKTOP_FILE" 2>/dev/null || true
rm -rf "$RUNTIME_DIR"
rm -f "$BIN_FILE"
if [[ -d "$SKILL_DIR" ]]; then
  read -r -p "Remover também a skill do Claude Code em $SKILL_DIR? [y/N] " ans
  [[ "${ans,,}" == "y" ]] && rm -rf "$SKILL_DIR" && ok "skill removida"
fi

# Icons (Linux)
if [[ "$UNAME" == "Linux" ]]; then
  for size in 16 32 64 128 256 512 1024; do
    rm -f "$HOME/.local/share/icons/hicolor/${size}x${size}/apps/doctomarkdown.png"
  done
fi

ok "DocToMarkdown removido. Deps de sistema (ocrmypdf/tesseract/ffmpeg) foram mantidas."
