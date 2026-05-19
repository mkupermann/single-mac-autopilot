# single-mac-autopilot

A small, opinionated pipeline for running pre-registered, falsifier-based
research tasks autonomously on **one** macOS workstation. No cloud, no GPU,
no team — just launchd, Python, and the discipline of writing acceptance
tests before you run them.

## What this is

When you have a research question that pencils out to "write some code, run
some tests, write the next idea on top of the result, repeat for two weeks",
you usually need a small lab to do that without being chained to your
terminal. This is the smallest such lab I could justify writing.

It runs on a single Mac with Python and `launchd`. It uses the headless mode
of [Claude Code](https://claude.com/claude-code) to execute the implementation
work itself. It enforces that every item has pre-registered acceptance criteria
*before* the item runs, that those criteria can't be edited mid-run, and that
the verdict is computed mechanically from `pytest` output rather than from a
language model's self-assessment.

This is not a framework. It is ~1000 lines of Python and a handful of
`launchd` plists. The shape is simpler than the discipline.

## What this is not

- It is not a multi-tenant research platform. One user, one workstation, one
  project at a time.
- It is not a replacement for human judgment. The pipeline executes
  pre-registered work and surfaces NULL/blocked outcomes for human review;
  it does not invent new research questions.
- It is not robust against an adversarial agent. The headless Claude session
  is assumed to follow the charter; the pre-commit hook is the last line of
  defence, not the only one.
- It is not Bayesian, neuromorphic, or anything else fashionable. It is a
  job queue with discipline.

## Why exists

Built originally as the vacation autopilot for
[`mkupermann/vibrasim`](https://github.com/mkupermann/vibrasim), a private
research project on bottom-up emergent neural substrates. The pipeline
turned out to be the more reusable artifact. The substrate research
continues as a test-bed for sharpening this pipeline.

The companion repo's purpose statement is reciprocal: vibrasim's stated
goal includes *improving this pipeline*.

## Components

| Piece | Where | What it does |
|---|---|---|
| Autopilot wrapper | `wrappers/run_autopilot.sh` | Per-launchd-tick: stash dirty state, run preflight, launch headless Claude with a CHARTER-appended system prompt, run postflight, restore stash. |
| Preflight | `pipeline/preflight.py` | Pick next item with `status: queued`, validate brief + acceptance, branch from main as `autopilot/<id>`, run baseline `pytest -m "not slow"`. |
| Postflight | `pipeline/postflight.py` | Run pre-registered pytest targets with 30-min timeout, compute verdict mechanically (PASS / NULL / FAIL), in-place yaml update on `QUEUE.yaml`, commit to `autopilot/<id>` branch, sync the item's status fields back to `main`, mail the user. |
| Supervisor | `pipeline/supervisor.py` | Every 30 min: if no wrapper running, fire one. If a wrapper has been alive >5h, kill its process tree and refire. |
| Long-run dispatcher | `pipeline/long_run_dispatcher.py` | Separate pipeline for items that exceed the 4-h verifier cap. Runs `pytest` detached, monitors via pidfile, evaluates result.json on completion, advances its own queue. |
| Watchdog | `pipeline/watchdog.py` | Hourly: heartbeat freshness check. Daily 08:30: summary mail. Any time: immediate mail on `HUMAN_NEEDED.md` growth. |
| Mail helper | `pipeline/mail.py` | Apple Mail osascript primary, `/usr/bin/mail` fallback, disk persistence on total failure. |
| Pre-commit hook | `hooks/pre-commit` | Hard-blocks autopilot commits to forbidden paths (`marker_protocol*.md`, `CHARTER.md`, `preregistered_acceptance:` blocks in `QUEUE.yaml`) and to any branch other than `autopilot/*`. |
| Mail osascript | `mail/send_mail.scpt` | Sends plain-text mail via Apple Mail.app. Decodes `\n` to newline. |
| CHARTER template | `CHARTER.template.md` | The constitutional contract appended to every headless Claude session's system prompt. Defines hard prohibitions, when to set `HUMAN_NEEDED`, what every session must produce. |

## Setup

Edit placeholders in the copied files (search for `<<REPO_ROOT>>`,
`<<STATE_DIR>>`, `<<HOME>>`, `<<USER>>`, `<<PROJECT>>`, `<<RECIPIENT_EMAIL>>`,
`<<project>>`, `AUTOPILOT_ACTIVE`).

```
git clone https://github.com/<<USER>>/single-mac-autopilot.git
cd single-mac-autopilot

# 1. Place pipeline scripts under <<REPO_ROOT>>/tools/, plists under
#    ~/Library/LaunchAgents/, wrappers under ~/.<<project>>/autopilot/
# 2. Personalize the placeholders with sed
# 3. Bootstrap:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.autopilot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.supervisor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.watchdog.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.long-run.plist
```

No installer ships yet; the steps above are what worked on one machine.
Polish is a TODO.

## State files (outside the repo)

The pipeline writes its runtime state to `~/.<<project>>/autopilot/` and
`~/.<<project>>/long-run/`. These are intentionally outside `~/Documents/`
because macOS 15 launchd silently denies FDA to `/bin/bash` even when
explicitly granted; the workaround is to place launchd wrapper scripts
outside TCC-protected paths.

## Pre-registration as the only discipline that survives contact

Every item in `QUEUE.yaml` has:

```yaml
  - id: R-N
    title: short description
    brief: docs/superpowers/plans/<plan-file>.md
    preregistered_acceptance:
      - "tests/.../specific_test.py::test_function PASSES"
      - "tests/.../negative_control.py::test_function PASSES"
    time_budget_hours: 4
    status: queued
    attempts: 0
    blockers: []
```

The `preregistered_acceptance` block is **locked** once the item transitions
to `in_progress`. The headless session cannot edit it (pre-commit hook
blocks). The verdict is computed by running those pytest targets and reading
their exit code. `PASSES` strings become pass-targets; `FAILS` strings become
negative-control targets that *must* fail for the item to pass (catches
silent-pass tests).

If the session can't get the pre-registered tests to pass, the verdict is
NULL or FAIL. Not "PASS with a caveat". Not "I'll try a different threshold".
The discipline is mechanical because human discipline isn't.

## Lessons learned (the real value of this repo)

Documented in [`docs/lessons-learned.md`](docs/lessons-learned.md). Short
list of failure modes encountered and the fixes that ended up in the
pipeline:

- macOS 15 launchd ignores FDA on `/bin/bash` for executables under `~/Documents/` — relocate or use launchd-safe paths.
- Headless Claude commits referenced files that were never staged ("false pass") — postflight runs the pytest verifier independently of the session's self-report.
- `yaml.safe_dump` reformat triggers pre-commit hook false-positive on `preregistered_acceptance:` blocks — postflight does in-place text-update of only the runtime fields.
- Acceptance tests that include `@pytest.mark.slow` integration tests hung postflight for 6.5h — added 30-min `pytest` timeout.
- Branch-isolation: item outputs land on `autopilot/<id>`, downstream items branch from main and don't see them — solved by per-item code import to main between items (manual today, automation TODO).
- Postflight TypeError when status was YAML `null` (parses to Python `None`) — defensive coercion in mail body builder.

## License

MIT. See [`LICENSE`](LICENSE).

## Acknowledgements

Built with Claude Code (Opus 4.7, 1M context). The autopilot uses Claude
Code's headless mode for the implementation work; the pipeline orchestration,
acceptance evaluation, and discipline-enforcement are all this repo.
