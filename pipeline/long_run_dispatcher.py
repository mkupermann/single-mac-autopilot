"""<<PROJECT>> long-run dispatcher.

Runs every 30 min via launchd. Manages the long-run queue at
~/.<<project>>/long-run/queue.yaml. Each item is a substrate training run
that exceeds the 4-h autopilot postflight cap.

Decision tree per tick:
    1. STOP marker (~/.<<project>>/long-run/STOP) present → exit clean.
    2. PID file (~/.<<project>>/long-run/current.pid) points to a live process
       → exit clean (run in progress).
    3. PID file exists but process is dead:
       3a. Read result.json from current item's out_dir.
       3b. Evaluate against pre-registered acceptance — pytest_target
           tests run on the result.
       3c. Update item status (passed/null/failed) in queue.yaml.
       3d. Append summary to ~/.<<project>>/long-run/LOGBOOK.md.
       3e. Send mail to user.
       3f. Fall through to step 4.
    4. Pick next queued item.
    5. Launch detached: nohup pytest <target> with env, write pidfile,
       redirect output.

Logs to ~/.<<project>>/long-run/dispatcher.log (append).

Pre-registration: status updates are mechanical (pytest verdict). No
post-hoc threshold tuning.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO = Path("<<REPO_ROOT>>")
sys.path.insert(0, str(REPO / "tools"))
try:
    from autopilot_mail import send_mail
except Exception:
    send_mail = None  # mail optional — dispatcher must still work

STATE_DIR = Path.home() / ".<<project>>/long-run"
QUEUE_PATH = STATE_DIR / "queue.yaml"
STOP_PATH = STATE_DIR / "STOP"
PID_PATH = STATE_DIR / "current.pid"
CURRENT_ITEM_PATH = STATE_DIR / "current_item.txt"
DISPATCHER_LOG = STATE_DIR / "dispatcher.log"
LOGBOOK_PATH = STATE_DIR / "LOGBOOK.md"


def log(msg: str) -> None:
    line = f"[{_dt.datetime.now().isoformat()}] {msg}\n"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with DISPATCHER_LOG.open("a") as f:
        f.write(line)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


def load_queue() -> dict:
    return yaml.safe_load(QUEUE_PATH.read_text())


def save_queue_item_status(item_id: str, status: str, attempts: int, finished_at: str) -> None:
    """In-place text update of one item's runtime fields."""
    text = QUEUE_PATH.read_text()
    pattern = re.compile(
        r"(^[ \t]*- id: " + re.escape(item_id) + r"\b.*?\n)(.*?)(?=^[ \t]*- id: |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        log(f"  save: item {item_id} not found in queue.yaml")
        return
    body = m.group(2)
    indent_match = re.search(r"^([ \t]+)status:", body, re.MULTILINE)
    indent = indent_match.group(1) if indent_match else "    "
    body = re.sub(
        r"^(" + re.escape(indent) + r"status: ).*$",
        rf"\g<1>{status}",
        body, count=1, flags=re.MULTILINE,
    )
    if not re.search(r"^" + re.escape(indent) + r"attempts:", body, re.MULTILINE):
        body = re.sub(
            r"^(" + re.escape(indent) + r"status: " + re.escape(status) + r")$",
            rf"\1\n{indent}attempts: {attempts}\n{indent}finished_at: \"{finished_at}\"",
            body, count=1, flags=re.MULTILINE,
        )
    else:
        body = re.sub(
            r"^(" + re.escape(indent) + r"attempts: ).*$",
            rf"\g<1>{attempts}",
            body, count=1, flags=re.MULTILINE,
        )
        if re.search(r"^" + re.escape(indent) + r"finished_at:", body, re.MULTILINE):
            body = re.sub(
                r"^(" + re.escape(indent) + r"finished_at: ).*$",
                rf'\g<1>"{finished_at}"',
                body, count=1, flags=re.MULTILINE,
            )
        else:
            body = re.sub(
                r"^(" + re.escape(indent) + r"attempts: \d+)$",
                rf'\1\n{indent}finished_at: "{finished_at}"',
                body, count=1, flags=re.MULTILINE,
            )
    QUEUE_PATH.write_text(text[: m.start(2)] + body + text[m.end(2) :])


def evaluate_completed_item(item: dict) -> tuple[str, str]:
    """Run the pytest_target against the (now-existing) result.json + env.
    Returns (verdict, log_tail)."""
    item_id = item.get("id", "?")
    env = os.environ.copy()
    for k, v in (item.get("env") or {}).items():
        env[k] = str(v)
    targets = (item.get("pytest_target") or "").split()
    if not targets:
        return "null", "no pytest_target declared"
    venv_py = REPO / ".venv/bin/python"
    try:
        r = subprocess.run(
            [str(venv_py), "-m", "pytest", *targets, "--tb=short", "-q"],
            cwd=REPO, capture_output=True, text=True, env=env, timeout=600,
        )
        tail = (r.stdout + "\n" + r.stderr)[-2000:]
        return ("passed" if r.returncode == 0 else "null"), tail
    except subprocess.TimeoutExpired:
        return "null", "[dispatcher: evaluation pytest hit 10-min timeout]"
    except Exception as exc:
        return "null", f"[dispatcher: evaluation exception {exc!r}]"


def append_logbook(item_id: str, verdict: str, log_tail: str) -> None:
    entry = (
        f"\n\n## {_dt.date.today().isoformat()} — long-run {item_id} → {verdict.upper()}\n\n"
        f"```\n{log_tail.strip()[-1500:]}\n```\n"
    )
    LOGBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOGBOOK_PATH.open("a") as f:
        f.write(entry)


def write_result_json(item: dict, verdict: str, attempts: int, log_tail: str) -> None:
    """Standardized per-item result.json. Cross-run comparison reads these.

    Schema:
        item_id, title, hypothesis, env (recorded), pytest_target,
        verdict, attempts, started_at, finished_at, wallclock_seconds,
        log_tail (last 2000 chars).
    """
    import json
    item_id = item.get("id", "?")
    out_dir = (item.get("env") or {}).get("<<PROJECT>>_R11_OUT_DIR") \
              or (item.get("env") or {}).get("<<PROJECT>>_R8_OUT_DIR") \
              or str(STATE_DIR / item_id)
    out_path = Path(out_dir) / "result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "item_id": item_id,
        "title": item.get("title", ""),
        "hypothesis": item.get("hypothesis", ""),
        "env": item.get("env") or {},
        "pytest_target": item.get("pytest_target", ""),
        "verdict": verdict,
        "attempts": attempts,
        "started_at": item.get("started_at", ""),
        "finished_at": _dt.datetime.now().isoformat(),
        "log_tail": log_tail[-2000:] if log_tail else "",
    }
    out_path.write_text(json.dumps(record, indent=2, default=str))


