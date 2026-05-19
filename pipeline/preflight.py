"""autopilot preflight check.

Run BEFORE every headless Claude session. Decides whether to proceed and which
item to work on. Exits non-zero on any precondition failure — the launchd job
must skip the session in that case.

Outputs (on success):
    - ~/.<<project>>/autopilot/current_item.txt   : the item id Claude will work on
    - ~/.<<project>>/autopilot/last_tick.txt      : ISO timestamp of this preflight
    - stdout                                : a one-line summary

Preconditions:
    - ~/.<<project>>/autopilot/STOP must NOT exist
    - repo working tree must be clean (no uncommitted changes on main)
    - QUEUE.yaml must parse and contain at least one queued or in_progress item
    - the brief file for that item must exist
    - HEAD must be on main (we'll branch from there)
    - fast-slice pytest must pass (sanity baseline)
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
QUEUE_PATH = REPO / ".<<project>>/autopilot/QUEUE.yaml"
STATE_DIR = Path.home() / ".<<project>>/autopilot"
STOP_PATH = STATE_DIR / "STOP"
CURRENT_ITEM_PATH = STATE_DIR / "current_item.txt"
CURRENT_BRIEF_PATH = STATE_DIR / "current_brief.txt"
LAST_TICK_PATH = STATE_DIR / "last_tick.txt"


def die(msg: str, code: int = 1) -> None:
    print(f"preflight: FAIL: {msg}", file=sys.stderr)
    sys.exit(code)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. STOP marker
    if STOP_PATH.exists():
        die(f"STOP marker present at {STOP_PATH} — autopilot disabled by user")

    # 2. Clean tree on main
    r = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = r.stdout.strip()
    if branch != "main":
        die(f"HEAD is on '{branch}', expected main")
    r = run(["git", "status", "--porcelain"])
    if r.stdout.strip():
        die("working tree not clean — uncommitted changes on main:\n" + r.stdout)

    # 3. Queue
    if not QUEUE_PATH.exists():
        die(f"queue file missing: {QUEUE_PATH}")
    queue = yaml.safe_load(QUEUE_PATH.read_text())
    items = queue.get("items") or []
    if not items:
        die("queue is empty — nothing to work on")

    # Pick the next item: in_progress takes priority (resume), else first queued.
    pick = None
    for it in items:
        if it.get("status") == "in_progress":
            pick = it
            break
    if pick is None:
        for it in items:
            if it.get("status") == "queued":
                pick = it
                break
    if pick is None:
        die("no items with status in {queued, in_progress} — queue exhausted")

    # 4. Item-level sanity
    item_id = pick.get("id")
    if not item_id:
        die("picked item has no id")
    if pick.get("attempts", 0) >= 3:
        die(f"item {item_id} has attempts>=3 — should have been marked failed by postflight")
    brief = pick.get("brief")
    if not brief:
        die(f"item {item_id} has no brief: field")
    brief_path = REPO / brief
    if not brief_path.exists():
        die(f"brief file missing for {item_id}: {brief_path}")
    if not pick.get("preregistered_acceptance"):
        die(f"item {item_id} has empty preregistered_acceptance")

    # 5. Fast-slice pytest sanity. We tolerate "no tests collected" but not failures.
    r = run([str(REPO / ".venv/bin/python"), "-m", "pytest", "-m", "not slow", "-x", "--tb=no", "-q"])
    if r.returncode != 0:
        # Print last 20 lines of pytest output for the watchdog mail.
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-20:])
        die(f"baseline pytest fails on main BEFORE autopilot starts:\n{tail}")

    # 6. Switch to autopilot/<item-id> branch (create if absent, reset to main otherwise).
    branch_name = f"autopilot/{item_id}"
    r = run(["git", "rev-parse", "--verify", branch_name])
    if r.returncode == 0:
        # Branch exists — check it out, fast-forward only.
        run(["git", "checkout", branch_name], check=True)
    else:
        run(["git", "checkout", "-b", branch_name], check=True)

    # 7. Persist the pick + timestamp
    CURRENT_ITEM_PATH.write_text(item_id + "\n")
    CURRENT_BRIEF_PATH.write_text(brief + "\n")
    LAST_TICK_PATH.write_text(_dt.datetime.now().isoformat() + "\n")

    print(f"preflight OK — branch={branch_name} item={item_id} attempts={pick.get('attempts', 0)}")


if __name__ == "__main__":
    main()
