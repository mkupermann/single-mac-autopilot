#!/usr/bin/env bash
# <<PROJECT>> long-run dispatcher wrapper.
#
# Lives outside ~/Documents (TCC-safe from launchd context). Shells
# out to the repo's Python dispatcher every 30 min.

set -u
REPO="<<REPO_ROOT>>"
exec "$REPO/.venv/bin/python" "$REPO/tools/long_run_dispatcher.py"
