# H-281 EVIDENCE: diffs to existing tests

The approved rule (owner ruling 2026-11-03) makes every teacher whom no
school license funds an *individual* teacher. Almost every existing test
builds its refused teacher that way (no license), so those tests now read
the individual sentence. Each diff below changes only the expected text,
or the remediation, for such a teacher. **No assertion was removed or
loosened.** Student, school-admin, mid-batch and untagged cases still
assert the school sentence where they did before.

| # | Test | Teacher fixture | Change |
|---|---|---|---|
| X1 | `billing.tests.test_refusal_handling` D11 `test_every_credit_guarded_endpoint_answers_402_with_a_code` (12 TEACHER subtests) | `unsubscribed_teacher` | TEACHER branch → `INDIVIDUAL_CREDITS_MESSAGE`, a new literal constant next to `GENERIC_CREDITS_MESSAGE`. The STUDENT branch is unchanged. |
| X2 | same module, D2D3 `test_teacher_chat_refusals_are_402_or_403_never_500` [credits] | `underfunded_teacher` | `credits_message=INDIVIDUAL_CREDITS_MESSAGE`. The helper's default is NOT changed. |
| X3 | same module, D4 `test_sync_assignment_create_refusal_is_402_or_403` [credits] | `underfunded_teacher` | as X2 |
| X4 | `billing.tests.test_refusal_handling_gates.ConcurrentRefusalTest…twenty_simultaneous…` (10 rounds) | `underfunded_teacher` | → `INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE` |
| X5 | `students.tests_h216_student_credit_sentence…test_c1_a_teacher_asking_for_the_same_edit_keeps_the_generic_body` | plain teacher | message → individual; remediation → `CREDITS_SPEC.alternative_remediations[0]` |
| X6 | `students.tests_h225_student_empty_wallet…test_c1_a_teacher_at_an_empty_wallet_keeps_the_generic_body` | plain teacher | as X5 |
| X7 | `students.tests_h223_batch_runs_what_fits` `assertRefusedWhole` (t4 and t6, both routes: 4 tests) | plain teacher | → individual message |
| X8 | `students.tests_upload_credit_door` `AssignmentUploadDoorTest` and `TeacherBatchDoorTest` `test_a_poor_teacher_is_refused_and_nothing_is_queued` | plain teacher | → individual message |

The X5 and X6 test names still say "keeps the generic body". They are not
renamed, so their history stays traceable; a comment in each says why the
body is now the individual one.

Not changed:
- `students.tests_upload_credit_door` lines 509–510 (a polled teacher
  task) mock the gate with an **untagged** `InsufficientCreditsError`, so
  they still read the school sentence. That is correct for an untagged
  refusal.
- `AutoGrader.tests_reason_codes.RequireAiAccessTests` keeps its
  expectation. Its 500 was a predicate bug, fixed at the source (anonymous
  user).

The byte-exact patch is `logs/existing_test_diffs_8591f89b.diff.gz`, raw
sha256 be211f13a87d6b009f14b7eb255e249be4024f8dd18aa16f3c0029c175d7a5a0.
