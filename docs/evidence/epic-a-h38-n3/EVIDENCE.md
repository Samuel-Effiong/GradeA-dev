# H-38 tasks N3: a refused grading run records why (Epic A)

**Author:** 1a. **Verifier:** v2. **Branch:** `task/epic-a-h38-n3`, off
`phase2/epic-a` `cc22bc03`, base-updated by 0b onto `3fff1382` (the bundle 5
merge-down; clean merge, `ba663453`). **Code tip:** `c81eacbb`.

**The assignment (SM, 2026-10-02).** On Epic A, the course-reachability
refusals in the grading tasks must leave an audit event `GRADING_FAILED`
that says "not reachable": ids only, through `audit.emitter.emit()`, exactly
one event per refused run.

## The change (4 points)
1. **Before.** Three places in `assignments/tasks.py` refuse a grading run
   whose course the teacher it would run as can no longer reach (H-38):
   - `grade_engine_async` raises `CourseNotReachableError`. Its except block
     already wrote one `GRADING_FAILED`, with no reason: the trail could not
     tell it from any other user-class failure. Its school came from the
     actor, and a teacher removed from the school has none, so it was filed
     under no school.
   - `grade_batch_async` returned "This course wasn't found." and wrote
     nothing.
   - `auto_grade_due_assignment` did the same.
2. **After.**
   - One new reason code, `COURSE_NOT_REACHABLE` ("Course not reachable"),
     **audit-only**: it is in `audit.enums.ReasonCode` and in
     `AUDIT_ONLY_CODES`, and has no response spec. The exception stays
     uncoded and a client is still told only "This course wasn't found."
   - `grade_engine_async`: its one event keeps its shape (target the
     submission; actor the tracked task's requester, SYSTEM without one) and
     gains the reason. For this refusal only, it is filed under the
     **course's** school.
   - `grade_batch_async` and `auto_grade_due_assignment`: one
     `GRADING_FAILED` each, about the teacher (`target_type` "CustomUser",
     `target_id` the teacher's id), `error_class` USER, the reason, the
     assignment's id (and the tracked task's, if any) in the metadata, filed
     under the course's school. The batch records its tracked task's
     requester when it has one, else SYSTEM; the auto-grade, which Beat
     starts, is SYSTEM.
   - The helpers' own lookups (the tracked task's requester, the course's
     school) are guarded: a database error there cannot turn a refusal into
     a task failure. The event is still written with whatever was read
     before the error: nothing if the requester lookup failed (the system's,
     no school), or the requester alone if only the school lookup failed.
     One ERROR line names the ids and the error's class.
3. **Reach.** The audit trail only. No response, task result, model or
   migration changes. `AuditEvent.reason_code` is a plain `CharField`
   validated by the emitter, so the new code needs **no migration**. The
   reason-code metric (`reason_code_rate`, tagged by code) counts it with no
   further change: the emitter counts any event that carries a code.
4. **Tests.** `students/tests_h38_n3_refusal_audit.py` (23 tests): one event
   per path and exactly one each; none on a member's successful runs; ids
   only; the code is audit-only and nothing a client reads carries it; each
   refusal is seen by the course's school admin through the real audit
   route and by no other school's; every other failure keeps its school
   rule; the school-less course cases; the guards; the metric.

## SM rulings this change follows
- **The reason code.** Nothing in the catalogue fitted, and GRADING_FAILED's
  metadata allow-list has no reason key. The SM ruled one audit-only code
  (not a free-text metadata key), as with INVALID_REQUEST, SERVER_ERROR and
  FAILED_AUTH_CAPPED, and that it must never reach a client.
- **The actor.** The SM's first wording said "actor SYSTEM". Corrected by
  the SM for the batch: the same rule as `grade_engine_async`, the tracked
  task's requester when there is one, SYSTEM otherwise.
- **The school.** All three refusals are filed under the course's school,
  so the school that removed the teacher finds them in its own audit query.

## For v2 to check knowingly
- **`grade_engine_async`'s existing event changes its school for this one
  cause.** S7b's verified event took its school from the actor. For
  `CourseNotReachableError` only, it now takes the course's. Any other
  failure on that path is unchanged (two control tests).
