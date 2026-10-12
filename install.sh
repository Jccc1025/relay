#!/bin/sh
# Install Relay and its `relay` command:
#
#   curl -fsSL https://raw.githubusercontent.com/sunnweiwei/relay/main/install.sh | sh
#   sh install.sh [SOURCE]      # SOURCE: a checkout, a wheel or a pip URL (default: the GitHub repository)
#
# Relay gets an environment of its own (uv's tool environment where uv is installed, else a
# virtual environment in ~/.local/share/relay), and `relay` goes in ~/.local/bin. Running it again
# upgrades Relay. Then: `relay setup <harness> --strategy <name>` (`relay help` is the guide).
set -eu

SOURCE="${1:-git+https://github.com/sunnweiwei/relay.git}"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"

if command -v uv >/dev/null 2>&1; then
  uv tool install --quiet --force --reinstall "$SOURCE"
  RELAY="$(uv tool dir --bin 2>/dev/null || echo "$BIN")/relay"
else
  PYTHON="$(command -v python3 || command -v python || true)"
  if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "relay: needs Python 3.11 or newer (or uv: https://docs.astral.sh/uv/)" >&2
    exit 1
  fi
  VENV="$HOME/.local/share/relay/venv"
  if [ ! -x "$VENV/bin/python" ] && ! "$PYTHON" -m venv "$VENV" >/dev/null 2>&1; then
    rm -rf "$VENV"
    echo "relay: Python cannot make a virtual environment here (on Debian or Ubuntu: apt install python3-venv)," >&2
    echo "       or install uv (https://docs.astral.sh/uv/) and run this again" >&2
    exit 1
  fi
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade "$SOURCE"
  ln -sf "$VENV/bin/relay" "$BIN/relay"
  RELAY="$BIN/relay"
fi

echo "Relay installed: $RELAY"
FOLDER="$(dirname "$RELAY")"
case ":$PATH:" in
  *":$FOLDER:"*) ;;
  *)  # as uv's and rustup's installers do: the folder on PATH for shells started from now on
    LINE="export PATH=\"$FOLDER:\$PATH\"  # added by Relay's installer"
    for RC in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
      case "$RC" in
        */.profile) ;;
        */.bashrc) [ -f "$RC" ] || [ "$(basename "${SHELL:-}")" = bash ] || continue ;;
        */.zshrc) [ -f "$RC" ] || [ "$(basename "${SHELL:-}")" = zsh ] || continue ;;
      esac
      grep -qsF "added by Relay's installer" "$RC" || echo "$LINE" >> "$RC"
    done
    echo "  note: $FOLDER is now on PATH for new shells; in this one, run Relay as $RELAY"
    ;;
esac
echo "Next: relay setup <harness> --strategy <name>   (relay help: the guide)"
