# Batch 20: the one strict full run, green

Recorded by the Release Engineer (bd; a6 before the 19:33 reboot), 2026-10-14. bd merged the batch, started the run and read its result.

**What batch 20 is:** beta `486687ed` (batches 17–19) plus slice 2 of the AI-call record at `0a9411b5`, the Verifier's final tip. The merge is `d322f6d6`, with no conflicts.

- Every slice file at `d322f6d6` equals `0a9411b5`.
- Every batch 19 file equals `486687ed`.
- Batch 19 (`e68f08cd..486687ed`) and slice 2 change no file in common.
- The batch adds migrations `students` 0032, 0033 and 0034. `makemigrations --check --dry-run` on the merge tip: "No changes detected".

**Hooks on the merge tip `d322f6d6`:** `pre-commit run --all-files`, 22:54:02 to 22:56:35, exit 0. The table is in `hook_table_d322f6d6.txt`: 26 hooks, 25 Passed, 1 Skipped (broken symlinks, no files), none Failed. The hooks changed no files.

**Command:** `strict_bundle.sh d322f6d6 b20 Grade-Automator-Plus-beta-batch-20 task/beta-batch-20`. A copy is beside this file (sha256 `93215098…`). It is the batch 19 script plus the SM's rules of 2026-10-14:

- the load is checked AFTER the pre-checks, and the suite starts only under 8 (or the script stops and tells the SM after 20 minutes);
- the memory cap is available memory − 2 GB (at least 6G);
- a guard SIGKILLs the run's own scope if available memory drops under 1.5 GB.

The memory stop was drilled at 16:02 and 16:04 before this run. The run kept rules 12, 13, 16 (one inhibitor around the whole script), 17 and 18, plus the shared lock, the 3600 s timeout and the 300 s stall watchdog.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `d322f6d6` | 2026-10-14 22:56:56 to 23:05:54 (498 s of tests; 538 s wall) | **Ran 7846 tests, OK (skipped=30)**, exit 0. FAIL/ERROR headers: 0 across the whole log. No suspend; the watchdog never fired; the memory guard never acted. The script's own pre-checks found mypy Passed and makemigrations "No changes detected". |

**Tests per app** (they sum to 7846): ai_processor 1231, assignments 767, audit 376, AutoGrader 754, billing 2216, classrooms 567, dashboard 294, students 800, users 841. Against batch 19 (7692), counted by each test's own line:

- ai_processor is +105, the slice's new modules: tests_reading_config 22, tests_reading_run 40, tests_reading_pipeline 42, tests_step_run_adopted 1.
- students is +49: tests_reading_label_columns 22, tests_reading_label_routes 10, tests_reading_label_static 17.
- `ai_processor/tests_reading_pinned.py` holds shared fixture data (`PINNED`) and no tests.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran, and all 24 of its tests are ok, counted by their own test lines.

**Load and memory.** The load was 3.67 2.29 1.27 at the suite's start, measured after the pre-checks, and 4.21 5.02 3.25 at the end. Available memory was 24,859 MB at the start, giving a 22G cap. The lowest available memory during the run was 19,930 MB.

**The log:** `strict_b20.log.xz`, 9,288,505 bytes unpacked, sha256 `23b6c369af398125e1a37203ed5d8b9dfecbf527c34fed7d7eaa88b9aa4c34de`. Unpacking it was checked to give the identical bytes. `strict_b20.summary.txt` is the script's summary.

**Disclosed: blocked outbound = 8.** These are the two blocked attempts to reach Google's token address from `users/views.py` `_google_auth` (line 2131), inside the Google sign-in tests (H-226), as in batches 17 and 19. Every test passed. No batch 20 file is on that path.

**Not shown by this run:** the three migrations on a database the size of live or beta. 0034's validation time grows with the number of submissions and has not been measured. Anything about live data. The paid AI path (no real-model test ran).

**After the run tip:** the commit that adds this record changes files under `docs/` only.
