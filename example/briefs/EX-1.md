# EX-1 — Example item brief

This is a minimal example of an item brief. The autopilot session reads
this file as the implementation reference. The pre-registered acceptance
in `QUEUE.yaml` is the verdict criterion; this file is the *how*.

## Goal

Implement `<<REPO_ROOT>>/utility/example.py` with a `clean_input(s: str) -> str | None`
function that:

- Returns the trimmed string for normal input
- Returns the empty string for empty input
- Returns the trimmed string for unicode input (NFC-normalised)
- Returns `None` for non-string input

## Acceptance

See `QUEUE.yaml` item EX-1.

## Notes

Implementation should be ~30 lines of Python. No external dependencies
beyond the standard library.
