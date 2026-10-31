#!/bin/bash
# Double-click this file on macOS to open the DevMind web app.
cd "$(dirname "$0")/.." || exit 1
if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; else PY="$(command -v python3)"; fi
exec "$PY" -m devmind serve --open --port 8787
