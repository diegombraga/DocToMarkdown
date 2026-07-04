#!/usr/bin/env bash
# Linux installer for DocToMarkdown.
# - Detects package manager (apt / dnf / pacman) and installs system deps
# - Copies source into ~/.local/share/DocToMarkdown/src/
# - Creates isolated venv + installs Python deps
# - Places a .desktop entry + icons for the DE
# - Adds a `doc2md` CLI to ~/.local/bin
# - Optionally installs the Claude Code skill
# Idempotent: safe to re-run to update.

set -euo pipefail

CI_MODE="${CI:-}"
if [[ "${1:-}" == "--ci" ]]; then CI_MODE=1; fi

RUNTIME_DIR="$HOME/.local/share/DocToMarkdown"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR_BASE="$HOME/.local/share/icons/hicolor"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [[ ! -f "$REPO_ROOT/app.py" ]]; then
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

[[ "$(uname -s)" == "Linux" ]] || die "Este script é somente para Linux. Use install.sh na raiz."

log "Instalando DocToMarkdown para Linux"

SUDO=""
if [[ $EUID -ne 0 ]]; then SUDO="sudo"; fi

# ---------- Package manager detection ----------
if command -v apt-get >/dev/null 2>&1; then
  PKG=apt
elif command -v dnf >/dev/null 2>&1; then
  PKG=dnf
elif command -v pacman >/dev/null 2>&1; then
  PKG=pacman
else
  die "Não achei apt-get, dnf ou pacman. Instale as deps manualmente ou use o Docker."
fi
log "Detectei gerenciador de pacotes: $PKG"

# ---------- System deps ----------
case "$PKG" in
  apt)
    $SUDO apt-get update -qq
    $SUDO apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      ocrmypdf \
      tesseract-ocr tesseract-ocr-por tesseract-ocr-eng \
      tesseract-ocr-spa tesseract-ocr-fra tesseract-ocr-ita tesseract-ocr-deu \
      ffmpeg poppler-utils ghostscript unpaper pngquant \
      curl git
    ;;
  dnf)
    $SUDO dnf install -y \
      python3 python3-virtualenv python3-pip \
      ocrmypdf \
      tesseract tesseract-langpack-por tesseract-langpack-eng \
      tesseract-langpack-spa tesseract-langpack-fra tesseract-langpack-ita tesseract-langpack-deu \
      ffmpeg poppler-utils ghostscript unpaper pngquant \
      curl git
    ;;
  pacman)
    $SUDO pacman -Sy --needed --noconfirm \
      python python-pip \
      ocrmypdf \
      tesseract tesseract-data-por tesseract-data-eng \
      tesseract-data-spa tesseract-data-fra tesseract-data-ita tesseract-data-deu \
      ffmpeg poppler ghostscript unpaper pngquant \
      curl git
    ;;
esac
ok "Dependências de sistema prontas"

# ---------- Runtime + source ----------
log "Preparando $RUNTIME_DIR"
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
  python3 -m venv "$RUNTIME_DIR/.venv"
fi
log "Instalando pacotes Python (pode demorar ~1 min)…"
"$RUNTIME_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$RUNTIME_DIR/.venv/bin/pip" install --upgrade -r "$RUNTIME_DIR/src/requirements.txt" >/dev/null
ok "Ambiente Python pronto"

# ---------- CLI wrapper ----------
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/doc2md" <<EOF
#!/usr/bin/env bash
exec "$RUNTIME_DIR/.venv/bin/python" "$RUNTIME_DIR/src/app.py" "\$@"
EOF
chmod +x "$BIN_DIR/doc2md"
ok "CLI 'doc2md' criado em $BIN_DIR"

# ---------- Icons ----------
log "Instalando ícones no tema hicolor…"
for size in 16 32 64 128 256 512 1024; do
  SRC="$REPO_ROOT/platform/linux/Resources/doctomarkdown_${size}.png"
  DEST="$ICON_DIR_BASE/${size}x${size}/apps"
  mkdir -p "$DEST"
  cp "$SRC" "$DEST/doctomarkdown.png"
done
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "$ICON_DIR_BASE" 2>/dev/null || true
fi
ok "Ícones instalados"

# ---------- .desktop entry ----------
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/DocToMarkdown.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DocToMarkdown
GenericName=File to Markdown Converter
Comment=Convert PDF, DOCX, images and more to Markdown (with OCR)
Exec=$BIN_DIR/doc2md
Icon=doctomarkdown
Terminal=false
Categories=Office;Utility;Publishing;
Keywords=markdown;pdf;ocr;convert;doc;docx;
StartupNotify=true
EOF
chmod +x "$DESKTOP_DIR/DocToMarkdown.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q "$DESKTOP_DIR" 2>/dev/null || true
fi
ok "Atalho de aplicativo criado (aparece no menu do sistema)"

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
echo "   • App menu:  procure 'DocToMarkdown'"
echo "   • Terminal:  doc2md arquivo.pdf"
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
  warn "$BIN_DIR não está no seu \$PATH — adicione manualmente ou reinicie o shell."
fi
echo ""
