#!/usr/bin/env bash
# macOS installer for DocToMarkdown.
# - Installs Homebrew if missing
# - Installs system deps (python@3.12, ocrmypdf, tesseract-lang, ffmpeg)
# - Copies source into ~/Library/Application Support/DocToMarkdown/src/
# - Creates isolated venv + installs Python deps
# - Builds and installs /Applications/DocToMarkdown.app
# - Optionally installs the Claude Code skill
# Idempotent: safe to re-run to update.

set -euo pipefail

CI_MODE="${CI:-}"
if [[ "${1:-}" == "--ci" ]]; then CI_MODE=1; fi

RUNTIME_DIR="$HOME/Library/Application Support/DocToMarkdown"
APP_PATH="/Applications/DocToMarkdown.app"
BIN_DIR="$HOME/.local/bin"

# Locate the repo — either the dir this script lives in, or the current working
# directory if invoked via `curl | bash` (we then clone the repo).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [[ ! -f "$REPO_ROOT/app.py" ]]; then
  echo "→ Cloning DocToMarkdown into $RUNTIME_DIR/src …"
  mkdir -p "$RUNTIME_DIR"
  if [[ -d "$RUNTIME_DIR/src/.git" ]]; then
    git -C "$RUNTIME_DIR/src" pull --ff-only
  else
    rm -rf "$RUNTIME_DIR/src"
    git clone --depth 1 https://github.com/diegombraga/DocToMarkdown "$RUNTIME_DIR/src"
  fi
  REPO_ROOT="$RUNTIME_DIR/src"
fi

log()  { printf "\033[36m→\033[0m %s\n" "$*"; }
ok()   { printf "\033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[33m!\033[0m %s\n" "$*"; }
die()  { printf "\033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "Este script é somente para macOS. Use install.sh na raiz."

log "Instalando DocToMarkdown para macOS"

# ---------- Homebrew ----------
if ! command -v brew >/dev/null 2>&1; then
  if [[ -n "$CI_MODE" ]]; then
    warn "Homebrew não encontrado; assumindo runner do CI já tem"
  else
    log "Homebrew não encontrado — instalando…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
fi

# Ensure brew on PATH
if [[ -x /opt/homebrew/bin/brew ]]; then eval "$(/opt/homebrew/bin/brew shellenv)"; fi
if [[ -x /usr/local/bin/brew ]]; then eval "$(/usr/local/bin/brew shellenv)"; fi

# ---------- System deps ----------
log "Instalando dependências via Homebrew…"
brew install python@3.12 ocrmypdf tesseract-lang ffmpeg >/dev/null || warn "brew install teve avisos (pode ser normal)"
ok "Dependências de sistema prontas"

PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v python3)"

# ---------- Runtime dir + source copy ----------
log "Preparando $RUNTIME_DIR …"
mkdir -p "$RUNTIME_DIR/src"
rsync -a --delete \
  --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
  --exclude='.DS_Store' --exclude='dist' --exclude='build' \
  "$REPO_ROOT/app.py" "$REPO_ROOT/templates" "$REPO_ROOT/static" \
  "$REPO_ROOT/video" "$REPO_ROOT/requirements.txt" \
  "$RUNTIME_DIR/src/"

# ---------- venv ----------
if [[ ! -x "$RUNTIME_DIR/.venv/bin/python" ]]; then
  log "Criando venv em $RUNTIME_DIR/.venv"
  "$PYTHON_BIN" -m venv "$RUNTIME_DIR/.venv"
fi
log "Instalando pacotes Python (pode demorar ~1 min na primeira vez)…"
"$RUNTIME_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$RUNTIME_DIR/.venv/bin/pip" install --upgrade -r "$RUNTIME_DIR/src/requirements.txt" >/dev/null
ok "Ambiente Python pronto"

# ---------- Build .app bundle ----------
log "Montando $APP_PATH"
rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"
cp "$REPO_ROOT/platform/macos/DocToMarkdown.app/Contents/Info.plist" "$APP_PATH/Contents/Info.plist"
cp "$REPO_ROOT/platform/macos/DocToMarkdown.app/Contents/MacOS/DocToMarkdown" "$APP_PATH/Contents/MacOS/DocToMarkdown"
cp "$REPO_ROOT/platform/macos/Resources/AppIcon.icns" "$APP_PATH/Contents/Resources/AppIcon.icns"
chmod +x "$APP_PATH/Contents/MacOS/DocToMarkdown"
# Bust Gatekeeper cache so the icon refreshes
touch "$APP_PATH"
ok "App instalado em $APP_PATH"

# ---------- CLI symlink ----------
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/doc2md" <<'EOF'
#!/usr/bin/env bash
exec "$HOME/Library/Application Support/DocToMarkdown/.venv/bin/python" "$HOME/Library/Application Support/DocToMarkdown/src/app.py" "$@"
EOF
chmod +x "$BIN_DIR/doc2md"
ok "CLI 'doc2md' criado em $BIN_DIR (adicione ao PATH se ainda não estiver)"

# ---------- Skill (opcional) ----------
if [[ -d "$REPO_ROOT/skill" ]]; then
  SKILL_DEST="$HOME/.claude/skills/DocToMarkdown"
  if [[ -n "$CI_MODE" ]] || { [[ -t 0 ]] && read -r -p "Instalar skill do Claude Code em ~/.claude/skills/? [Y/n] " ans && [[ "${ans,,}" != "n" ]]; }; then
    mkdir -p "$SKILL_DEST"
    cp -R "$REPO_ROOT/skill/." "$SKILL_DEST/"
    ok "Skill instalada em $SKILL_DEST"
  fi
fi

echo ""
ok "Tudo pronto. Para abrir a interface:"
echo "   • Duplo-clique em $APP_PATH"
echo "   • ou CLI:  doc2md arquivo.pdf"
echo ""

# Auto-launch se interativo
if [[ -z "$CI_MODE" ]] && [[ -t 0 ]]; then
  open "$APP_PATH" || true
fi