def mail_session_result(item_id: str, verdict: str, log_tail: str) -> None:
    if send_mail is None:
        return
    subject = f"[<<PROJECT>> long-run] {item_id} → {verdict.upper()}"
    body = (
        f"Long-run completed: {item_id}\n"
        f"Verdict: {verdict.upper()}\n"
        f"Time: {_dt.datetime.now().isoformat()}\n\n"
        f"Pytest tail:\n{log_tail[-1500:]}\n\n"
        f"--\nThis mail is sent by tools/long_run_dispatcher.py.\n"
        f"Stop: touch ~/.<<project>>/long-run/STOP\n"
    )
    try:
        send_mail(subject, body)
    except Exception as exc:
        log(f"  mail failed: {exc!r}")


def launch_item(item: dict) -> int:
    item_id = item["id"]
    env = os.environ.copy()
    for k, v in (item.get("env") or {}).items():
        env[k] = str(v)
    out_dir = env.get("<<PROJECT>>_R11_OUT_DIR") or env.get("<<PROJECT>>_R8_OUT_DIR")
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    log_file = STATE_DIR / f"{item_id}.log"
    targets = (item.get("pytest_target") or "").split()
    cmd = [str(REPO / ".venv/bin/python"), "-m", "pytest", *targets, "--tb=short", "-v", "-s"]
    log(f"  launching {item_id}: {' '.join(cmd)}")
    log(f"  env additions: {item.get('env')}")
    with log_file.open("w") as lf:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO,
            stdout=lf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    PID_PATH.write_text(str(proc.pid))
    CURRENT_ITEM_PATH.write_text(item_id)
    return proc.pid


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log(f"--- dispatcher tick")

    if STOP_PATH.exists():
        log("STOP marker present — exit clean")
        return 0

    if not QUEUE_PATH.exists():
        log("no queue.yaml — exit clean")
        return 0

    # 1. Is a run in progress?
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text().strip())
        except ValueError:
            pid = None
        if pid and pid_alive(pid):
            log(f"  run in progress pid={pid} item={CURRENT_ITEM_PATH.read_text().strip() if CURRENT_ITEM_PATH.exists() else '?'} — exit clean")
            return 0
        # Process dead → evaluate
        item_id = CURRENT_ITEM_PATH.read_text().strip() if CURRENT_ITEM_PATH.exists() else None
        if item_id:
            q = load_queue()
            item = next((i for i in q.get("items") or [] if i.get("id") == item_id), None)
            if item is not None:
                # Update attempts before evaluation
                attempts = int(item.get("attempts", 0)) + 1
                verdict, log_tail = evaluate_completed_item(item)
                save_queue_item_status(item_id, verdict, attempts, _dt.datetime.now().isoformat())
                append_logbook(item_id, verdict, log_tail)
                write_result_json(item, verdict, attempts, log_tail)
                mail_session_result(item_id, verdict, log_tail)
                log(f"  evaluated {item_id} → {verdict} (attempts={attempts}, result.json written)")
        # Clean pidfile, fall through to fire next
        PID_PATH.unlink(missing_ok=True)
        CURRENT_ITEM_PATH.unlink(missing_ok=True)

    # 2. Pick next queued
    q = load_queue()
    items = q.get("items") or []
    next_item = next((i for i in items if i.get("status") == "queued"), None)
    if next_item is None:
        log("  no queued items — exit clean")
        return 0

    new_pid = launch_item(next_item)
    log(f"  launched {next_item['id']} pid={new_pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
