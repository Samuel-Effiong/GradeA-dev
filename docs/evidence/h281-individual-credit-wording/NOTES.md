# H-281 NOTES: credit refusal wording for individual teachers

Design: `DESIGN.md` (cbf1af408), approved by the SM on 2026-11-03.
- (a) Use `is_under_license()`. The case of a teacher removed from their
  license (or whose license lapsed) is with the owner. It is built as
  *individual*, and one constant in the test module flips it
  (`REMOVED_FROM_LICENSE_IS_INDIVIDUAL`).
- (b) and (c) are approved as written, including `meta["message_variant"]`
  and passing the billed user into `batch_refusal_response`. No migration.
- (d) Characterization tests only. The upload door's user_type-only check
  is H-282 and is not touched here.

Worktree `Grade-Automator-Plus-h281`, DB `test_AutoGrader_h281`, venv
outside the worktree.

## Red tests: `billing.tests_h281_individual_credit_wording` (42)

| Class | Tests | Covers |
|---|---|---|
| WhoIsAnIndividualTeacher | 7 | the predicate: individual, licensed, school admin, student, half-set-up super admin, removed (flippable), None |
| TheRaiseSitesTagTheRefusal | 17 | R1 ×2, R2 ×3, R3 ×2, R4 ×2 plus the student control, R5, R6, R7 ×2, R8, R10 ×2 |
| WhatEachSurfaceSays | 10 | S1 ×3 (individual, licensed, removed), S2 ×2 (individual, school admin), S5 ×5 (whole batch ×2, single door ×2, student at the door) |
| WhatAPolledItemSays | 5 | S4 ×2, S6/S7 ×2, the student-requested task |
| AHalfSetUpSuperAdminAtTheCreditGate | 3 | (d) characterization |

## E1: red run at the red commit (expectations, written before the run)

Serial, this module only:
`manage.py test --noinput --settings=settings_worktree -v 2 billing.tests_h281_individual_credit_wording`

Expect **`Ran 42`, `FAILED (failures=5, errors=26)`**, with 11 passing.

- **ERROR (26):**
  - all 7 predicate tests (`ImportError`: there is no `is_individual_teacher`);
  - 16 raise-site tests (`AttributeError`: there is no `.individual`; for R6, possibly a different refusal). Only the R4 student control passes.
  - 3 polled tests (`TypeError`: the `individual=` keyword) are
    `test_s4_describe_user_error_individual`,
    `test_s6_s7_an_individual_refusal_is_stored_and_read_back` and
    `test_s6_a_student_requested_task_keeps_the_student_sentence`.
- **FAIL (5)**, each a message mismatch where the school sentence is read:
  - `test_s1_an_individual_teacher…`
  - `test_s1_a_teacher_removed…`
  - `test_s2_custom_ai_prompt_individual`
  - `test_s5_a_whole_batch_refusal_individual`
  - `test_s5_the_single_upload_door_for_a_teacher_individual`
- **PASS (11):**
  - the R4 student control;
  - S1 licensed and S2 school admin;
  - S5 batch licensed, single-door licensed, and the student at the door;
  - S4 untagged and S6/S7 untagged;
  - the 3 characterization tests, which are green on purpose: no behavior
    change.

Gate: ≥ 4000 MB available. B33 is finished, so normal runs are allowed.

## E1 result (e02721c5d): NOT met, a test-fixture error of mine

Got `Ran 42`, `FAILED (failures=6, errors=26)`, with 10 passing.

- **The 26 errors are exactly the ones predicted:**
  - 7 `ImportError` (`is_individual_teacher`);
  - 14 `AttributeError` on `InsufficientCreditsError` plus 2 on
    `EmptyWalletError` (`.individual`); R6 was among them, with no other
    refusal;
  - 3 `TypeError` (`individual=`).
- **The 5 predicted failures failed**, each on
  `"…your credits or ask your school administrator…" != "…your credits, then try again."`.
- **One extra failure:**
  `test_s2_custom_ai_prompt_school_admin_keeps_the_school_sentence` gave
  `403 != 402`, "You must be a teacher to access this endpoint." The
  custom-ai-prompt route is teacher-only, so a school admin can't reach the
  credit gate there. The test was wrong, not the code.

Log: `logs/red_run_e02721c5.txt`.

**Correction.** The S2 control is now a licensed teacher:
`test_s2_custom_ai_prompt_licensed_teacher_keeps_the_school_sentence`.
School admins are still covered at R2 (`consume_credits`). Still 42 tests.

## E1b: red re-run of the corrected module (expectations, written before the run)

Expect **`Ran 42`, `FAILED (failures=5, errors=26)`**, with 11 passing.
That is E1 as written, with the new S2 control passing: a licensed teacher
reads the school sentence on the old code.

**E1b result (6a1fc7f83): met.** `Ran 42`, `FAILED (failures=5, errors=26)`.
The failures are exactly the 5 named in E1. Log: `logs/red_run_6a1fc7f8.txt`.

## Fix commit: what changed

- **billing/errors.py**
  - `InsufficientCreditsError(*args, individual=False)`; the attribute
    survives pickle (checked for `EmptyWalletError` too);
  - `INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE`;
  - `is_individual_teacher(user)`, the only place the rule lives.
