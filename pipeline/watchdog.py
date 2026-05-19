"""autopilot watchdog.

Runs hourly via launchd. Two responsibilities:

1. **Daily summary mail** at 08:30 local time: items_done, items_null, items_failed,
   items_blocked, current_item, last_commit_sha, last_tick_age, HUMAN_NEEDED count.

2. **Immediate alert** if any of:
   - last_tick.txt is older than 12 hours (autopilot is dead)
   - ~/.<<project>>/autopilot/HUMAN_NEEDED.md grew since last check
   - STOP marker appeared but Michael might want to know it's stuck

Mail goes via macOS `mail` command (uses local sendmail) to <<RECIPIENT_EMAIL>>.
A daily-sent marker file prevents duplicate daily summaries.

Designed to be idempotent and silent on the happy path.
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
LAST_TICK = STATE_DIR / "last_tick.txt"
STOP = STATE_DIR / "STOP"
HUMAN_NEEDED = STATE_DIR / "HUMAN_NEEDED.md"
DAILY_SENT = STATE_DIR / "watchdog_last_daily.txt"
ALERT_SEEN = STATE_DIR / "watchdog_alert_seen.txt"
RECIPIENT = "<<RECIPIENT_EMAIL>>"


def now() -> _dt.datetime:
    return _dt.datetime.now()


SCPT_PATH = REPO / ".<<project>>/autopilot/scripts/send_mail.scpt"


def send_mail(subject: str, body: str) -> None:
    """Send via Apple Mail osascript (primary, configured) or /usr/bin/mail (fallback).
    If both fail, persist the unsent mail to disk so it's recoverable on return."""
    # Primary: Apple Mail via osascript. The user's Mail.app has <<RECIPIENT_EMAIL>>
    # configured (daily-mail-drafter skill uses it). osascript expects \n as literal
    # backslash-n in the body argument; the .scpt decodes it.
    body_escaped = body.replace("\\", "\\\\").replace("\n", "\\n")
    try:
        r = subprocess.run(
            ["/usr/bin/osascript", str(SCPT_PATH), RECIPIENT, subject, body_escaped],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and "OK" in r.stdout:
            return
        # osascript ran but Mail.app rejected: fall through to fallback
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: /usr/bin/mail (may not be configured on stock macOS)
    try:
        subprocess.run(
            ["/usr/bin/mail", "-s", subject, RECIPIENT],
            input=body, text=True, check=True, timeout=30,
        )
        return
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    # Both failed: persist to disk
    fallback = STATE_DIR / f"watchdog_unsent_{now().strftime('%Y%m%d_%H%M%S')}.txt"
    fallback.write_text(f"SUBJECT: {subject}\n\n{body}\n\n(Both osascript Mail.app and /usr/bin/mail failed.)\n")


def queue_summary() -> dict:
    if not QUEUE_PATH.exists():
        return {"error": "QUEUE.yaml missing"}
    q = yaml.safe_load(QUEUE_PATH.read_text())
    items = q.get("items") or []
    summary = {
        "total": len(items),
        "passed": sum(1 for i in items if i.get("status") == "passed"),
        "null": sum(1 for i in items if i.get("status") == "null"),
        "failed": sum(1 for i in items if i.get("status") == "failed"),
        "blocked": sum(1 for i in items if i.get("status") == "blocked"),
        "queued": sum(1 for i in items if i.get("status") == "queued"),
        "in_progress": sum(1 for i in items if i.get("status") == "in_progress"),
        "current": next((i.get("id") for i in items if i.get("status") == "in_progress"), None),
    }
    return summary


def last_commit_sha() -> str:
    r = subprocess.run(
        ["git", "log", "--all", "--format=%h %s", "-n", "1"],
        cwd=REPO, capture_output=True, text=True,
    )
    return r.stdout.strip() or "(no commits)"


def tick_age_hours() -> float | None:
    if not LAST_TICK.exists():
        return None
    try:
        ts = _dt.datetime.fromisoformat(LAST_TICK.read_text().strip())
    except ValueError:
        return None
    return (now() - ts).total_seconds() / 3600.0


def build_daily_body() -> str:
    s = queue_summary()
    age = tick_age_hours()
    age_str = f"{age:.1f}h ago" if age is not None else "never"
    hn_count = 0
    if HUMAN_NEEDED.exists():
        hn_count = sum(1 for line in HUMAN_NEEDED.read_text().splitlines() if line.startswith("##"))
    stop = "ACTIVE" if STOP.exists() else "off"

    lines = [
        f"autopilot daily summary — {now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Queue: {s.get('passed', 0)} passed · {s.get('null', 0)} null · {s.get('failed', 0)} failed · "
        f"{s.get('blocked', 0)} blocked · {s.get('queued', 0)} queued · {s.get('in_progress', 0)} in_progress (of {s.get('total', 0)} total)",
        f"Current item: {s.get('current') or '(none)'}",
        f"Last autopilot tick: {age_str}",
        f"Last commit: {last_commit_sha()}",
        f"HUMAN_NEEDED entries: {hn_count}",
        f"STOP marker: {stop}",
        "",
        "Remote: git fetch && git branch -a --list 'autopilot/*'",
        "Tail logs: tail -200 ~/.<<project>>/autopilot/session.log",
        "Notbremse: touch ~/.<<project>>/autopilot/STOP",
    ]
    return "\n".join(lines)


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Immediate-alert conditions
    age = tick_age_hours()
    alerts: list[str] = []
    if age is not None and age > 12:
        alerts.append(f"Autopilot tick is {age:.1f}h old (threshold 12h). System may be stuck.")

    if HUMAN_NEEDED.exists():
        current_hn = HUMAN_NEEDED.read_text()
        last_hn = ALERT_SEEN.read_text() if ALERT_SEEN.exists() else ""
        if current_hn != last_hn:
            alerts.append("HUMAN_NEEDED.md changed since last check. New entry awaiting your review.")
            ALERT_SEEN.write_text(current_hn)

    for alert in alerts:
        send_mail("[autopilot] ALERT", alert + "\n\n" + build_daily_body())

    # Daily summary at first run after 08:00
    today = now().date().isoformat()
    last_daily = DAILY_SENT.read_text().strip() if DAILY_SENT.exists() else ""
    if now().hour >= 8 and last_daily != today:
        send_mail(f"[autopilot] daily {today}", build_daily_body())
        DAILY_SENT.write_text(today + "\n")


if __name__ == "__main__":
    main()
