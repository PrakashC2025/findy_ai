#!/usr/bin/env bash
# Linux / WSL: run `./scripts/devmind.sh` (or double-click in a file manager that runs scripts).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; else PY="$(command -v python3)"; fi
exec "$PY" -m devmind serve --open --port 8787
