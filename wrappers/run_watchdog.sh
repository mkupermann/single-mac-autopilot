#!/usr/bin/env bash
# Watchdog launchd wrapper. Exists so that the plist can invoke /bin/bash
# (which has Full Disk Access per System Settings), avoiding the macOS TCC
# block that prevents launchd from exec'ing binaries living under
# ~/Documents/ directly.

set -u
REPO="<<REPO_ROOT>>"
exec "$REPO/.venv/bin/python" "$REPO/tools/autopilot_watchdog.py"
