# Gate 1 (reads only): batch 13h, tip 84e31a74 (beta 3f2ad13e + H-191)

**Verifier:** 1a. **Date:** 2026-10-08, 17:37 to 17:39 WAT. Reads only, no test run. Branch `task/beta-batch-13h` @ `84e31a7445d052931f7fcf62736d2798fc265141`.

**Verdict: VERIFIED** (reads only; the full run is 0b's, once, and I repeated none of it).

## What I checked
- **Ancestry and shape.** Beta `3f2ad13e` is an ancestor. The tip is the merge `f53e1f0b` (parents `3f2ad13e` and `b7bccf3c`), then docs commit `e678c051`, then record `84e31a74`. `git merge-tree --write-tree 3f2ad13e b7bccf3c` gives `877d699e…`, the merge's own tree (clean); `diff-tree --cc` on the merge is empty (nothing on neither parent). Working tree clean.
- **Non-docs diff against beta:** exactly `assignments/prosemirror_converter.py` and `assignments/tests_sanitizer_threads.py`. Between the verified `b7bccf3c` and the tip: no non-docs file differs. No migration, settings, requirements, beat or management-command file changed.
- **The code.** The module-level shared `_CLEANER` is replaced by `_new_cleaner()`, called once per `sanitize_editor_html` call; the allowlists and the CSS sanitizer stay shared plain data. No other reference to `_CLEANER` and no other `Cleaner(` construction exists outside docs and tests, so no use of the shared cleaner is left.
- **The full-run log** (`strict_b13h.log.xz`, unpacked sha256 `e7fd5a9b…`, equal to the record's): one Ran line, `Ran 6252 tests`, `OK (skipped=28)`; 28 skip lines, the usual reasons (opt-in AI, load, network, daemonic worker); 6252 distinct test ids, compared with batch 13's 6247 ids: 5 new (all in `assignments.tests_sanitizer_threads`), 0 gone. The summary file agrees (per-app counts sum to 6252, watchdog 0, FAIL or ERROR headers 0, blocked outbound 0). The word BLOCKED appears in the log only in billing text (a wallet "consumption is now BLOCKED" message), not as network blocks.
- **The wording.** The record says crash and wedge OBSERVED 5 of 5 on the old code (the author's runs) and mixed text OBSERVED by Verifier 2's probe with counts and the no-saved-sample limit; this matches Verifier 2's record (line 48 on). It says plainly that a green run does not show the race gone.
- **Pattern check** of the added lines in `assignments/`: 0 hits for password, secret or token assignments or address-with-password forms.

## Notes
- N1. I did not run mypy or `makemigrations --check` myself; the record cites the script's own pre-checks (mypy Passed, no changes). Say the word and I do it in a slot as for earlier batches.
- N2. Rule 21: the record does not name the worktrees removed or to be removed (it is 0b's checklist line); nothing for me to check here.
- N3. The race itself is timing-dependent; no run proves it gone (the record says so).
