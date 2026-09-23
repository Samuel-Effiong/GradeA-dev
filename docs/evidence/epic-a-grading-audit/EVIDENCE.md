# Evidence — grading call-site instrumentation (GRADING_REQUESTED/COMPLETED/FAILED)

Worktree: `Grade-Automator-Plus-epic-a-grading-audit`, branch
`task/epic-a-grading-audit`, off `integration/epic-a` `d4a98da`. This
evidence at commit `d8b3a76`.

Instruments the three grading `AuditAction`s per Epic A plan §6:

- `GRADING_REQUESTED`: `students/views.py` `StudentSubmissionViewSet.grade_async`
  and `assignments/views.py` `AssignmentViewSet.grade_all_submission` (one
  event per submission in the batch loop). Both emit only after
  `launch_processing_task`'s dispatch succeeds — a broker-dispatch failure
  (Redis down) currently produces no audit trail at all; tracked as **H-40**
  in `docs/HARDENING_BACKLOG.md` rather than silently accepted.
- `GRADING_COMPLETED` / `GRADING_FAILED`: `assignments/tasks.py`
  `grade_engine_async`'s success path and `except Exception` block. Actor
  identity is read from the `BackgroundProcessingTask` row returned by
  `mark_processing_task_success`/`mark_processing_task_failure`
  (`task.requested_by`), not from a local variable that may be unbound on
  an early failure (e.g. the submission fetch itself failing before `user`
  is resolved).

Deliberately **not** touched: the shared chokepoints in
`students/task_tracking.py` (`create_processing_task`,
`mark_processing_task_success`/`failure`) — ~11–15 other task types
(extraction, formatting, student summaries) pass through them and must not
gain grading audit events from this change. Also not touched, per explicit
Senior Manager confirmation before implementing: the
`SubmissionGradingInProgressError` duplicate-skip branch's own
`mark_processing_task_success` call (a redelivered Celery task racing the
still-running original) — nothing actually happened there from an audit
standpoint, so it stays uninstrumented rather than misrepresenting a no-op
as a completed grading.

`error_class` for `GRADING_FAILED` (`_grading_failure_error_class`,
confirmed with the Senior Manager before implementing): an AI
content-policy refusal (`billing.refusals.PERMANENT_AI_REFUSALS`) →
`MODEL`; a recognized infra failure
(`AutoGrader.error_messages.classify_infra_error` — timeouts, rate limits,
dropped connections, unreadable/corrupt files) → `PROVIDER`; anything else
→ `SYSTEM`.

## 1. Test suite

`assignments/tests_grading_audit_events.py` — 7 tests:

- `GradeAsyncRequestedAuditEventTest`: successful dispatch emits exactly
  one `SUCCESS` event; a broker outage emits none (locks the H-40 gap as
  current behaviour, not silently).
- `GradeAllRequestedAuditEventTest`: batch grading emits exactly one event
  per submission.
- `GradeEngineAsyncOutcomeAuditEventTest` (runs `grade_engine_async.apply()`
  directly, independent of Celery/broker concerns): a successful grade
  emits exactly one `GRADING_COMPLETED`; a generic exception, a
  provider-classified exception, and an AI refusal each emit exactly one
  `GRADING_FAILED` with the right `error_class`.

`python manage.py test assignments.tests_grading_audit_events --settings=settings_worktree --noinput`

- Found 7 test(s)
- **OK**

Caught a real bug while writing these: the `_grading_failure_error_class`
helper had been added between `grade_engine_async`'s `@shared_task`
decorator and its `def` line, which silently turned the task into a plain
function (no `.apply`/`.delay`) — every test in this file failed with
`AttributeError: 'function' object has no attribute 'apply'` on the very
first run. Fixed by moving the helper above the decorator, before this
evidence's commit.

## 2. Mutation testing

11 mutants (`mutate.py`) against the three instrumented call sites and the
`error_class` helper. Applied one at a time from collision-safe copies,
restore verified by md5 against the pre-mutation original after every
mutant (never `git checkout`), confirmed both programmatically
(`restored_md5_ok`) and by `git status --short` showing a clean tree
afterward.

**Result: 11 / 11 KILLED**, clean on the first pass.

| id | protection weakened | expected test |
|----|---|---|
| R1 | grade_async: requested event not emitted | `successful_dispatch_emits...` |
| R2 | grade_async: wrong target (assignment id, not submission id) | `successful_dispatch_emits...` |
| B1 | grade_all_submission: requested event not emitted | `batch_grading_emits...` |
| C1 | grade_engine_async: completed event not emitted | `successful_grade_emits...` |
| C2 | completed event's actor always None | `successful_grade_emits...` |
| C3 | completed event outcome not SUCCESS | `successful_grade_emits...` |
| F1 | grade_engine_async: failed event not emitted | `failed_event` (all three error_class tests) |
| F2 | AI refusal not classed MODEL | `model_refusal_emits...` |
| F3 | infra failure not classed PROVIDER | `provider_error_emits...` |
| F4 | fallback never reaches SYSTEM (stuck at PROVIDER) | `system_error_emits...` |
| F5 | failed event's actor always None | `failed_event_classed_system` |

## 3. Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis, CI's
fake credentials).

`python manage.py test --settings=settings_worktree -v 1 --noinput`
(every app, no labels, no `--keepdb`)

- Ran 4689 tests in 2374.510s
- **OK (skipped=26)**

Clean — no failures, no contention noise this run. (Note for whoever runs
this branch's next full regression: gate-runner landed a test-suite speed
fix on beta after this branch was cut — de6d191, a fast password hasher
for tests, ~65% faster full-suite runs. This branch predates it; expect
~15min instead of ~40min after rebasing onto current beta/integration.)

## 4. Conclusion

All three grading `AuditAction`s emit exactly one well-formed `AuditEvent`
per instrumented branch, matching plan §6 and the outcome/error_class
vocabulary from `04_epic_a_implementation_plan.md`. No regressions
anywhere in the codebase from this change. One tracked gap (H-40,
broker-dispatch-failure at request time) and one confirmed non-instrumented
branch (the duplicate-skip redelivery path) are both documented above and
in `docs/HARDENING_BACKLOG.md`, not silently left out.

Post-commit sha256 (from `git show <sha>:<path>`, not the working copy —
pre-commit hooks can rewrite a file after it's written):

```text
$PENDING — filled in after this file's own commit, same as T1/T2's evidence
```
