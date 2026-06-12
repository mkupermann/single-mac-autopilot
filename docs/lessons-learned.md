# Lessons learned

Failure modes encountered while building this pipeline against a real
research project (`mkupermann/vibrasim`). Each one is now embedded in
the code; this document exists so the next person doesn't have to
rediscover them.

These are **plumbing** failures (launchd, git, YAML, pytest). The
**discipline** failures — ways a pre-registered item yields a verdict
that looks clean but proves nothing, which no hook can catch — live in
the companion `epistemic-failure-modes.md`, with matching rules in
`CHARTER.template.md`.

## macOS 15 silently denies FDA to /bin/bash from launchd

Symptom: launchd job exits 126. Stderr shows `shell-init: error
retrieving current directory: getcwd: cannot access parent directories:
Operation not permitted`, followed by `/bin/bash: <script>: Operation
not permitted` for any file under `~/Documents/`, `~/Desktop/`, or
`~/Downloads/`.

Cause: Even when `/bin/bash` is explicitly granted Full Disk Access in
System Settings, macOS 15 does not honor the grant for launchd-spawned
processes. The grant appears in the FDA list but is silently inactive
because launchd's child-process context isn't covered by the per-binary
FDA entry.

Workaround: place launchd wrapper scripts outside TCC-protected paths.
`~/.<<project>>/autopilot/run_wrapper.sh` works. The repo itself can stay
in `~/Documents/` as long as launchd only invokes scripts that live
elsewhere; from those scripts, bash *can* access files inside
`~/Documents/`.

## Headless Claude commits referenced files that were never staged

Symptom: `git status` after a session shows code changes; the commit
message says everything was added. But `import` fails because the
referenced module file is missing.

Cause: The headless session ran `git add <specific-files>` followed by
`git commit`, but missed one. The verdict commit said PASS but pytest
on the actual branch state fails immediately.

Workaround: postflight runs the pytest verifier itself, against the
file system as committed, with no reliance on the session's
self-report. If the session claims PASS but pytest disagrees, the
verdict is NULL.

## yaml.safe_dump reformat triggers pre-commit hook false-positive

Symptom: postflight commit rejected by pre-commit hook with
"Autopilot tried to modify a `preregistered_acceptance:` block".
Diff inspection: the block content is unchanged; only the YAML
serializer reformatted indentation / quotes.

Cause: `yaml.safe_dump(queue_dict, sort_keys=False, allow_unicode=True)`
roundtrips through a parser+emitter and emits canonical form, which
differs character-by-character from the human-written original. The
hook's diff scan sees added/removed `- "..."` list items and flags
them as protocol violations.

Workaround: postflight updates `QUEUE.yaml` via in-place text regex,
not via yaml.dump. Only the three runtime fields (status, attempts,
last_session) of the current item are touched. All other items, all
comments, all whitespace are byte-identical before and after.

## @pytest.mark.slow integration tests hang postflight for hours

Symptom: postflight's pytest invocation runs for 6.5 hours without
exiting. The wrapper holds its lock; supervisor sees lock and exits
clean every 30 min. Total stuck time exceeds 12 h.

Cause: an item's acceptance entry like `"tests/flux/test_learning.py
PASSES"` runs *all* tests in that file when given as a pytest target.
`@pytest.mark.slow` tests are NOT excluded by default; the marker only
affects collection filters like `pytest -m "not slow"`.

Workaround: postflight wraps its pytest invocation with
`timeout=1800` (30 min). On timeout, verdict is NULL with a
distinctive log line. The wrapper-level lock is released and the
queue advances. Items that need genuinely long runs go into the
separate `long_run_dispatcher.py` pipeline, which is uncapped at
the substrate level (24 h hard cap on the wrapper instead).

## Same-item-loop bug: passed item picked again forever

Symptom: queue shows an item passed on autopilot/<id>. Next launchd
slot fires, preflight picks the same item again, postflight verifies
already-done work, marks passed (attempt 2/3). Repeat until attempts=3,
item becomes "failed" despite being correct.

