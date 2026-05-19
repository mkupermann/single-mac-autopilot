"""autopilot_salvage_branch — bring code from a NULL/failed item's branch to main.

When an item gets a NULL verdict (acceptance not met) but the session
wrote substantive code/tests on `autopilot/<item-id>`, the code lives
on that branch and never reaches main. Downstream items branch from
main and don't see it.

This script identifies the code-vs-state changes on an autopilot branch
and brings only the code (excluding QUEUE.yaml, LOGBOOK.md, phase-log
metadata) onto main via `git checkout autopilot/<id> -- <code-paths>`
+ a salvage commit.

Usage:
    python tools/autopilot_salvage_branch.py autopilot/R-5

The user reviews the salvage commit before pushing. The script does
NOT push automatically — that's a human decision.

Exit codes:
    0 — salvage commit prepared, awaiting user review
    1 — nothing to salvage (branch identical to main on code paths)
    2 — branch doesn't exist or other error
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Paths that are "state" not "code" — never salvaged.
STATE_PATHS = {
    ".<<project>>/autopilot/QUEUE.yaml",
    ".<<project>>/autopilot/CHARTER.md",
    "LOGBOOK.md",
}
# Paths that are bookkeeping doc — usually salvageable but ask user.
DOC_PATHS_PREFIX = (
    "docs/flux/phase-log.md",
    "docs/superpowers/plans/",
    "docs/flux/long-run-results/",
)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("branch", help="autopilot/<item-id> branch to salvage from")
    p.add_argument("--include-docs", action="store_true",
                   help="also include phase-log/plans (default: code+tests only)")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be salvaged, don't make changes")
    args = p.parse_args()

    # Validate branch
    r = run(["git", "rev-parse", "--verify", args.branch])
    if r.returncode != 0:
        print(f"branch {args.branch} not found", file=sys.stderr)
        return 2

    # Get current branch
    r = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    current_branch = r.stdout.strip()
    if current_branch != "main":
        print(f"must be on main to salvage (currently {current_branch})", file=sys.stderr)
        return 2

    # Verify clean tree (or with untracked only)
    r = run(["git", "status", "--porcelain"])
    dirty = [ln for ln in r.stdout.splitlines() if not ln.startswith("?? ")]
    if dirty:
        print("working tree has uncommitted tracked changes; commit or stash first:",
              file=sys.stderr)
        print("\n".join(dirty), file=sys.stderr)
        return 2

    # List files changed between main..<branch>
    r = run(["git", "diff", "main..." + args.branch, "--name-only"])
    changed = [f.strip() for f in r.stdout.splitlines() if f.strip()]
    if not changed:
        print(f"nothing to salvage — {args.branch} has no changes vs main")
        return 1

    code_files: list[str] = []
    state_files: list[str] = []
    doc_files: list[str] = []
    for f in changed:
        if f in STATE_PATHS:
            state_files.append(f)
        elif any(f.startswith(p) for p in DOC_PATHS_PREFIX):
            doc_files.append(f)
        else:
            code_files.append(f)

    print(f"Salvage plan for {args.branch}:")
    print(f"  code/tests to salvage ({len(code_files)}):")
    for f in code_files:
        print(f"    {f}")
    print(f"  docs ({'INCLUDED' if args.include_docs else 'skipped — use --include-docs'}) ({len(doc_files)}):")
    for f in doc_files:
        print(f"    {f}")
    print(f"  state (always skipped) ({len(state_files)}):")
    for f in state_files:
        print(f"    {f}")

    to_salvage = code_files + (doc_files if args.include_docs else [])
    if not to_salvage:
        print("\nnothing to salvage after filtering.")
        return 1

    if args.dry_run:
        print("\n(--dry-run; no changes made)")
        return 0

    # Salvage: git checkout autopilot/<id> -- <files>
    print(f"\nsalvaging {len(to_salvage)} file(s)...")
    for f in to_salvage:
        rc = run(["git", "checkout", args.branch, "--", f])
        if rc.returncode != 0:
            print(f"  WARN: checkout failed for {f}: {rc.stderr}", file=sys.stderr)

    # Stage what we salvaged
    run(["git", "add", *to_salvage])
    r = run(["git", "diff", "--cached", "--stat"])
    print(r.stdout)

    # Prepare commit message (don't auto-commit — user reviews first)
    msg_path = REPO / ".git/SALVAGE_MSG"
    msg = (
        f"salvage: bring code from {args.branch} to main\n\n"
        f"Item NULLed but produced substantive code/tests. Bringing those\n"
        f"forward so downstream items have access. State files (QUEUE.yaml,\n"
        f"LOGBOOK.md, CHARTER.md) intentionally NOT touched — they remain\n"
        f"main's authoritative version.\n\n"
        f"Files salvaged ({len(to_salvage)}):\n"
        + "\n".join(f"  {f}" for f in to_salvage)
        + "\n\nReview with `git diff --cached`, then commit with:\n"
          f"  git commit -F .git/SALVAGE_MSG\n"
    )
    msg_path.write_text(msg)
    print(f"\nstaged. review with `git diff --cached` then commit:")
    print(f"  git commit -F .git/SALVAGE_MSG")
    print(f"\n(commit message draft in {msg_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
