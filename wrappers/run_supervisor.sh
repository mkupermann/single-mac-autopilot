#!/usr/bin/env bash
# autopilot supervisor wrapper.
#
# Lives outside ~/Documents (TCC-safe from launchd context). Shells
# out to the repo's Python supervisor every 30 min.

set -u
REPO="<<REPO_ROOT>>"
exec "$REPO/.venv/bin/python" "$REPO/tools/autopilot_supervisor.py"