- **A known edge, accepted by the SM (v2's N1).** A course in a teacher's
  own session has no school, so the event has no course school and follows
  the emitter's standing rule: the actor's school. With SYSTEM that is none.
  With a tracked requester who belongs to a school, it is that school. It
  needs a school member's tracked run pointing at someone else's private
  course, which only a hand reassignment produces. Pinned by
  `test_a_tracked_run_by_a_school_member_is_filed_under_their_school`. The
  emitter is not changed.
- **`grade_engine_async` itself is touched by two lines**, both in its
  except block's `emit(...)` call: `reason_code=` and `school_id=`. The
  rest is four module-level helpers above it and one call each in
  `grade_batch_async` and `auto_grade_due_assignment`. (0b asked: the
  merge-downs keep this function on the epic's side.)
- **Ids only.** The call sites pass ids. `actor_email` is the emitter's own
  column for a staff actor and is not theirs to set.
- **Not on this branch:** `docs/phase2/architecture/08a`'s catalogue needs
  the new code, through the docs branch (0b holds the pending note).

| Commit | What |
|---|---|
| `07ac25b6` | the tests (red) |
| `cf47a5b4` | the reason code; the three refusals' events |
| `d59f2aa4` | tests: the course's school (red for `grade_engine_async`) |
| `174caeb5` | `grade_engine_async`'s event is filed under the course's school |
| `ba663453` | base update onto `3fff1382` (0b) |
| `88b065b7` | test-only: two faults of mine fixed; v2's N1 pinned; the guard's tests (red) |
| `de6774c5` | the guard around the helpers' lookups (v2's N3) |
| `3cc0ecfe` | test-only: a tracked batch whose school lookup alone fails |
| `c81eacbb` | a comment (no code line changed) |

## Gates
One chain on the frozen tip `c81eacbb` (base `3fff1382`), in 0b's slots,
stopping on red. It was paused twice at 0b's request, at step boundaries
(Vezi's runs; ed's regression), so the four steps ran under four grants on
the same tip. Runner: `run_gates.py.txt`.

| Gate | Result | Log |
|---|---|---|
| Repro: the new module over `3fff1382`'s `assignments/tasks.py`, `audit/enums.py` and `AutoGrader/reason_codes.py` | 23 tests: 9 failures, 10 errors | `logs_c81eacbb/repro_c81eacbb_tests_over_3fff1382_code.log` |
| (a) 28 modules: the new module, the refusals' existing tests, every test that reads GRADING_FAILED, the catalogue and emitter tests, and ALL repo-wide guards | **496 OK** | `logs_c81eacbb/a_modules_and_guards_c81eacbb.log.gz` |
| (b) mutants (`test_epic_a_h38_n3_mut`) | baseline green (71 tests), **16/16 killed**, every restore sha-checked | `logs_c81eacbb/mutation_results.tsv`, `logs_c81eacbb/mutants/` |
| (c) ONE regression: `assignments students audit`, `--parallel 2`, under `flock ~/.machine-fullsuite.lock` | **Ran 1390 tests, OK (skipped=16)**, 122 s wall | `logs_c81eacbb/c_regression_assignments_students_audit_c81eacbb.log.gz` |

- **The repro's 4 passing tests** are controls: a member's runs record no
  failure; a member's failed run and an untracked failed run keep their
  school rule; nothing a client reads carries the code (it does not exist on
  the base).
- **(a)'s guards:** `audit.tests_route_coverage`, `audit.tests_history_guard`,
  `audit.tests_state_change`, `audit.tests_checks`,
  `AutoGrader.tests_reason_codes`, `AutoGrader.tests_no_wildcard_invalidation`,
  `AutoGrader.tests_cache_invalidation_coverage` (with the raw-Redis guard),
  `AutoGrader.tests_migration_rollback_defaults`,
  `AutoGrader.tests_redis_test_isolation`,
  `classrooms.tests_teacher_access_sweep`,
  `classrooms.tests_course_roster_scope_sweep`, and the five the bundle 5
  merge-down brought: `AutoGrader.tests_beat_locks`,
  `AutoGrader.tests_beat_health`,
  `AutoGrader.tests_management_commands_are_commands`,
  `audit.tests_sweep_beat_lock`, `billing.tests.test_logs_carry_no_email`.
  v2's handover list (`AutoGrader.tests_reason_codes`,
  `audit.tests_route_coverage`, `audit.tests_history_guard`,
  `students.tests_h38_tasks_namespace`) is all in (a).
- **(c)'s count:** assignments 663, students 385, audit 342. Gate 10 on
  `3fff1382` had 663, 362 and 342; students is +23, the new module. No FAIL
  or ERROR line. The 16 skips are the opt-in real-provider tests and the
  ones the epic already skips.

**How the runs were made.** Every run used `--settings` (the worktree's own
test DB; the mutants their own `_mut` DB) and an empty
`EXEMPT_EMAIL_DOMAINS`, wrapped as `systemd-inhibit
--what=idle:sleep:handle-lid-switch --who=GAP --mode=block systemd-run
--user --scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60
1800` (rules 12, 13, 16). `RACE_COST_*`, `AUDIT_BENCH*` and
`ENABLE_GRADING_BENCHMARK` were unset. (c) ran through a timestamper.

**Rule 17.** The mutants, their baseline and the repro ran with
`PYTHONDONTWRITEBYTECODE=1` (and `python -B`). `__pycache__` of the mutated
modules' directories (`assignments/`, `audit/`, `AutoGrader/`) was deleted
before the baseline, before each mutant and after each restore; none was
left afterwards. Each restore was checked against the pre-run sha256 of the
three production files.

