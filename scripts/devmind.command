#!/bin/bash
# Double-click this on macOS: it hands over to the shared launcher, which creates
# .venv, installs DevMind once and starts the web app. If macOS refuses to run the
# file, right-click > Open, or run:
#   chmod +x scripts/devmind.command scripts/devmind.sh
exec bash "$(dirname "$0")/devmind.sh"
