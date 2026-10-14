# Batch 18: the one strict full run, green

Recorded by the Release Engineer (a6), 2026-10-14. a6 merged the batch, started the run and read its result.

**What batch 18 is:** beta `8a64c35b` (batch 17) plus three verified tips, each merged at the tip the Verifier named, with no conflicts:

- H-216 `70af3119` (merge `67016aa8`);
- H-225 `3677b75e`, built on H-216's code `daac5cf5` (merge `406b75f0`);
- H-223 `4fded380` (merge `7ebdfd6b`).

On the merge tip, every non-docs file except `students/views.py` is identical to the verified tip that changed it. `students/views.py` is the one real three-way merge: its diff from H-225's tip is exactly H-223's own diff from `913f248f`, line for line. No branch adds a migration.

**Hooks on the merge tip `7ebdfd6b`:** `pre-commit run --all-files` exit 0. The table is in `hook_table_7ebdfd6b.txt`: 26 hooks, 25 Passed, 1 Skipped (broken symlinks, no files), none Failed. The hooks changed no files.

**Command:** `strict_bundle.sh 7ebdfd6b b18 Grade-Automator-Plus-beta-batch-18 task/beta-batch-18`. A copy is beside this file, unchanged since batch 17 (output folder `GAP-a6-runs`, `MemoryMax` 9G). About 9 GB was available at the start, and the cap was not reached.

The run kept rules 12, 13, 16 (one inhibitor around the whole script), 17 and 18, plus the shared lock, the 3600 s timeout and the 300 s stall watchdog.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `7ebdfd6b` | 2026-10-14 14:20:00 to 14:30:08 (571 s of tests; 608 s wall) | **Ran 7673 tests, OK (skipped=30)**, exit 0. FAIL/ERROR headers: 0 across the whole log. No suspend; the watchdog never fired. The script's own pre-checks found mypy Passed and makemigrations "No changes detected". |

**Tests per app** (they sum to 7673): ai_processor 1126, assignments 767, audit 376, AutoGrader 754, billing 2216, classrooms 552, dashboard 290, students 751, users 841. Against batch 17 (7647), only students changed: +26, which are the new H-216, H-225 and H-223 test modules.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran, and all 14 of its tests are "... ok".

**Load average (1 minute):** 5.34 at the start and 8.25 at the end. Four parallel workers, plus another project's single-module test run on the same laptop at the start.

**The log:** `strict_b18.log.xz`, 9,213,149 bytes unpacked, sha256 `6724e64c60e15d5b4588f904719880b49fa6a94b5b0c5746d2e0bb9c8fae015f`. Unpacking it was checked to give the identical bytes. `strict_b18.summary.txt` is the script's summary.

**Disclosed: the Google token call (H-226) again, in a different form.**
- The script's `blocked_outbound` count is 0. But the log has two tracebacks of an attempt to reach `oauth2.googleapis.com/token`, from `users/views.py` `_google_auth` (lines 2093, 2131, 2137), the same path as batch 17's two blocked attempts.
- This time the name lookup failed ("Temporary failure in name resolution") before any connection was tried, so the network guard (H-39), which acts on connect, had nothing to block. The lookup itself was a real DNS query from a test.
- Every test passed. No batch 18 file is on that path. This is added to H-226.

**Not shown by this run:** anything about live data; the paid AI path (no real-model test ran).

**After the run tip:** the commit that adds this record changes files under `docs/` only.
