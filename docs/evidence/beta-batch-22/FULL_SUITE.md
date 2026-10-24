# Batch 22: the one strict full run, green

Recorded by the Builder (a0), 2026-10-24. The Senior Manager (011c73) built the batch and granted the run; the
Builder started it and read its result. Gate 1 (the package read), the merge to beta and the push are the Senior
Manager's.

**What batch 22 is:** beta `3d5174b0` plus two `--no-ff` merges, both clean:

- `edc2987a`: H-201 (per-call AI usage record) at its frozen tip `c7f3895f`;
- `b1228baf`: H-206 (zero-retention provider block, switch off by default) at its frozen tip `3432524f`, which was
  based on `c7f3895f`.

On the merge tip `b1228baf`:

- The tree equals `3432524f` (`git diff 3432524f b1228baf` is empty), and `edc2987a`'s tree equals `c7f3895f`.
- 94 files changed against beta: H-201's 60 (`3d5174b0..c7f3895f`) and H-206's 36 (`c7f3895f..3432524f`). They share
  two files, `AutoGrader/settings.py` (both settings kept, H-201's first) and `ai_processor/services.py`.
- One migration, `ai_processor/migrations/0006_aicallrecord.py` (additive, one new table). `makemigrations --check
  --dry-run` on the merge tip: "No changes detected".

Each row's own gate is in its evidence folder: H-201 `docs/evidence/h201-ai-call-usage-record/` (gate on `49698d4c`:
781 OK, 34/34 mutants killed) and H-206 `docs/evidence/h206-zdr-provider-block/` (gate on `e717a0ee`: 642 OK, 14/14
killed). Each folder also keeps its stopped run (test faults only).

**Hooks on the merge tip `b1228baf`:** `pre-commit run --all-files`, 11:51:18 to 11:54:40, exit 0. The table is in
`hook_table_b1228baf.txt`: 26 hooks, 25 Passed, 1 Skipped (broken symlinks, no files), none Failed. The hooks changed
no files.

**Command:** `strict_bundle.sh b1228baf b22 Grade-Automator-Plus-beta-batch-22 task/beta-batch-22`. A copy is beside
this file (sha256 `0f41f8d5…`). It is batch 21's script with two changes: its logs go to the Builder's folder, and a
memory cap under 6G stops it with exit 5 (was 8). It kept rules 12, 13, 16 (one inhibitor around the whole script),
17 and 18, plus the shared lock, the 3600 s timeout, the 300 s stall watchdog, the load check after the pre-checks
(start only under 8), the cap of available memory − 2 GB, and the guard that kills the run's own scope under 1.5 GB.

| Tip | Suite (WAT) | Result |
|---|---|---|
| `b1228baf` | 2026-10-24 11:40:06 to 11:50:54 (608 s of tests; 648 s wall) | **Ran 8266 tests, OK (skipped=30)**, exit 0. FAIL/ERROR headers: 0 across the whole log; "... FAIL"/"... ERROR" result lines: 0. No suspend; the watchdog never fired; the memory guard never acted. The script's own pre-checks found mypy Passed and makemigrations "No changes detected". |

**Tests per app** (they sum to 8266): ai_processor 1382, assignments 767, audit 376, AutoGrader 762, billing 2216,
classrooms 640, dashboard 430, students 849, users 844. Against batch 21 (7876), +390, counted by each test's own line.
Batch 22's two rows give 99 of them; the other 291 are beta rows landed after batch 21 (H-237 to H-253 and the
20 October hot fix), which have no full-run record in this folder series:

- ai_processor +151: `tests_call_record` 48 and `tests_call_record_static` 27 (H-201); `tests_provider_block` 24
  (H-206); `tests_h242_retranscription` 31; `tests_h253_answers_never_empty` 20; `tests_reading_config` +1.
- AutoGrader +8: `tests_h246_sentry_environment` 8.
- classrooms +51: `tests_h247_student_identity` 33, `tests_h251_grade_trend_filter` 10,
  `tests_h252_my_students_filter_withdrawn` 3, `tests_teacher_names_student_on_add` +5.
- dashboard +128: `tests_h237_at_risk_counts_due_work` 17, `tests_h238_assignment_analytics_accuracy` 10,
  `tests_h240_question_difficulty` 38, `tests_h241_question_breakdown` 18, `tests_h245_scaled_minimum` 7,
  `tests_h248_twenty_percent_cards` 20, `tests_h249_pass_rate_and_average_score` 16, `tests_h250_course_analytics` 18;
  less `tests_question_difficulty` 16 (removed).
- students +49: `tests_h242_submission_flags` 31, `tests_h253_flags_inside_answers` 18.
- users +3: `tests_mailerlite_sync` +3.
- assignments, audit and billing are unchanged.

**Rule 20:** `AutoGrader.tests_cache_bespoke_1114` ran, and all 24 of its tests are ok, counted by their own test
lines (the same 24 as batch 21).

**Load and memory.**

- The load was 2.42 4.16 3.87 at the suite's start, measured after the pre-checks, and 3.98 4.88 4.49 at the end.
- Available memory was 8,615 MB at the start, giving a 6G cap (the minimum). The lowest available memory during the
  run was 6,627 MB.
- Beside the run: the Senior Manager's H-205 provider test (network calls only, no database, no test runner) may
  have been running; no other GAP test run. Every test passed, so no result depends on it.

**The log:** `strict_b22.log.xz` (packed with `xz -9e` to stay under the 500 KB hook limit), 9,461,365 bytes unpacked, sha256
`d8191aad277d764f4f6c4e8aae77934f32d219766336817c571a7de4c7688843`. Unpacking it was checked to give the identical
bytes. `strict_b22.summary.txt` is the script's summary.

**Disclosed: blocked outbound = 8.** All 8 are attempts to reach one Google address on port 443, the token call from
`users/views.py` `_google_auth` inside the Google sign-in tests (H-226), as in batches 17 and 19 to 21. Every test
passed. No batch 22 file is on that path.

**Not shown by this run:** live provider behaviour (zero-retention routing is off by default; H-206's tests mock the
SDK), the 0006 migration on the live database, anything about live data.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
