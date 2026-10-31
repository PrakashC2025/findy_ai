#!/usr/bin/env bash
# Double-click on macOS/Linux (or run from a terminal) to start the DevMind web app.
#
# It is deliberately forgiving, because the person clicking it may never have used a
# terminal:
#   * finds Python 3.11+ (py launcher, python3, python - in that order),
#   * creates .venv on first run and installs the project (needs internet once),
#   * picks a free port if 8787 is taken,
#   * writes everything to a log and keeps the window open when something fails.
#
# Diagnostics without starting anything:
#   DEVMIND_LAUNCH_DRY_RUN=1 ./scripts/devmind.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)" || exit 1
cd "$ROOT" || exit 1

PORT="${DEVMIND_PORT:-8787}"
DRY_RUN="${DEVMIND_LAUNCH_DRY_RUN:-0}"
LOG="${TMPDIR:-/tmp}/devmind-launch.log"
: >"$LOG" 2>/dev/null || LOG="/tmp/devmind-launch.log"

say() { printf '%s\n' "$*"; }
note() { printf '%s\n' "$*" >>"$LOG" 2>/dev/null; }

pause_if_tty() {
  if [ -t 0 ]; then
    printf '\nPress Enter to close this window...'
    IFS= read -r _ || true
  fi
}

die() {
  say ""
  say "✗ $*"
  say ""
  say "Log file: $LOG"
  say "You can always start DevMind by hand from a terminal in this folder:"
  say "  python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/python -m devmind serve --open"
  pause_if_tty
  exit 1
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
      "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

RUNTIME=""
if [ -x ".venv/bin/python" ]; then
  RUNTIME="$ROOT/.venv/bin/python"
  note "using the existing .venv"
else
  SYSTEM_PYTHON="$(find_python)" || SYSTEM_PYTHON=""
  if [ -z "$SYSTEM_PYTHON" ]; then
    say "DevMind needs Python 3.11 or newer."
    say ""
    say "macOS:   install it from https://www.python.org/downloads/ (or: brew install python@3.12)"
    say "Linux:   sudo apt install python3 python3-venv   (Fedora: sudo dnf install python3)"
    say "Windows: https://www.python.org/downloads/windows/ - tick \"Add python.exe to PATH\""
    die "Python 3.11+ was not found on this machine."
  fi
  note "no .venv yet; using $SYSTEM_PYTHON"
  if [ "$DRY_RUN" = "0" ]; then
    say "First run: creating .venv with $SYSTEM_PYTHON ..."
    if ! "$SYSTEM_PYTHON" -m venv .venv >>"$LOG" 2>&1; then
      say ""
      say "Could not create a virtual environment."
      say "Linux users often need: sudo apt install python3-venv"
      die "virtual environment creation failed."
    fi
    RUNTIME="$ROOT/.venv/bin/python"
  fi
fi

if [ "$DRY_RUN" != "0" ] && [ -z "$RUNTIME" ]; then
  say "root=$ROOT"
  say "python=$SYSTEM_PYTHON (system)"
  say "venv=missing"
  say "deps=unknown (dry run)"
  say "command=python -m devmind serve --open --port $PORT"
  exit 0
fi

if ! "$RUNTIME" -c 'import fastapi, httpx, uvicorn' >/dev/null 2>&1; then
  if [ "$DRY_RUN" != "0" ]; then
    say "root=$ROOT"
    say "python=$RUNTIME"
    say "deps=missing"
    say "command=$RUNTIME -m devmind serve --open --port $PORT ${DEVMIND_DEMO:+--demo}"
    exit 0
  fi
  say "Installing DevMind and its dependencies (one time, needs internet) ..."
  if ! "$RUNTIME" -m pip install --disable-pip-version-check -e . >>"$LOG" 2>&1; then
    say ""
    say "The install step failed. Common causes:"
    say "  * no internet access right now,"
    say "  * a corporate proxy (set HTTPS_PROXY),"
    say "  * a very old pip (the log shows it)."
    die "pip install failed."
  fi
fi

if [ "$DRY_RUN" != "0" ]; then
  say "root=$ROOT"
  say "python=$RUNTIME"
  say "deps=ok"
  say "command=$RUNTIME -m devmind serve --open --port $PORT ${DEVMIND_DEMO:+--demo}"
  exit 0
fi

# Pick a nearby free port so a stale DevMind window does not block the new one.
port_is_free() {
  "$RUNTIME" - "$1" <<'PY' >/dev/null 2>&1
import socket, sys
sock = socket.socket()
sock.settimeout(0.3)
sys.exit(0 if sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) else 1)
PY
}

BASE_PORT="$PORT"
while ! port_is_free "$PORT"; do
  PORT=$((PORT + 1))
  if [ "$PORT" -gt $((BASE_PORT + 20)) ]; then
    die "no free port between $BASE_PORT and $((BASE_PORT + 20)); stop the other program first."
  fi
done

DEMO_FLAG=""
if [ "${DEVMIND_DEMO:-0}" = "1" ]; then DEMO_FLAG="--demo"; fi
VERSION="$("$RUNTIME" -m devmind --version 2>/dev/null | head -1)"
say "Starting DevMind${VERSION:+ ($VERSION)} at http://127.0.0.1:$PORT"
say "Leave this window open while you use the app. Press Ctrl+C here to stop it."
say ""

if ! "$RUNTIME" -m devmind serve --open --port "$PORT" $DEMO_FLAG 2>&1 | tee -a "$LOG"; then
  die "the server could not start (see the lines above)."
fi

say ""
say "DevMind stopped. If it crashed before showing the web page, the log is: $LOG"
pause_if_tty
