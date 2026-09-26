# Evidence — Epic A landing (retire integration/epic-a, rebase onto beta)

Worktree: `Grade-Automator-Plus-epic-a-land`, branch `task/epic-a-land`, off
`beta` `4b902fc`.

## 1. Background

`integration/epic-a` had diverged from `beta` (33 commits it had that beta
lacked, 30 commits beta had since the fork point) and its tip was a
merge-then-revert of an unrelated school-admin active-invite flow. Per SM
decision: retire `integration/epic-a` rather than repair it. Rebuild the
integration point from `beta` + `task/epic-a-metrics-alerting`, which is
confirmed to be a strict ancestor of `integration/epic-a` containing every
Epic A commit through §9 (model/migration, taxonomy, emitter, auth/credit/
grading/CRUD/admin-action call-site instrumentation, retention sweep, query
API, metrics/alerting), then carry over the two non-merge evidence-backfill
commits `integration/epic-a` had that neither `beta` nor
`task/epic-a-metrics-alerting` had.

`task/phase2-t1-audit-event` (the standalone original T1 build) is
confirmed superseded: a full-tree diff against `task/epic-a-metrics-alerting`
shows T1 has nothing metrics-alerting lacks except one cosmetic line in
`audit/emitter.py` (`return AuditEvent.objects.create(**fields)` inlined
instead of assign-then-return) — irrelevant, since metrics-alerting's
two-step form exists specifically to feed the post-write metrics call that
T1 predates and doesn't have. T1 is retired (left unmerged/unused, not
deleted — branch deletion wasn't requested and is easy to reverse-out-of by
just not building on it).

## 2. Steps taken

1. `./scripts/task-worktree.sh new epic-a-land` from `beta` @ `4b902fc`.
2. `git merge --no-edit task/epic-a-metrics-alerting` — clean, no conflicts
   (95 files, 8529 insertions / 51 deletions). Merge commit: see `git log`.
3. `git cherry-pick 592bb03` — "Backfill evidence doc for BE-A-04 PII
   cleanup (d960176 / 148d32c)" — clean, no conflicts.
4. `git cherry-pick 2b8870d` — "Backfill EVIDENCE.md for the admin-audit and
   audit-query pieces" — clean, no conflicts.
5. The two revert commits on `integration/epic-a` (`19e487e` school-admin,
   `7bde269` drf-spectacular) are not carried over, per SM instruction —
   confirmed unnecessary since neither the merge nor the two cherry-picks
   touch the files those reverts concern.

## 3. Verification

- `python manage.py makemigrations --check --dry-run` — no changes
  detected (the audit migration merged cleanly with beta's current state).
- Targeted Epic A suite (`audit`, `users.tests_auth_audit_events`,
  `billing.tests.test_credit_transaction_audit`,
  `assignments.tests_grading_audit_events`,
  `assignments.tests_epic_a_crud_audit`,
  `classrooms.tests_epic_a_roster_audit`,
  `students.tests_epic_a_submission_upload_audit`,
  `scripts.test_check_no_pii_in_logs`), `--settings=settings_worktree
  --parallel 4`: **204 tests, OK.** (Logged tracebacks/warnings in the
  output are expected — deliberately-mocked provider failures, refusal
  handling, and a benign "Free trial plan not found" fixture warning, not
  real errors.)
- Full regression (`python manage.py test --settings=settings_worktree
  --parallel 4`, no app filter, machine load ~33 on 8 cores): **4872 tests,
  3 failures, 28 skipped.** Triage:
  - `assignments.tests_pdf_renderer.ConcurrentRenderingTest.
    test_one_slow_render_does_not_stall_the_others` (timing assertion) and
    `students.tests_grading_redelivery_live.GradingRedeliveryLiveTest.
    test_5_concurrent_submissions_with_one_redelivery_each_grade_exactly_once`:
    **pass on targeted re-run** of their modules (39 tests) - load-induced.
  - `users.tests_email_domain_rules.ExemptDomainTests.
    test_nothing_is_exempt_by_default`: **reproduces**. Cause is
    environmental: the symlinked `.env` sets `EXEMPT_EMAIL_DOMAINS` to a
    value containing yopmail.com, and the test asserts the default is empty.
    This branch does not touch `users/email_domain_rules*` or that test
    (empty diff vs 4b902fc). Not caused by Epic A; the Verification Engineer
    should confirm by running it on plain beta with the same `.env`.

## 4. Gates (per `docs/phase2/architecture/10 Gates.md`)

This task is integration/merge work, not new logic — Gates 3/4/5/6/9
(concurrency, adversarial, failure, stress, isolation) are inherited from
each already-landed Epic A branch's own evidence
(`docs/evidence/phase2-t1-audit-event/`, `phase2-t2-auth-audit/`,
`phase2-credit-audit/`, `epic-a-grading-audit/`, `epic-a-crud-audit/`,
`epic-a-retention-sweep/`, `epic-a-admin-audit/`, `epic-a-audit-query/`,
`epic-a-metrics-alerting/`), not re-run here. Gate 1 (baseline/regression)
is this doc's §3. Independent verification is Integration & Release Lead's
final gate before merge, per team rules.
