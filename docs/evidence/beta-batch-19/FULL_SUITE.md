# Batch 19: the one strict full run, green

Recorded by the Release Engineer (a6), 2026-10-14. a6 merged the batch, started the run and read its result.

**What batch 19 is:** beta `e68f08cd` (batch 18) plus three verified tips, each merged at the tip the Verifier named, with no conflicts:

- H-230 `6cc9836e` (merge `7583d0af`);
- H-231 `6596ab6f` (merge `039504b5`);
- H-232 `ddf7806d`, tests only (merge `2afc8430`).

On the merge tip, every file each branch changed is identical to that branch's tip. No two branches change the same file, and batch 18 changed none of their files. No branch adds a migration.

**Hooks on the merge tip `2afc8430`:** `pre-commit run --all-files` exit 0. The table is in `hook_table_2afc8430.txt`: 26 hooks, 25 Passed, 1 Skipped (broken symlinks, no files), none Failed. The hooks changed no files.

**Command:** `strict_bundle.sh 2afc8430 b19 Grade-Automator-Plus-beta-batch-19 task/beta-batch-19`. A copy is beside this file, unchanged since batch 17 (`MemoryMax` 9G).

The run kept rules 12, 13, 16 (one inhibitor around the whole script), 17 and 18, plus the shared lock, the 3600 s timeout and the 300 s stall watchdog.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `2afc8430` | 2026-10-14 15:19:05 to 15:34:11 (843 s of tests; 906 s wall) | **Ran 7692 tests, OK (skipped=30)**, exit 0. FAIL/ERROR headers: 0 across the whole log. No suspend; the watchdog never fired. The script's own pre-checks found mypy Passed and makemigrations "No changes detected". |

**Tests per app** (they sum to 7692): ai_processor 1126, assignments 767, audit 376, AutoGrader 754, billing 2216, classrooms 567, dashboard 294, students 751, users 841. Against batch 18 (7673), classrooms is +15 (H-230's 5 and H-231's 10 new tests) and dashboard is +4 (H-232's 4).

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran, and all 24 of its tests are ok, counted by their own test lines.

**Load (disclosed).** The Senior Manager's bar is a 1-minute load under 8 at the start. The wait for it ended at 15:18:45 at 7.93, but that was measured before the script's pre-checks. When the suite itself started at 15:19:05, the load was 10.21 (9.79 and 7.22 over 5 and 15 minutes). It reached about 14 during the run and was 13.00 at the end. The CRM team was running three test suites on the same laptop. The SM let the run go on, with any timing-sensitive red to be judged in that light. None went red. From the next run on, the load is checked after the pre-checks.

**Memory (disclosed).** About 8 GB was available at the start, against the 9G cap. On the SM's word, a guard checked the laptop's available memory every 5 s from 15:19:54, ready to stop this run's own scope below 1.5 GB. It never acted. The scope's peak was 2004 MB, and available memory never fell below 6975 MB. The log is `strict_b19.memory.txt`.

**The log:** `strict_b19.log.xz`, 9,086,743 bytes unpacked, sha256 `000e13cd70c46bc17341987b3bccb95af9308b9488f40ad528f09e18b2a201ef`. Unpacking it was checked to give the identical bytes. `strict_b19.summary.txt` is the script's summary.

**Disclosed: blocked outbound = 8.** These are the two blocked attempts to reach Google's token address from `users/views.py` `_google_auth` (line 2131), inside the Google sign-in tests (H-226). It is the same form as batch 17's, where the network guard blocked the connection. Every test passed. No batch 19 file is on that path.

**Not shown by this run:** anything about live data; the paid AI path (no real-model test ran).

## Correction to batch 18's record

`docs/evidence/beta-batch-18/FULL_SUITE.md` (and batch 18's package) says `AutoGrader.tests_cache_bespoke_1114` had "all 14 of its tests" ok. **It had 24, all ok.** a6 counted only the lines where "... ok" sits on the test's own line, and missed the ten tests with docstrings, which print "ok" on the next line. The Verifier caught it at batch 18's Gate 1. The SM chose to correct it here rather than move batch 18's pushed tip. Batch 17's record undercounted the same way; its "every test in it is ok" stands. From batch 19 on, a6 counts a module's tests by their own test lines.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