- **AutoGrader/reason_codes.py**
  - The INSUFFICIENT_CREDITS spec gets
    `alternative_messages={"individual": …}` and
    `alternative_remediations=("Top up credits, then try again.",)`.
  - New `credit_refusal_variant`, `credit_refusal_message` and
    `credit_refusal_remediation`.
  - `coded_response` uses the variant's remediation for a plain refusal.
- **AutoGrader/error_messages._passthrough_message**:
  `credit_refusal_message(error)`. That covers `describe_user_error`,
  `describe_task_error` and `_refusal_reason`.
- **Raise sites:**
  - R1–R3 in `billing/models.py` (`self.user`);
  - R4 in `users/permissions.py` (`target_user`);
  - R5 in `billing/access_control.py` (`request.user`);
  - R6 in `ai_processor/services.py` (`user`); R7 and R8 there
    (`target_teacher`);
  - R9 and R10 in `assignments/upload_door.py` (`billed`).
- **upload_door**
  - The single door's teacher message is `credit_refusal_message` of its
    tagged refusal; the student branch is unchanged.
  - `batch_refusal_response(billed_user)`. Both callers are teacher-only
    batch routes and pass `request.user`, which is `_billed_user` for a
    teacher.
- **S7**
  - `mark_processing_task_failure` merges `{"message_variant": "individual"}`
    into `meta`;
  - `item_results._remediation` reads it.
- No migration.

## E2/E3 expectations (written before the runs)

**E2.** `billing.tests_h281_individual_credit_wording`, serial: `Ran 42`,
`OK`.

**E3, targeted.** Every module that names the credit sentence or
remediation, plus the gate and permission modules, serial: **305 tests**.
- Labels: AutoGrader.tests_error_messages AutoGrader.tests_reason_codes ai_processor.tests_answer_chunk_merge assignments.tests_edit_refusal_student_sentence billing.tests.test_refusal_handling billing.tests.test_refusal_handling_gates billing.tests_h281_individual_credit_wording students.tests_h216_student_credit_sentence students.tests_h223_batch_runs_what_fits students.tests_h225_student_empty_wallet students.tests_task_tracking students.tests_upload_credit_door billing.tests.test_execute_graded_task users.tests_credit_balance_permission
- Expected: every test that reads the credit refusal **as a non-licensed
  teacher** and asserts the school sentence or remediation now FAILS. Their
  fixture teacher is individual under the approved rule, so these are
  intended changes. Each one is listed in EVIDENCE.md as an existing-test
  diff, and its expectation is moved to the individual sentence. The diff is
  committed before the re-run.
- Every other test passes, in particular the student-sentence tests (H-216,
  H-225) and the mid-batch tests.
- No test outside that pattern fails. If one does, it is a finding and is
  reported, not adjusted.

## E2 result (49eb48436): met

`Ran 42`, `OK`. Log: `logs/green_run_49eb4843.txt`.

## E3 result (c1821c3a4): 32 intended changes and 1 real finding

Got `Ran 305`, `FAILED (failures=34)`, counting subtests.

**32 are the intended change.** Each one is
`"…your credits, then try again." != "…your credits or ask your school administrator, then try again."`,
because the fixture teacher is unlicensed and so individual:
- `billing.tests.test_refusal_handling`:
  - D11 `test_every_credit_guarded_endpoint_answers_402_with_a_code`, 12 TEACHER subtests;
  - D2D3 `test_teacher_chat_refusals_are_402_or_403_never_500` [credits];
  - D4 `test_sync_assignment_create_refusal_is_402_or_403` [credits].
- `billing.tests.test_refusal_handling_gates.ConcurrentRefusalTest.test_twenty_simultaneous_refusals_charge_nothing_and_persist_nothing`, 10 rounds.
- `students.tests_h216_student_credit_sentence…test_c1_a_teacher_asking_for_the_same_edit_keeps_the_generic_body`
- `students.tests_h225_student_empty_wallet…test_c1_a_teacher_at_an_empty_wallet_keeps_the_generic_body`
- `students.tests_h223_batch_runs_what_fits`: t4 and t6 on both routes, 4 tests (the shared `assertRefusedWhole`).
- `students.tests_upload_credit_door`: `AssignmentUploadDoorTest` and `TeacherBatchDoorTest` `test_a_poor_teacher_is_refused_and_nothing_is_queued`.

**1 real finding, in 2 subtests.**
`AutoGrader.tests_reason_codes.RequireAiAccessTests.test_a_balance_reason_gets_the_credits_refusal`
gave `500 != 402`.
- Cause: the test's request user is anonymous (a `SimpleTestCase`), and
  `is_individual_teacher(request.user)` at R5 read `.user_type`, which an
  AnonymousUser does not have.
- This is a bug in my predicate, not an intended change. That test keeps
  its expectation as it is: an anonymous user is not individual, so it
  reads the school sentence.

Log: `logs/targeted_run_c1821c3a.txt.gz`.

### E4: red for the finding (expectations, written before the run)

New test: `WhoIsAnIndividualTeacher.test_an_anonymous_user_is_not_individual`
(the module now has 43 tests). Serial, that one test only. Expect `Ran 1`,
`FAILED (errors=1)`, with
`AttributeError: 'AnonymousUser' object has no attribute 'user_type'`.
