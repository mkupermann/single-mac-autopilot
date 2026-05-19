"""autopilot_compare_runs — aggregate long-run result.json files.

Scans ~/.<<project>>/long-run/*/result.json and produces a comparison
table. Useful for cross-run analysis when multiple variants of the
same hypothesis have been tested.

Output: stdout table + optional Markdown summary file.

Usage:
    python tools/autopilot_compare_runs.py
    python tools/autopilot_compare_runs.py --md out.md
    python tools/autopilot_compare_runs.py --filter encoder-free
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LONG_RUN_DIR = Path.home() / ".<<project>>/long-run"


def load_results(filter_substr: str | None = None) -> list[dict]:
    """Find all result.json files; load + filter."""
    results = []
    if not LONG_RUN_DIR.exists():
        return results
    for result_path in sorted(LONG_RUN_DIR.glob("*/result.json")):
        try:
            data = json.loads(result_path.read_text())
        except Exception as exc:
            print(f"WARN: failed to load {result_path}: {exc}", file=sys.stderr)
            continue
        if filter_substr:
            haystack = " ".join([
                str(data.get("item_id", "")),
                str(data.get("title", "")),
                str(data.get("hypothesis", "")),
            ]).lower()
            if filter_substr.lower() not in haystack:
                continue
        results.append(data)
    return results


def render_table(results: list[dict]) -> str:
    """Plain-text aligned table."""
    if not results:
        return "(no results)"
    cols = ["item_id", "verdict", "attempts", "finished_at", "title"]
    widths = {c: len(c) for c in cols}
    for r in results:
        for c in cols:
            widths[c] = max(widths[c], len(str(r.get(c, ""))[:50]))
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    sep = "  ".join("-" * widths[c] for c in cols)
    rows = []
    for r in results:
        rows.append("  ".join(str(r.get(c, ""))[:50].ljust(widths[c]) for c in cols))
    return header + "\n" + sep + "\n" + "\n".join(rows)


def render_markdown(results: list[dict]) -> str:
    """Markdown table + per-result expanded hypothesis."""
    if not results:
        return "# Long-run comparison\n\n(no results)\n"
    out = ["# Long-run comparison\n"]
    out.append(f"_{len(results)} run(s) loaded from `{LONG_RUN_DIR}`._\n")
    out.append("| item_id | verdict | attempts | finished_at | hypothesis (short) |")
    out.append("|---|---|---|---|---|")
    for r in results:
        hyp = (r.get("hypothesis") or "").replace("|", "\\|").replace("\n", " ")
        out.append(
            f"| {r.get('item_id', '?')} "
            f"| {r.get('verdict', '?').upper()} "
            f"| {r.get('attempts', '?')} "
            f"| {r.get('finished_at', '?')} "
            f"| {hyp[:120]} |"
        )
    out.append("\n## Per-item details\n")
    for r in results:
        out.append(f"### {r.get('item_id', '?')} — {r.get('verdict', '?').upper()}\n")
        out.append(f"**Title:** {r.get('title', '?')}\n")
        out.append(f"**Hypothesis:** {r.get('hypothesis', '?')}\n")
        out.append(f"**Env:** `{json.dumps(r.get('env') or {})}`\n")
        out.append(f"**pytest target:** `{r.get('pytest_target', '?')}`\n")
        if r.get("log_tail"):
            out.append(f"\n<details><summary>log tail</summary>\n\n```\n{r['log_tail']}\n```\n\n</details>\n")
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--md", metavar="FILE",
                   help="write Markdown summary to FILE in addition to stdout")
    p.add_argument("--filter", metavar="SUBSTR",
                   help="only include results where item_id/title/hypothesis contains SUBSTR")
    args = p.parse_args()

    results = load_results(args.filter)
    print(render_table(results))
    if args.md:
        Path(args.md).write_text(render_markdown(results))
        print(f"\nMarkdown summary written to {args.md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
