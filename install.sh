#!/usr/bin/env bash
# DocToMarkdown — universal Unix installer.
# Detects the OS and delegates to platform/{macos,linux}/install.sh.
# Windows users: use install.ps1 instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null || echo "")"
UNAME="$(uname -s)"

# Support `curl | bash`: if we don't have the repo alongside us, clone it first.
if [[ -z "$SCRIPT_DIR" ]] || [[ ! -d "$SCRIPT_DIR/platform" ]]; then
  TMP="$(mktemp -d)"
  echo "→ Baixando DocToMarkdown para $TMP …"
  if ! command -v git >/dev/null 2>&1; then
    echo "✗ Preciso do 'git' pra clonar o projeto. Instale e tente de novo." >&2
    exit 1
  fi
  git clone --depth 1 https://github.com/diegombraga/DocToMarkdown "$TMP"
  SCRIPT_DIR="$TMP"
fi

case "$UNAME" in
  Darwin)
    exec bash "$SCRIPT_DIR/platform/macos/install.sh" "$@"
    ;;
  Linux)
    exec bash "$SCRIPT_DIR/platform/linux/install.sh" "$@"
    ;;
  *)
    echo "✗ SO não suportado nativamente: $UNAME" >&2
    echo "  Use o Docker: docker run --rm -p 5555:5555 ghcr.io/diegombraga/doctomarkdown"
    exit 1
    ;;
esac
