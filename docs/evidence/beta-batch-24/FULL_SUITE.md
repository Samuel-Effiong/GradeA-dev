# Batch 24: two strict full runs; the second is green

Recorded by the Builder (a0), 2026-10-25. The Senior Manager built the batch and granted both runs. The Builder
started them and read their results. Gate 1 (the package read), the merge to beta and the push are the Senior
Manager's.

**What batch 24 is:** beta `b02de3fb` (batch 23) plus three `--no-ff` merges, all clean:

- `ad791b66`: H-254 (the second grader tries claude-haiku-5.5 first) at its first frozen tip `6bbdce5f`.
- `c666408a`: H-215a (automatic grading skips papers graded meanwhile, and retries outages without duplicate rows)
  at its frozen tip `9ef2f18e`.
- `b5fa15bc`: H-254's follow-up at its new frozen tip `d824aa43`. The benchmark replay pins the second grader's
  recorded order.

The run 1 evidence commit `36f7d1e8` sits between `c666408a` and `b5fa15bc`.

## Run 1 on `c666408a`: red, kept on the record

Ran 8311 tests, FAILED (failures=1, skipped=30). The one failure was `tests_benchmark_golden`: H-254's haiku-first
second grader had no recordings in the benchmark replay. `RED_RUN.md` has the run, the cause and the Senior
Manager's ruling, and the logs are in `red_c666408a/`. The fix is H-254's follow-up. Its gate, on `e10bc08a`, was
green: 361 OK, and mutant B1 killed for its written reason. Its evidence is in
`docs/evidence/h254-second-opinion-haiku-first/pin/`.

## Run 2 on `b5fa15bc`: green

- The only code change since run 1 is the pin in `ai_processor/benchmark/runner.py`: 11 added lines, four of them
  code and the rest docstring. Every other change is under `docs/`. Against beta `b02de3fb`, 70 files changed.
- No migration. `makemigrations --check --dry-run` on the merge tip: "No changes detected". This is the script's own
  pre-check; mypy passed too.
- The rows' own gates are recorded in:
  - `docs/evidence/h254-second-opinion-haiku-first/`: the gate on `92854ca9` (788 OK, 4/4 killed) and the pin gate
    on `e10bc08a`;
  - `docs/evidence/h215a-auto-grade-resilience/`: the gate on `f92afb2c` (866 OK, 12/12 killed, 19 written reds).

**Hooks on the merge tip `b5fa15bc`:** run by the Senior Manager. `pre-commit run --all-files` exited 0. The table is
in `hook_table_b5fa15bc.txt`: 26 hooks, 25 Passed, 1 Skipped (broken symlinks, no files to check), none Failed.

**Command:** `strict_bundle.sh b5fa15bc b24r2 Grade-Automator-Plus-beta-batch-24 task/beta-batch-24`.
- The same script as run 1 (sha256 `756dc7e3…`). A copy is beside this file.
- It is batch 23's script with its logs in the Builder's `b24` folder. Everything else is the same: rules 12, 13, 16,
  17 and 18, the shared lock, the 3600 s timeout, the 300 s stall watchdog, the load check after the pre-checks, the
  cap of available memory − 2 GB, and the 1.5 GB guard.
- The new label `b24r2` kept run 1's files.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `b5fa15bc` | 2026-10-25 10:26:55 to 10:36:43 (557 s of tests; 588 s wall) | **Ran 8311 tests, OK (skipped=30)**, exit 0 |

- FAIL/ERROR headers across the whole log: 0. "... FAIL" and "... ERROR" result lines: 0.
- No suspend. The stall watchdog never fired, and the memory guard never acted.

**Tests per app** (they sum to 8311): ai_processor 1408, assignments 767, audit 377, AutoGrader 762, billing 2216,
classrooms 640, dashboard 430, students 867, users 844.
- Against batch 23 (8286) that is +25, counted by each test's own line.
- ai_processor +7: `tests_h254_second_opinion_haiku_first` (new).
- students +18: `tests_h215a_auto_grade_resilience` (new).
- Every other app is unchanged. H-254's change to an H-205 test, and the pin, added no tests.

**The benchmark golden test** (run 1's failure) ran and passed. Its own log lines come between its name and its
"ok", and the log has no failure block.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran: 24 tests, counted by their own test lines, and none failed.
For ten of them the "ok" is on a later line, not the test's own line.

**Load and memory:**
- Load 9.49 at the first pre-start check (10:23:48), so the start waited. The load was 7.95 at the repeated check
  (10:26:39), and 7.86 7.90 6.84 at the suite's start, measured after the pre-checks. The load at the end was
  7.38 7.29 6.93. Vezi's runs were sharing the laptop.
- 10,897 MB available at the start, so an 8G cap. The lowest available during the run was 9,636 MB.

**The log:** `strict_b24r2.log.xz` (packed with `xz -9e`; 504,780 bytes, under the 500 KB hook limit).
- 9,477,888 bytes unpacked, sha256 `a4ed82d5eadcf0572c307e5b0c299506ba4f65a59eb81142a7c1786232097860`.
- Unpacking it was checked to give the identical bytes.
- `strict_b24r2.summary.txt` is the script's summary.

**Disclosed: blocked outbound = 8.** The same count as in batches 17 and 19 to 23.
- All 8 were to the same Google address (`2607:f8b0:4002:c05::5f`, port 443).
- Four tracebacks show `users/views.py` `_google_auth` (H-226). The other four are interleaved with other tests'
  output from the parallel workers, so their frames cannot be read in order.
- Every test passed, and no batch 24 file is on that path.

**Not shown by this run:**
- live provider behaviour (the grading tests replace only the provider call);
- the benchmark with haiku as the second grader (that needs re-recording, which is live and paid; the Senior Manager
  will put it to the owner);
- the concurrency race that H-215a's reset guards against (named in its NOTES; no test can make it);
- anything about live data.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
