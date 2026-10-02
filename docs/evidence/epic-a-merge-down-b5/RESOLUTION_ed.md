# Bundle 5 merge-down (beta 74bfc8d3 → phase2/epic-a cc22bc03): resolution record

Author of the resolution: ed (Security), 2026-10-02. 0b created the branch
`task/epic-a-merge-down-b5` and the in-progress merge, checks the result and commits it.
ed gates it; v2 verifies. Merge base: 67a06817.

## The five conflict hunks (SM rulings, 2026-10-02)

| File | Hunk | Resolution | Why |
|---|---|---|---|
| `billing/license_service.py` | the "Removed teacher … Expired %d credit buckets." log string | **epic** | Both sides are ids-only. Beta's is two adjacent literals that join with no separator ("…license %sExpired %d…"); d5 fixes that on beta to match. No test pins the line. |
| `billing/tasks.py` | imports | **both** | The epic's `audit_metrics` and beta's `beat_locks` / `single_instance` are each used. |
| `classrooms/tests_security_penetration.py` | one assertion | **epic** (S7d's form: `ROW_STAFF_EMAIL`, "Row 1: …") | The epic's own comment at that line says S7d's form wins over H-71's rowless message. Beta's two lines are the only beta-added lines absent from the result. |
| `users/signals.py` | `str(exc)` vs `type(exc).__name__`, twice | **beta** (H-80) | `str(exc)` can carry an address. These were the two epic-only lines H-80's guard would have flagged. |

`assignments/tasks.py` and `students/tests_h38_tasks_namespace.py` did not conflict and are
byte-identical to cc22bc03 (the epic side: S7b's coded H-38 refusal), as recorded for b4.

## The branch, commit by commit

| Commit | By | What |
|---|---|---|
| 05808f8a | 0b | **Rename follow-through for H1**, before the merge (SM ruling). The epic's `check-no-pii-in-logs` hook rejected the first merge attempt: H1 moved `billing/management/commands/backfill.py` to `scripts/one_off_backfill_stripe_schedules.py`, and the epic's `scripts/pii_log_baseline.txt` listed only the old path. This commit lists the new path beside the old one. |
| 5554811a | 0b | The merge of beta 74bfc8d3, with the five hunks above as ed left them (0b compared sha256 of the four files across an abort and redo). `git show --remerge-diff` lists exactly those four files. |
| 518771e2 | ed | **Epic-only follow-up required by beta's H-65 guard**, part 1: `audit/tests_sweep_beat_lock.py`. |
| 804258f0 | ed | Part 2: `@single_instance(max_hold=beat_locks.DAILY)` on both audit sweeps, plus the imports. |
| 88d3f29d | ed | The stale baseline line for the old backfill path removed. |
| (next) | ed | `billing/license_service.py` and `users/signals.py` leave the PII-log baseline. |

**5554811a alone fails one guard**: `AutoGrader.tests_beat_locks`
`test_every_beat_task_is_locked_or_exempt`, on the two epic-only beat entries below. That is
accepted (SM): only the branch tip is merged into `phase2/epic-a`, with `--no-ff`, so the
epic's first-parent history never has a red state. The gate's step 0 shows the failure.

## Epic-only follow-up required by beta's H-65 guard: the audit sweeps take the Beat lock

Cross-side guard rule. Beta's H-65 guard requires every `CELERY_BEAT_SCHEDULE` entry to use
`@single_instance` or be in `EXEMPT_BEAT_TASKS`. The epic's two audit sweeps
(`audit.tasks.sweep_audit_retention`, `sweep_audit_pii_short_retention`) are epic-only beat
entries and were neither. Both now carry `@single_instance(max_hold=beat_locks.DAILY)`, like
every other daily task (`audit/tasks.py`, +4 lines). The SM chose the lock over an exemption:
an overlapping second sweep would delete nothing new but would write a second
`AUDIT_RETENTION_SWEEP` event. After the change the only unlocked beat tasks are the two
exempt ones.

`audit/tests_sweep_beat_lock.py` (7 tests) pins what the lock means for a sweep; mutants R3
and R4 remove each decorator.

### What happens to a sweep when Redis is unavailable (SM's question)

Under H-65 a locked task that cannot reach the lock store **fails closed**: it is skipped,
with an ERROR log naming the task and the lock key, and returns the "skipped" summary. For
the sweeps that means nothing is deleted or scrubbed and no `AUDIT_RETENTION_SWEEP` event is
written for that run (tests `TheLockStoreIsUnavailableTests`).

- **Daily retention sweep (06:00).** A skipped run delays deletion by one day. Nothing is
  lost: the sweep selects by cutoff (`occurred_at < now - 365 d` / `3 y`), so the next run
  that gets the lock deletes everything the skipped run would have. The missing self-record
  for the skipped day is the visible gap S3's G6 rule intends ("a stopped sweep is a gap").
- **PII short-retention sweep (06:30).** The promise is that `source_ip` / `user_agent` are
  blanked after `PII_SHORT_RETENTION_DAYS` (90). The sweep is daily, so a row is blanked
  between 90 d and 91 d after it was written even with no failure. One skipped run moves the
  upper bound to 92 d: **one extra day per skipped run, never more**, because the sweep also
  selects by cutoff and the next run catches up
  (`test_the_next_run_does_the_skipped_runs_work`). It can fall further behind only if the
  lock store is down at 06:30 on consecutive days.
- **Does the lock add a new way to miss a run?** Only narrowly. The lock store is the
  `default` cache's Redis. If the Celery broker is the same Redis, a run cannot be delivered
  while it is down, lock or no lock. The new case is a Redis that answers the broker but not
  the cache connection at that minute; then the sweep is skipped where before it would have
  run. The Beat watchdog reports a lock store that is not answering
  (`test_the_watchdog_reports_a_lock_store_that_is_not_answering`).

## The PII-log baseline (`scripts/pii_log_baseline.txt`)

- The stale line `billing/management/commands/backfill.py` is removed (88d3f29d): the file
  does not exist after the merge. The new path stays listed (05808f8a); the script has 3
  flagged lines (92, 113, 120), unchanged by the move.
- `billing/license_service.py` and `users/signals.py` are **removed from the baseline** (SM
  ruling, from d5's read of the hook). `scripts/check_no_pii_in_logs.py`'s scanner finds
  **0 flagged calls** in each on the merged tree (it also flags `first_name`, `last_name`
  and `get_full_name`), and the whole hook passes with the two entries gone
  ("OK: no new PII-in-logs violations", exit 0). The hook now guards both files.
- Still listed, with flagged calls on the merged tree: `billing/access_control.py` 7,
  `billing/qa_time_travel.py` 2, `billing/services.py` 17, `billing/stripe_service.py` 16,
  `billing/tasks.py` 11, `billing/views.py` 2 (H-23 / H-91).

## Cross-side checks done before the gate (static, nothing run)

Beta's guards against epic-only code:
- **H-80 log guard** (`billing/tests/test_logs_carry_no_email.py`): its `leaks()` helper run
  over the merged `billing/license_service.py` and `users/signals.py`: **0 flagged calls**.
  (Before the merge the epic had 32 and 12; pre-check output in
  `~/Documents/Projects/GAP-ed-scripts/merge-down-b5/`.) `billing/services.py` is not guarded;
  it has 18 flagged calls on the merged tree (H-91, d5).
- **H1 commands guard**: 24 command modules on the merged tree, all with a `Command` class,
  the epic-only `audit_volume_report` included. `billing/management/commands/backfill.py` is
  gone and `scripts/` is not a package.
- **H-73 raw-Redis guard** (`RAW_CLIENT_USERS` in `tests_cache_invalidation_coverage.py`): a
  grep of production modules finds raw-client use only in the four listed modules; no
  epic-only module (the audit failed-auth cap uses the cache API).
- **H-65 beat-lock guard**: fails on the merge commit for the two audit sweeps; fixed by the follow-up above.

Epic's guards against beta's new code (`tests_reason_codes`, `tests_error_messages`, the
sync-only email-code guard, `audit.tests_route_coverage`, `audit.tests_history_guard`): not
checkable by reading alone; they are in the gate's first step.

Tests grepped for text a resolution removes: nothing asserts on the two `users/signals.py`
log lines or on the license log string; `NOT_A_STUDENT_MESSAGE` is still asserted by
`classrooms/tests_h71_student_add_role.py` and by the S7d-form line.

## Auto-merged files changed on both sides (for the remerge-diff review)

For each, every line added by either side since the merge base is present in the result
(checked by script over these twelve files; the only two absent lines are beta's in
`tests_security_penetration.py`, above).

v2's pre-review found a third beta-added line absent from the result, in a file my script did
not cover: beta's `print(f"FAILED: {user_sub.id} (user {user_sub.user.email}): {exc}")` in
`scripts/one_off_backfill_stripe_schedules.py`. The merged script has the epic's
`user_sub.user_id` form, because git carried the epic's ids-only edit of `backfill.py` through
H1's rename. That is the right result; it also means the script's flagged-line count on the
merged tree (3) can differ from beta's.

| File | Beta since base | Epic since base | Note |
|---|---|---|---|
| `AutoGrader/settings.py` | +7/-0 | +47/-2 | additive both ways (H2's switch; the epic's audit settings and beat entries) |
| `AutoGrader/tests_cache_invalidation_coverage.py` | +732/-1 | +4/-0 | H-73's guard arrives; the epic's four lines kept |
| `billing/license_views.py` | +35/-0 | +69/-34 | additive |
| `billing/services.py` | +47/-9 | +33/-9 | both kept |
| `billing/tests/test_other_school_before_subscription.py` | +267/-0 | +267/-0 | identical on both sides (H-78 fold) |
| `classrooms/serializers.py` | +19/-18 | +10/-13 | both kept |
| `classrooms/services/__init__.py` | +2/-0 | +2/-0 | identical on both sides |
| `classrooms/services/enrollment.py` | +15/-3 | +28/-6 | the result equals the epic's file: beta's change was already there (H-71 copy in S7d) |
| `billing/license_service.py` | +133/-88 | +224/-63 | H-80's ids-only calls and H-86's reason lines arrive; the epic's lines kept |
| `billing/tasks.py` | +22/-35 | +6/-0 | both kept |
| `users/signals.py` | +20/-25 | +5/-5 | the result equals beta's file: the epic's five email→id changes are the same lines in H-80 |

## Gate round 1 (RED) and the test-only fixes

Step 1 at 3cfc40f8 (2026-10-02 13:33–13:53 WAT; the machine was shared, load about 27):
**Ran 745 tests, FAILED (failures=6, errors=2, skipped=1)**. Log:
`run1_failed_3cfc40f8_modules_and_guards.txt` (last 200 lines, all 8 headers and the totals;
full log sha256 prefix `4ef376a310656971`, kept in `~/Documents/Projects/GAP-evidence-logs/`).
The mutants did not run. All eight are tests that collide with intended behaviour; no
production code changed in the fix.

| Tests | Why red | Fix (test-only) |
|---|---|---|
| `audit.tests_retention_sweep.ConcurrentSweepTests`, 2 errors | They run two sweeps at once and parse both summaries. With the Beat lock one run is skipped, and its summary has no counts. I had the module in the gate but had not read it against the lock. | `runs_that_worked()`: the totals are summed over the runs that did the work; every other result must be the lock's skip summary. The assertion that each row is processed exactly once is unchanged. |
| `AutoGrader.tests_beat_locks` `test_every_guarded_beat_task_skips_while_another_run_holds_it`, 1 failure | Beta pins `len(guarded) == 21`; the epic has 23 with the two audit sweeps. | The pin is 23 on the epic, with a comment. |
| `classrooms.tests_h71_student_add_role`, 5 failures (3 + 2 subtests) | Beta's H-71 module is new to the epic. It pins the rowless message for a bulk row, and compares whole response bodies across roles. On the epic a bulk row has S7d's row form (`ROW_STAFF_EMAIL`, "Row 1: …"), and a coded error carries a support reference that is new for every response. | The bulk assertion takes S7d's form (as `tests_security_penetration.py` does). The role comparison masks the `reference` value and still compares everything else byte for byte. |

**Divergences from beta these create** (future merge-downs keep the epic side of each):
`AutoGrader/tests_beat_locks.py` (the 23), `classrooms/tests_h71_student_add_role.py` (two
assertions and the helper), plus the ones recorded for b4 (`grade_engine_async`, the students
H-38 test) and `tests_security_penetration.py`'s S7d line.

## Gate round 2 (rule 15.4) at 798c06e2: green

2026-10-02 14:10–14:16 WAT. The SM accepted the reduced scope: only three test files differ
from 3cfc40f8 outside docs/, and the other 737 tests of round 1 passed on the same production
code.

| Step | Result | Log |
|---|---|---|
| Reproduce-first (round 1, step 0): beta's beat-lock guard and the new sweep test on the merge commit's `audit/tasks.py` | FAILED: 6 of the 7 sweep-lock tests fail, and `test_every_beat_task_is_locked_or_exempt` | `prefix_sweeps_unlocked_failing.txt` |
| The three touched modules + `audit.tests_sweep_beat_lock` | 49 tests OK | `r2_touched_modules.txt` |
| Resolution mutants R1–R6 | 6 of 6 killed, no survivors | `mutation_log.txt`, `mutation_results.json` |

Rule 17: the mutant runs used `PYTHONDONTWRITEBYTECODE=1`, and the `__pycache__` of each
mutated module's directory was deleted before each mutant and after each restore. Own
database (`test_epic_a_merge_down_b5_mut`), dropped afterwards. Rules 12, 13 and 16 (the
`idle:sleep:handle-lid-switch` prefix) on every run.

| Mutant | Killed by |
|---|---|
| R1 settings failure logs the exception text | `test_no_logger_call_formats_an_address` |
| R2 wallet failure logs the exception text | `test_no_logger_call_formats_an_address` |
| R3 the retention sweep has no beat lock | `test_every_beat_task_is_locked_or_exempt`; `test_every_guarded_beat_task_skips_while_another_run_holds_it`; `test_the_next_run_does_the_skipped_runs_work`; `test_the_retention_sweep_is_skipped_and_records_nothing`; `test_the_retention_sweep_is_skipped_with_an_error`; `test_the_two_sweeps_do_not_block_each_other` |
| R4 the pii sweep has no beat lock | `test_every_beat_task_is_locked_or_exempt`; `test_every_guarded_beat_task_skips_while_another_run_holds_it`; `test_the_next_run_does_the_skipped_runs_work`; `test_the_pii_sweep_is_skipped_and_records_nothing`; `test_the_pii_sweep_is_skipped_with_an_error` |
| R5 billing tasks loses the audit metrics import | `test_a_clean_expiration_emits_no_anomaly`; `test_a_failed_bucket_expiration_emits_the_anomaly_exactly_once` |
| R6 billing tasks loses the beat lock imports | the test run fails at import (`NameError` in `billing/tasks.py`), before any test |

## The guarded-task pin (SM's question)

`test_every_guarded_beat_task_skips_while_another_run_holds_it` now names the epic's own
guarded tasks (`EPIC_ONLY_GUARDED_BEAT_TASKS`: the two audit sweeps) and pins **beta's own
count, 21**, over the rest. So a guarded task added on beta fails here at the next
merge-down until the 21 follows beta's, and an epic-only one must be named. This commit is
test-only and came after round 2; the seven-app regression runs at the tip that has it.

## The seven-app regression at c32de6aa: 5134 passed, 1 planner-dependent test (H-95)

`manage.py test billing users classrooms ai_processor AutoGrader dashboard audit`, 12G, flock,
timeout 3600, `--verbosity 2`, `RACE_COST_*` / `AUDIT_BENCH*` / `ENABLE_GRADING_BENCHMARK`
unset. Script start 14:18:50 WAT; it waited for the machine-wide lock (a Vezi suite held it);
the tests started 14:24:11 and ended 14:40:07.

**Ran 5135 tests in 928.4s: FAILED (failures=1, skipped=14).** Per app (unique test ids in
the log): billing 2048, ai_processor 830, users 698, AutoGrader 533, classrooms 415, audit 341,
dashboard 270. Log: `run1_failed_c32de6aa_regression_seven_apps.txt` (last 200 lines, with
the FAIL header and the totals; full log sha256 prefix `d06175d7f617abed`, kept in
`~/Documents/Projects/GAP-evidence-logs/`).

The one failure: `audit.tests_volume_report.MeasuredTests.test_every_windowed_count_has_an_index_path`.
It runs EXPLAIN, with seq scans off, on each windowed count of `audit_volume_report` and
requires the plan's `Index Cond` to pin `action` or `retention_class`. For the `AUTH_LOGIN`
count the planner showed `audit_retention_ix` with `Index Cond` on `occurred_at` and `action`
as a `Filter` (cost 0.25..8.28, rows=1): a full scan of another index, on a table of four
rows, where it costs the same as the pinned scan.

Why this is not the merge: under `audit/` the branch differs from cc22bc03 only in `tasks.py`
(the two decorators) and test modules; no model, index, migration or command changed. The
same module alone, serially, three times at c32de6aa on a fresh test database each time:
**9 tests OK, three times** (`volume_report_alone_x3_before_fix_c32de6aa.txt`; a passing run
prints no plan, so which index each used is not shown).

SM ruling: the 12G run is not repeated; the test is made deterministic now, test-only, and
must still fail when an index it protects is missing. That closes backlog **H-95** on Epic A
(beta has no such test).

The fix (`audit/tests_volume_report.py`):
- `fill_and_analyse()`: before the EXPLAIN the test inserts 60 rows per action across both
  retention classes, spread over 300 days, and runs `ANALYZE`. With real statistics the
  pinned scan is clearly cheaper than a full scan of another index.
- `test_the_index_check_fails_when_an_index_it_protects_is_missing`: drops
  `audit_action_time_ix`, then `audit_retention_ix`, each inside a rolled-back savepoint, and
  requires the same check to raise. That is the proof the SM asked for, kept in the suite.

### After the fix, at 878ddf58 (rule 15.4; 2026-10-02 14:46–14:49 WAT)

| Run | Result | Log |
|---|---|---|
| The module alone, serially, fresh test database, three times | 10 tests OK, three times | `volume_report_after_fix.txt` |
| The module once with `--parallel 2`, under the machine-wide flock | 10 tests OK | same file |
| **Deliberate red**: the new test with `assertRaises` taken out, so the check's own failure is shown (own database, source restored afterwards) | FAILED (failures=2): one per dropped index. Without `audit_action_time_ix` the action count has no plan that pins `action`; without `audit_retention_ix` the class count falls to a bitmap scan with no pinned `retention_class`. | `volume_report_red_without_each_index.txt` |

So the regression's result stands as **5134 passed, 1 planner-dependent test, fixed test-only
at 21645f8c**. There is no single green run of the seven apps on the final tip; the tip
differs from c32de6aa only in `audit/tests_volume_report.py` and docs.

## The gate in one table

| What | Tip | Result |
|---|---|---|
| Reproduce-first: beta's beat guard + the sweep test on the merge commit's `audit/tasks.py` | 3cfc40f8 | red, as expected |
| Changed modules + both sides' guards (745 tests) | 3cfc40f8 | **RED**: 6 failures + 2 errors, all test-side; fixed test-only at 9f87eedf |
| The three touched modules + the sweep test | 798c06e2 | 49 OK |
| Resolution mutants R1–R6 | 798c06e2 | 6 of 6 killed |
| Seven-app regression (5135 tests) | c32de6aa | **1 failure** (H-95, planner-dependent); 5134 passed |
| `audit.tests_volume_report` alone ×3 before the fix | c32de6aa | 9 OK ×3 |
| The same after the fix, ×3 serial and ×1 parallel; deliberate red | 878ddf58 | 10 OK ×4; red for each dropped index |

## Apps whose production code the merge changes on the epic

`billing`, `users`, `classrooms`, `ai_processor`, `AutoGrader`, `dashboard`
(`dashboard/tasks.py`), and `audit` (the sweep locks). The combined regression covers
those seven.
