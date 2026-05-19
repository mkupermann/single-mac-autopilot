#!/usr/bin/env bash
# <<PROJECT>> vacation autopilot — single-session driver.
#
# Called by launchd every 6 hours. One invocation = one Claude Code session
# working on one queue item. Returns clean on token-cap, crash, or success;
# the next launchd tick picks up where this left off.
#
# Layout:
#   .<<project>>/autopilot/run_autopilot.sh   <- this file (versioned)
#   .<<project>>/autopilot/CHARTER.md         <- system-prompt append (versioned)
#   .<<project>>/autopilot/QUEUE.yaml         <- work queue (versioned)
#   ~/.<<project>>/autopilot/STOP             <- touch this to disable (not versioned)
#   ~/.<<project>>/autopilot/current_item.txt <- preflight writes, postflight reads
#   ~/.<<project>>/autopilot/last_tick.txt    <- preflight timestamp for watchdog
#   ~/.<<project>>/autopilot/HUMAN_NEEDED.md  <- session appends when uncertain
#   ~/.<<project>>/autopilot/session.log      <- this script appends every run

set -uo pipefail

REPO="<<REPO_ROOT>>"
STATE_DIR="$HOME/.<<project>>/autopilot"
LOG="$STATE_DIR/session.log"
CHARTER="$REPO/.<<project>>/autopilot/CHARTER.md"
VENV_PY="$REPO/.venv/bin/python"
LOCKDIR="$STATE_DIR/wrapper.lock.d"

mkdir -p "$STATE_DIR"
exec >> "$LOG" 2>&1

echo ""
echo "==================== $(date -Iseconds) ===================="

# Single-instance lock. macOS has no `flock` binary, so use mkdir as the
# atomic primitive (POSIX-portable). If another wrapper is mid-flight,
# exit clean — the next launchd slot will try again.
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  other_pid=$(cat "$LOCKDIR/pid" 2>/dev/null || echo "?")
  echo "[$(date -Iseconds)] another wrapper is running (pid=$other_pid) — exiting clean"
  exit 0
fi
echo $$ > "$LOCKDIR/pid"

cd "$REPO" || { echo "cannot cd to $REPO"; exit 2; }

# 0. Stash any pre-existing uncommitted state (user's in-progress work that
# is unrelated to the autopilot). preflight requires a clean tree; we restore
# this stash at the very end so the user's work is preserved across sessions.
DIRTY_BEFORE=$(git status --porcelain)
STASH_MSG=""
if [[ -n "$DIRTY_BEFORE" ]]; then
  STASH_MSG="autopilot-isolation-$(date +%s)"
  echo "[$(date -Iseconds)] stashing pre-existing dirty state as '$STASH_MSG'"
  git stash push --include-untracked -m "$STASH_MSG" >/dev/null 2>&1 || echo "[$(date -Iseconds)] WARN: stash push failed"
fi

# Helper: pop the stash back AND release the lock. Called from every exit path.
restore_stash() {
  if [[ -n "$STASH_MSG" ]]; then
    local sref
    sref=$(git stash list | grep -F "$STASH_MSG" | head -1 | sed 's/:.*//')
    if [[ -n "$sref" ]]; then
      git checkout main 2>/dev/null || true
      git stash pop "$sref" >/dev/null 2>&1 || \
        echo "[$(date -Iseconds)] WARN: stash pop failed; recover manually: git stash list | grep $STASH_MSG"
    fi
  fi
  rm -f "$LOCKDIR/pid" 2>/dev/null
  rmdir "$LOCKDIR" 2>/dev/null || true
}
trap restore_stash EXIT

# 1. Preflight
echo "[$(date -Iseconds)] preflight..."
if ! "$VENV_PY" tools/autopilot_preflight.py; then
  echo "[$(date -Iseconds)] preflight rejected — exiting clean"
  exit 0
fi

ITEM=$(cat "$STATE_DIR/current_item.txt")
BRIEF=$(cat "$STATE_DIR/current_brief.txt")
echo "[$(date -Iseconds)] preflight OK, item=$ITEM brief=$BRIEF"

# 2. Headless Claude session
#
# --print              : non-interactive, prints final result
# --append-system-prompt: bolt CHARTER onto the system prompt
# --model              : Opus 4.7 primary, Sonnet 4.6 fallback if Opus rate-limited at start
# --max-turns 80       : per-session ceiling (charter line 6)
# --permission-mode acceptEdits : allow file edits + bash, no interactive approval
# --allowed-tools      : whitelist; intentionally NOT including WebFetch/WebSearch
#                       (fully offline session, no surprise network calls)
#
# Fallback policy: if Opus exits non-zero within 5 minutes AND output contains a
# rate-limit signature, retry with sonnet-4-6. Late failures (Opus ran > 5min
# then quit) are accepted as-is — postflight evaluates whatever the partial
# session produced.