## The first chain, on the old base (disclosed)
At `174caeb5` on `cc22bc03`, before the base update:

| Gate | Result | Log |
|---|---|---|
| Repro over `cc22bc03`'s code | 18 tests: 9 failures, 8 errors (red as intended) | `logs_174caeb5_on_cc22bc03/repro_174caeb5_tests_over_cc22bc03_code.log` |
| (a) 23 modules | **RED: 417 tests, 1 error, 2 failures**, all three in this branch's own new test module | `logs_174caeb5_on_cc22bc03/a_modules_and_guards_174caeb5_RED.log.gz` |

All three were faults in my tests, not in the fix, and are fixed in
`88b065b7`:
1. The control test let a bare MagicMock from the patched `delay` be stored
   as a tracked task's celery id (a rule 14 slip, which v2 also flagged);
   the second dispatch then broke on the column's unique key. `delay` now
   returns a fresh real id each call.
2. and 3. One test, two sub-cases: it asserted a tracked task's
   `reason_code` is NULL for an uncoded failure; the column holds "".

Every other module in that run passed, including all of `cc22bc03`'s
guards. The chain was paused after (a) for Gate 10; (b) and (c) never ran
on the old base.

The logs' addresses are test fixtures only.

## Mutants
| Id | What it breaks | Result | Killed by |
|---|---|---|---|
| K1 | the refused run's event carries no reason | killed | `test_a_refused_run_on_a_schoolless_course_has_no_school`; `test_a_tracked_run_by_a_school_member_is_filed_under_their_school` (+4 more) |
| K2 | the refused batch writes no event | killed | `test_a_refused_batch_on_a_schoolless_course_has_no_school`; `test_a_refused_batch_records_one_event_about_the_teacher` (+6 more) |
| K3 | the refused auto-grade writes no event | killed | `test_a_refused_auto_grade_records_one_system_event_about_the_teacher`; `test_the_refused_auto_grade_is_filed_under_the_courses_school` (+4 more) |
| K4 | the batch-level event targets the assignment, not the teacher | killed | `test_a_refused_batch_on_a_schoolless_course_has_no_school`; `test_a_refused_auto_grade_records_one_system_event_about_the_teacher` (+4 more) |
| K5 | the batch-level event is always the system's | killed | `test_a_refused_tracked_batch_names_its_requester`; `test_a_tracked_batch_keeps_its_requester_when_only_the_school_fails` |
| K6 | the batch-level event carries no reason | killed | `test_a_refused_batch_on_a_schoolless_course_has_no_school`; `test_the_refusal_is_counted_by_its_reason` (+8 more) |
| K7 | the batch-level event has no school | killed | `test_a_refused_auto_grade_records_one_system_event_about_the_teacher`; `test_a_refused_batch_records_one_event_about_the_teacher` (+4 more) |
| K8 | the batch-level event is a system fault, not the user's case | killed | `test_a_refused_batch_on_a_schoolless_course_has_no_school`; `test_a_refused_auto_grade_records_one_system_event_about_the_teacher` (+5 more) |
| K9 | the code is not listed as audit-only | killed | `test_the_code_is_audit_only_and_the_refusal_stays_uncoded`; `test_every_code_is_user_facing_or_audit_only_and_not_both` |
| K10 | every batch writes the event, refused or not | killed | `test_a_refused_batch_on_a_schoolless_course_has_no_school`; `test_a_refused_batch_records_one_event_about_the_teacher` (+7 more) |
| K11 | the batch-level event drops the tracked task's id | killed | `test_a_refused_tracked_batch_names_its_requester` |
| K12 | the refused run's event keeps the actor's school (none, once removed) | killed | `test_a_refused_run_survives_a_failed_school_lookup`; `test_the_refused_run_is_filed_under_the_courses_school` |
| K13 | every failed run borrows the course's school, not only the refusal | killed | `test_a_failed_run_with_no_tracked_task_still_has_no_school` |
| K14 | a failed requester lookup breaks the refused batch (no guard) | killed | `test_a_refused_auto_grade_survives_a_failed_school_lookup`; `test_a_refused_batch_survives_a_failed_requester_lookup` (+1 more) |
| K15 | a failed school lookup breaks the refused run's failure handling (no guard) | killed | `test_a_refused_run_survives_a_failed_school_lookup` |
| K16 | the guard's log line carries the error's text | killed | `test_a_refused_batch_survives_a_failed_requester_lookup` |

The full list of killing tests per mutant is in
`logs_c81eacbb/mutation_results.tsv`; each mutant's log is in
`logs_c81eacbb/mutants/` (gzipped). `h38n3_mutants.py.txt` holds the exact
replacements.