Cause: postflight only commits `QUEUE.yaml` status changes to the
`autopilot/<id>` branch. The next preflight reads `QUEUE.yaml` from
main at startup. Main never learns the item passed.

Workaround: postflight does a "main-sync" step after committing on
the item branch: switch to main, take only the current item's runtime
fields, commit + push main. This requires a different env (clear
`AUTOPILOT_ACTIVE` env var) so the pre-commit hook's
"autopilot-on-non-autopilot-branch" check doesn't fire on a sync
commit that is itself the autopilot's responsibility per the charter.

## Branch isolation breaks downstream items

Symptom: R-3 implements F2 (cochlea + synthesis) on `autopilot/R-3`.
R-LR-1 expects `tests/flux/test_encoder_free_training_run.py` to
exist. The test only lives on `autopilot/R-11` because main was
branched-from before R-11 ran. Pytest exits 0.01s with "file not
found"; dispatcher marks NULL.

Cause: every item branches from main, runs in isolation, and its
outputs commit on `autopilot/<id>`. Main only gets the runtime
status sync, not the code. Downstream items branch from main again
and don't see prior items' code.

Workaround (manual today): between items, the human (or this Claude
session in the role of supervising agent) brings code from passed
items onto main via `git checkout autopilot/<prev-item> -- <code>
<tests>`. A scripted "salvage" workflow is on the TODO list.

## Postflight TypeError on YAML-null status

Symptom: postflight succeeds at running tests and committing, then
crashes with `TypeError: unsupported format string passed to
NoneType.__format__` during mail body construction. Verdict was
correctly synced to main; only the email is missing.

Cause: YAML `null` parses to Python `None`. The mail body builder
uses f-strings with format specs like `:11s` that don't accept
`None`. After an item's status becomes `null` via verdict, any
subsequent postflight that reads it fails.

Workaround: postflight coerces all queue field accesses with
`str(value) if value is not None else "null"` before format-spec
use. The defensive coercion is added at every mail-body interpolation
site.

## Postmortem: what's still raw

- ~~Salvage workflow is manual.~~ **RESOLVED 2026-05-19**: `pipeline/salvage_branch.py` identifies code-vs-state changes and stages code-only commits for review.
  useful code, bringing that code to main requires human attention.
- ~~Cross-run comparison is manual.~~ **RESOLVED 2026-05-19**: `pipeline/compare_runs.py` aggregates `result.json` files (plain text + Markdown output).
  out_dirs; no aggregator tabulates them.
- Branch lifecycle is manual. Old autopilot/<id> branches accumulate;
  pruning is on the operator.
- Session log capture is plain-text. Structural mining of common
  failure patterns (the bugs in this list) was done by reading
  session.log; no analytical layer exists.

These are TODO items the pipeline will eventually fix. The
companion research repo (`vibrasim`) is the test-bed where each
fix is validated against real work before being merged here.

## Resolved 2026-05-19: conditional queueing in preflight

Preflight previously picked the first `queued` item regardless of the
`blockers:` field, leading to downstream items running before their
prerequisites passed. Fixed: preflight now scans each `blockers` line
for word-boundary matches against other item IDs in the queue and
requires those items to be `passed` before the candidate item is picked.

```python
def blockers_satisfied(item, idx):
    for line in (item.get("blockers") or []):
        for other_id, other_status in idx.items():
            if re.search(rf"\b{re.escape(other_id)}\b", line):
                if other_status != "passed":
                    return False
    return True
```

## Resolved 2026-05-19: standardized long-run result.json

`long_run_dispatcher.py` now writes a per-item `result.json` in the
item's `out_dir` alongside the LOGBOOK append. Schema includes
`item_id`, `title`, `hypothesis`, `env`, `pytest_target`, `verdict`,
`attempts`, `started_at`, `finished_at`, `log_tail`. The
`compare_runs.py` tool reads these for cross-run analysis.

## Still open: session log analytical layer

`session.log` accumulates structurally-similar bug patterns across
sessions (this lessons-learned doc was hand-extracted from it).
A grep/jq layer over `session.log` would surface recurring failure
modes automatically. Not blocking; recommended after the next batch
of sessions.