PROMPT="You are starting an autopilot session on <<PROJECT>> item ${ITEM}.

The charter at .<<project>>/autopilot/CHARTER.md is your constitutional contract. Read it first.
The amendment brief is at ${BRIEF}. Read it second.
The pre-registered acceptance is in .<<project>>/autopilot/QUEUE.yaml under id: ${ITEM}. It is locked.

You are on branch autopilot/${ITEM}. Commits made here will be pushed by the postflight script — you do NOT push yourself.

Now invoke the 'autonomous-prototype-build' skill and run it against the pre-registered acceptance until one of: pass, hard blocker, time/turn budget exhausted. NULL is a valid outcome. Do not retune thresholds. Do not edit marker_protocol files.

When done, exit cleanly. The postflight script will run the tests, decide the verdict, write LOGBOOK, commit, and push.
"

echo "[$(date -Iseconds)] launching claude headless for item=$ITEM"

# Wall-clock cap: 4h. Use `timeout` if installed, else `gtimeout` (coreutils via brew),
# else fall back to per-call --max-turns and trust it.
TIMEOUT_CMD=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_CMD="timeout 4h"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_CMD="gtimeout 4h"
fi

# Set autopilot env BEFORE invoking claude so the pre-commit hook arms.
export AUTOPILOT_ACTIVE=1

CLAUDE_OUT="$STATE_DIR/last_claude_output.txt"
RATE_LIMIT_RE="rate.?limit|usage.?limit|5.?hour limit|quota|usage_limit_reached|too many requests|429|Try again at"

run_claude() {
  local MODEL="$1"
  echo "[$(date -Iseconds)] invoking claude --model=$MODEL"
  $TIMEOUT_CMD claude \
    --print \
    --model "$MODEL" \
    --max-turns 80 \
    --permission-mode acceptEdits \
    --allowed-tools "Read,Edit,Write,Bash,Grep,Glob,Skill,TaskCreate,TaskUpdate,TaskList" \
    --append-system-prompt "$(cat "$CHARTER")" \
    "$PROMPT" 2>&1 | tee "$CLAUDE_OUT"
  return ${PIPESTATUS[0]}
}

START_TS=$(date +%s)
run_claude "claude-opus-4-7"
CLAUDE_EXIT=$?
ELAPSED=$(( $(date +%s) - START_TS ))
echo "[$(date -Iseconds)] opus exited code=$CLAUDE_EXIT elapsed=${ELAPSED}s"

if [[ $CLAUDE_EXIT -ne 0 ]] && [[ $ELAPSED -lt 300 ]] && grep -qiE "$RATE_LIMIT_RE" "$CLAUDE_OUT"; then
  echo "[$(date -Iseconds)] Opus rate-limit signature detected (elapsed=${ELAPSED}s) — falling back to sonnet-4-6"
  START_TS=$(date +%s)
  run_claude "claude-sonnet-4-6"
  CLAUDE_EXIT=$?
  ELAPSED=$(( $(date +%s) - START_TS ))
  echo "[$(date -Iseconds)] sonnet fallback exited code=$CLAUDE_EXIT elapsed=${ELAPSED}s"
fi

echo "[$(date -Iseconds)] claude exited with code=$CLAUDE_EXIT"

# 3. Postflight runs regardless of how claude exited (success, timeout, 429, crash).
echo "[$(date -Iseconds)] postflight..."
"$VENV_PY" tools/autopilot_postflight.py
POST_EXIT=$?
echo "[$(date -Iseconds)] postflight exited with code=$POST_EXIT"

# 4. Return to main so the next preflight has a clean starting branch.
# (Note: trap restore_stash also does checkout main, so this is just for clarity
# before the postmortem step below.)
git checkout main 2>/dev/null || true

# 5. If postflight saw a NULL/FAIL verdict, kick off the Opus postmortem in a
#    short separate process. Gated to that one job — no chance of Opus inside the loop.
LAST_VERDICT=$(tail -50 "$REPO/LOGBOOK.md" | grep -E "^- \*\*Verdict\*\*:" | tail -1 | awk '{print tolower($3)}')
if [[ "$LAST_VERDICT" == "null" || "$LAST_VERDICT" == "fail" ]]; then
  if [[ -x "$REPO/tools/autopilot_opus_postmortem.py" ]]; then
    echo "[$(date -Iseconds)] launching Opus postmortem (verdict=$LAST_VERDICT)"
    "$VENV_PY" tools/autopilot_opus_postmortem.py "$ITEM" || true
  fi
fi

echo "[$(date -Iseconds)] session done"
exit 0
