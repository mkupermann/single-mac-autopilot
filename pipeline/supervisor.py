"""autopilot supervisor.

Runs every 30 minutes via launchd. Single responsibility: ensure the
autopilot wrapper keeps moving forward through the queue without
human intervention.

Decision tree per run:
    1. Is `~/.<<project>>/autopilot/STOP` present?
       → exit clean. User has paused the autopilot.
    2. Is the wrapper lock (~/.<<project>>/autopilot/wrapper.lock.d/) held by
       an alive PID?
       2a. If alive AND held for >5h → kill it (likely stuck postflight
           or pytest), log, fall through to step 3.
       2b. If alive AND held for <5h → exit clean. Wrapper is healthy.
    3. Are there any items with status=queued in QUEUE.yaml?
       → fire the wrapper (nohup, background).
    4. No queued items → exit clean. Queue exhausted; nothing to do.

Logs to ~/.<<project>>/autopilot/supervisor.log (append).
"""
from __future__ import annotations

import datetime as _dt
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
QUEUE_PATH = REPO / ".<<project>>/autopilot/QUEUE.yaml"
WRAPPER_PATH = REPO / ".<<project>>/autopilot/run_autopilot.sh"
STATE_DIR = Path.home() / ".<<project>>/autopilot"
STOP_PATH = STATE_DIR / "STOP"
LOCKDIR = STATE_DIR / "wrapper.lock.d"
LOCK_PID = LOCKDIR / "pid"
SUPERVISOR_LOG = STATE_DIR / "supervisor.log"
STUCK_THRESHOLD_SECONDS = 5 * 3600  # 5 h


def log(msg: str) -> None:
    line = f"[{_dt.datetime.now().isoformat()}] {msg}\n"
    SUPERVISOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SUPERVISOR_LOG.open("a") as f:
        f.write(line)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def kill_pid_tree(pid: int) -> None:
    """SIGTERM the wrapper and any python/claude/pytest children."""
    try:
        # Find children via pgrep -P
        r = subprocess.run(
            ["/usr/bin/pgrep", "-P", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
        children = [int(p) for p in r.stdout.split() if p.strip().isdigit()]
        for cpid in children:
            kill_pid_tree(cpid)
    except Exception as exc:
        log(f"  child enumeration for pid {pid} failed: {exc!r}")
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    # Give it a moment
    time.sleep(2)
    if pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def queue_has_queued_items() -> bool:
    if not QUEUE_PATH.exists():
        return False
    q = yaml.safe_load(QUEUE_PATH.read_text())
    items = q.get("items") or []
    return any(i.get("status") == "queued" for i in items)


def queue_summary() -> str:
    if not QUEUE_PATH.exists():
        return "(no QUEUE.yaml)"
    q = yaml.safe_load(QUEUE_PATH.read_text())
    items = q.get("items") or []
    from collections import Counter
    c = Counter((i.get("status") or "?") for i in items)
    return f"total={len(items)} " + " ".join(f"{k}={v}" for k, v in sorted(c.items()))


def fire_wrapper() -> int:
    """Launch run_autopilot.sh detached. Returns wrapper PID."""
    # Truncate logs so we can tell the new session apart from old
    (STATE_DIR / "session.log").touch()
    proc = subprocess.Popen(
        ["/bin/bash", str(WRAPPER_PATH)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def main() -> int:
    log(f"--- supervisor tick; queue: {queue_summary()}")

    if STOP_PATH.exists():
        log("STOP marker present — exiting clean")
        return 0

    if LOCKDIR.exists():
        try:
            pid_text = LOCK_PID.read_text().strip()
            pid = int(pid_text)
        except (FileNotFoundError, ValueError):
            pid = None
        if pid and pid_alive(pid):
            lock_age = time.time() - LOCK_PID.stat().st_mtime
            if lock_age < STUCK_THRESHOLD_SECONDS:
                log(f"wrapper alive pid={pid} age={lock_age:.0f}s — healthy, exit")
                return 0
            log(f"wrapper STUCK pid={pid} age={lock_age:.0f}s (>{STUCK_THRESHOLD_SECONDS}s) — killing tree")
            kill_pid_tree(pid)
            # Wait a moment for the trap to release lock
            time.sleep(5)
            # If lock is still there (trap didn't run), force-clean
            if LOCKDIR.exists():
                try:
                    LOCK_PID.unlink(missing_ok=True)
                    LOCKDIR.rmdir()
                    log("  force-removed stale lockdir")
                except Exception as exc:
                    log(f"  lockdir cleanup failed: {exc!r}")
        else:
            # Lock dir present but pid dead — stale, clean
            log("stale lockdir (pid dead) — force-cleaning")
            try:
                LOCK_PID.unlink(missing_ok=True)
                LOCKDIR.rmdir()
            except Exception as exc:
                log(f"  cleanup failed: {exc!r}")

    # At this point no wrapper is running
    if not queue_has_queued_items():
        log("queue exhausted (no queued items) — exit clean")
        return 0

    new_pid = fire_wrapper()
    log(f"fired new wrapper pid={new_pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
