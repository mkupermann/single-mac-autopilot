"""Shared mail helper for autopilot.

Sends plain-text email to the user via Apple Mail (osascript) with /usr/bin/mail
as fallback. If both fail, persists the unsent mail to ~/.<<project>>/autopilot/.

Used by:
    - tools/autopilot_watchdog.py (hourly heartbeat + daily summary + alerts)
    - tools/autopilot_postflight.py (per-session report at end of every run)
"""
from __future__ import annotations

import datetime as _dt
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCPT_PATH = REPO / ".<<project>>/autopilot/scripts/send_mail.scpt"
STATE_DIR = Path.home() / ".<<project>>/autopilot"
RECIPIENT = "<<RECIPIENT_EMAIL>>"


def send_mail(subject: str, body: str) -> bool:
    """Send via Apple Mail osascript (primary) or /usr/bin/mail (fallback).

    Returns True on success, False on total failure (unsent persisted to disk).
    """
    body_escaped = body.replace("\\", "\\\\").replace("\n", "\\n")

    # Primary: Apple Mail via osascript.
    try:
        r = subprocess.run(
            ["/usr/bin/osascript", str(SCPT_PATH), RECIPIENT, subject, body_escaped],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and "OK" in r.stdout:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: /usr/bin/mail
    try:
        subprocess.run(
            ["/usr/bin/mail", "-s", subject, RECIPIENT],
            input=body, text=True, check=True, timeout=30,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    # Both failed: persist to disk
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = STATE_DIR / f"unsent_mail_{stamp}.txt"
    fallback.write_text(
        f"SUBJECT: {subject}\n\n{body}\n\n"
        "(Both osascript Mail.app and /usr/bin/mail failed.)\n"
    )
    return False
