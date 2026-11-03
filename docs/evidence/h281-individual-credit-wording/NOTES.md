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
