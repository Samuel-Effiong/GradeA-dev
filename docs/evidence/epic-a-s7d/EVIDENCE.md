# Epic A S7d: QA error catalogue, sections B–F

- **Author:** ed (Security), 2026-09-30 / 10-01.
- **Verifier:** v2.
- **Branch:** `task/epic-a-s7d`, off `phase2/epic-a` ca35971.
- **Source:** `docs/phase2/qa/catalogue_additions_proposal.md` from `task/qa-catalogue-proposal` 0ecad75. The founder, acting as QA, **approved it as written** on 2026-09-30. It is copied into this branch with an approval-record header (375adbe).
- **Scope (SM):** sections B, C, D, E, F. A (sign-in locks) and G (retry, credits mid-batch) were already built.
- **H-68 is folded into section B.** It is the same route and code (register_student's REGISTRATION_PAUSED). 0b marks the H-68 backlog row "delivered by Epic A S7d".
- **Built to:** v2's `PREP_s7d.md` checklist and the SM's rulings Q1–Q7 (listed below).

## Code carried byte-identical from the beta line (SM rulings)

The merge-down should find identical changes on both sides and resolve trivially. Check each one with a single diff.

| What | Source | Here | Check |
|---|---|---|---|
| H-71: neutral non-student refusal (`NOT_A_STUDENT_MESSAGE`, ids-only log) | `task/h71-student-add-role` **cf4e405** (v2 VERIFIED), the hunks in `classrooms/services/enrollment.py` and `classrooms/services/__init__.py` | ee6f4b2 | `git diff cf4e405 <tip> -- classrooms/services/__init__.py` is empty; the enrollment.py hunks are identical |
| Bundle 4: add-teachers disclosure fix | `e120414^..f7260bd` (merged at 3b94aa8; bundle 4 frozen at 67a0681), `billing/license_service.py` | 042d0f6 | `_get_or_invite_teacher` was identical to f7260bd at 042d0f6 |
| H-78: other school before subscription (SM Q1); ids-only refusal logs (SM condition b) | d5's canonical `task/h78-other-school-first-b4` **8a31d19** (on 67a0681), plus the test module | 0dde18e | `_get_or_invite_teacher` and `_invite_and_enroll_one_teacher` are identical to 8a31d19 |

If any source changes before the merge-down, the copy here is re-synced.

**The one line that will conflict at merge-down:** `classrooms/tests_security_penetration.py`, in the non-student bulk-add assertion. H-71's form pins the rowless `NOT_A_STUDENT_MESSAGE`; S7d's form pins the row code `ROW_STAFF_EMAIL` and the text "Row 1: this email…". **Take S7d's.**

## What changed, by section

### The catalogue layer (375adbe)
- 28 approved codes (32 in the proposal, minus the 4 in A and G) are added to `audit.enums.ReasonCode`, each with its `REASON_CODES` spec.
- `AutoGrader.tests_reason_codes.APPROVED_ADDITIONS` pins every status, message template and remediation **exactly**.
- `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION`'s text lives in one constant, so it can be switched to the generic text.
- Two small extensions to the shared spec:
  - an empty remediation is the proposal's "none" and reaches the body as `null`;
  - `alternative_remediations`, with `CodedError(remediation=)`, lets one code carry a per-route remediation (TEACHER_LIST_EMPTY on removal). An unapproved remediation is refused, and a chosen one survives `cls(*args)` as a 5th arg; every other error keeps its 4-tuple.
- `coded_entry(error, **fields)` builds one item of a sync batch route's result list. It uses the same coded keys as session-results items, plus the legacy `error`.

### B: REGISTRATION_PAUSED (f3a5d53), which is H-68
- `register_student`'s global-budget 429 keeps its text (now read from the catalogue) and its Retry-After.
- It gains `code` + the coded envelope, so it can be told apart from the per-network RegisterThrottle 429, which carries neither.
- The renew-student-token door is **unchanged** (SM Q6; it goes to the backlog until its wording is approved).

### C: FILE_NOT_A_PDF (c7cf636)
- `PDFService` raises `PDFNotAPdfError` (a `PDFUnreadableError`) when the bytes aren't a PDF.
- `prepare_ai_content` maps it to `FileNotAPdfError` (a `FileUnreadableError`): 422, params `{file_name}`.
- A damaged real PDF stays FILE_UNREADABLE.

### D: roster import (6c28bf9, 27df119, 6be92b0)

**D1, the whole request:**
- ROSTER_NO_INPUT (the view);
- ROSTER_EMPTY, also for a header alone or only blank rows (SM Q4);
- ROSTER_FILE_UNREADABLE;
- ROSTER_TOO_MANY_ROWS;
- the shared FILE_TOO_LARGE. It is **413** now, with int params, and the size is still checked before `read()`.

**D2, every row that isn't added:**
- **Row number:** `row` is its place among the data rows, blank rows counted (SM Q3).
- **Codes:** each row gets a ROW_* code with the approved text.
- **ROW_NAME_INVALID (2–150 characters)** is checked before either path reaches the database. A middle initial is allowed.
- **ROW_EMAIL_INVALID:** a header email column only (SM Q7). It creates and sends nothing.
- **ROW_DUPLICATE:** the same normalised email, or the same name when there's no email, within one request. It points at the first occurrence, even one that failed (SM Q5).
- **ROW_STAFF_EMAIL / ROW_OTHER_SCHOOL:** mapped from the shared enrollment constants by identity.
- **ROW_NAME_CLASH:** also on the emailed path (the dev run found it). The model refuses two students of exactly the same name in one course.
  - A new account is refused before anything is created.
  - An existing one is refused by the model after the staff and cross-school gates, so no other school's stored name is ever shown. The row's transaction rolls back.
- **ROW_FAILED:** anything else, with no exception text.
- `skipped_count` is added.

### E: licence teacher management (3360ab6)

**E1, the whole request:**
- TEACHER_LIST_EMPTY on both routes. A string instead of a list is refused, never read per character. Removal carries the approved "Choose the teachers to remove.".
- LICENCE_INACTIVE.
- LICENCE_SEATS_EXCEEDED, with int params and the two approved forms. With one seat left it reads "1 seat left", the approved template in the singular.

**E2, add_teachers:**
- **Three lists:** `added` (successes; it was a count only), `skipped` (TEACHER_ALREADY_ON_LICENCE; it was silent) and coded `errors`.
- **Codes:** mapped from the refusal texts the copied beta code keeps.
- **One savepoint per teacher (SM Q2):** a new unit, `_add_one_teacher`, puts the account, seat and on_commit invitation in one savepoint. A failed unit leaves nothing, and the other teachers are added.
- **Logging:** ids and the code only.
- **Checkout:** the copied `_invite_and_enroll_one_teacher` is untouched and still serves checkout.

**Removal:** unknown, malformed, a student's, another school's and not-on-licence ids all answer TEACHER_NOT_ON_LICENCE alike. The removed ids are listed.

### F: SUBMISSION_NOT_GRADED (b404af1, b47bcea)
- The single publish of a submission that isn't fully graded answers a coded 400.
- publish-all lists every unpublishable submission in `skipped` (ids only, the code); `ungraded_count` is kept. "Nothing to publish" is still a 200 and carries `skipped` too.

## Two wordings QA approved after gate run 2 (the founder, 2026-10-01)

Folded in at the SM's instruction. Step 4's first attempt was **stopped** at about 00:40, after ~7 minutes, with no result claimed, so that the regression runs once on the final text.

1. **ROW_NAME_CLASH on an emailed row** gets its own remediation: "Two students in one course can't have exactly the same name, because papers are matched to students by name. Add a middle name or initial to tell them apart." It is carried as an approved `alternative_remediations` entry and chosen on both emailed clash paths. A row without an email keeps "Add an email address to tell the two students apart.".
2. **INSUFFICIENT_CREDITS_MID_BATCH with nothing finished** reads "Credits ran out before any of the {total} items were finished." instead of "after 0 of N". **This touches S7c's already-merged message** (1a's code; the SM told 1a):
   - It is a new, small spec mechanism: `alternative_messages` (a named second approved template), with `CodedError(variant=)` to pick it. A variant rides as the 6th arg only when present, so every existing error keeps its 4-tuple, and Celery's json and pickle round-trips keep it (tests).
   - The variant is picked in `students.task_tracking._mid_batch_error`, the one place both S7c raise sites build the error (1a's pointer). `completed` is recounted at each raise, so a later item of the same batch can truthfully read "after 1 of N".

## SM rulings applied
- **Q1:** the other-school check runs before the individual-subscription check (H-78, copied).
- **Q2:** one savepoint per teacher unit, with the on_commit invite inside it.
- **Q3:** the row number counts blank rows.
- **Q4:** ROSTER_EMPTY for a header alone or only blank rows.
- **Q5:** the ROW_DUPLICATE definition.
- **Q6:** renew-student-token is out of scope.
- **Q7:** ROW_EMAIL_INVALID applies to a header email column only.
- **The sync-only email exception (2026-10-01):** the four codes whose approved text names the address may carry `email` (`SYNC_ONLY_EMAIL_CODES`). A guard in `tests_codederror_serialization` pins that none is raised anywhere and that no task module names them or their builders. Condition (b): tests prove the address reaches no log line, for the roster and for add_teachers. It is documented in 08a §4.2.
- **The emailed-row clash remediation:** the approved text ships now. The founder (QA) is asked for an emailed-row alternative, which fits `alternative_remediations`.

## Documented limits
- ROW_EMAIL_INVALID applies to a **header-mapped** email column only. In a headerless paste, a non-address value is taken as a name part (Q7).
- ROW_NAME_CLASH on the no-email path is the direct-add serializer's defensive rule. The teacher's own-student match normally reaches such a student first. The test reaches it with that match switched off.
- A concurrent import racing on the same student can still end as ROW_FAILED, when the enrollment's "already enrolled" error isn't one of the mapped constants. The invariant that nothing is duplicated holds (tests_concurrency_and_resilience).
- `total_processed` still counts blank rows, as before. success + failure + skipped = the non-blank rows.

## Behaviour changes (frontend contract: the "⚠ Frontend contract changes" list, 0b's 4-point format)

| # | Route | Before | After | Tests updated |
|---|---|---|---|---|
| 1 | `POST course/{id}/bulk-add-students` with a file over 2 MB | **400** `{"file": ["This file is too large…"]}` | **413** FILE_TOO_LARGE envelope (int `actual`/`limit`, `dimension: "bytes"`) | tests_tenancy_and_roster, tests_security_penetration |
| 2 | same, a header alone or only blank rows | **200** with an empty result | **400** ROSTER_EMPTY (SM Q4) | new |
| 3 | same, whole-request refusals (no input, not UTF-8, too many rows) | 400 with a field error / ParseError text | 400 with the coded envelope and the approved texts | tests_tenancy_and_roster |
| 4 | same, every row not added | `{name, status, error}`, text only | `+ row, reason_code, error_class, message, remediation, retryable, params, reference`; `error` is the approved text with "Row N:"; `skipped_count` added | test_bulk_enrollment, tests_roster_ready_to_use, tests_cross_school_enrollment, tests_security_penetration |
| 5 | same, an emailed row with a 1-character or >150-character name | 1 char: created; >150: ROW_FAILED | ROW_NAME_INVALID, nothing created | tests_cross_school_enrollment (a one-letter fixture name changed to two letters) |
| 6 | answer uploads (single, batch) and assignment upload, a photo labelled PDF | 422 FILE_UNREADABLE, "We couldn't read …" | 422 **FILE_NOT_A_PDF**, "… is not a PDF. If it is a photo or scan, upload it as an image instead." | tests_pdf_type_validation, tests_upload_task_retry_policy, tests_file_reason_codes (found by step 4, see below) |
| 7 | `POST auth/register/student`, budget spent | 429, no code | 429 + `code`/`reason_code` REGISTRATION_PAUSED (text, Retry-After unchanged) | new |
| 8 | `POST license-subscriptions/{id}/add_teachers` | `{successful, failed, errors:[{teacher_email, error}]}`; seat and inactive refusals as plain 400 text | `+ added, skipped`; coded `errors`; LICENCE_* refusals coded (400). The seat messages end at the counts; the remediation moved to the envelope | test_license_teacher_changes_400 |
| 9 | `POST license-subscriptions/{id}/remove_teachers` | per-id text; an unknown id gave "We couldn't remove this teacher…" | coded `errors` (TEACHER_NOT_ON_LICENCE for every bad id), `removed` list | test_license_teacher_changes_400 |
| 10 | both licence routes, empty or non-list input | 400 `{"error": "teacher_emails is required"}` | 400 TEACHER_LIST_EMPTY envelope | new |
| 11 | `POST submissions/{id}/publish`, not fully graded | 400 `{"error": "Cannot publish an ungraded submission."}` | 400 SUBMISSION_NOT_GRADED envelope | new |
| 12 | `POST assignments/{id}/publish-all-grades` | counts only | `+ skipped: [coded entries]` | new |

## Gates (the ONE rule-15 run)

| Gate | Result | Log |
|---|---|---|
| Reproduce-first: S7d's new test modules with S7d's production files reverted to the epic tip 830bf8d (S7c included) | **Fails, as expected**, at import (`ReasonCode` has no `REGISTRATION_PAUSED`). The new modules name the new codes, so on the base they cannot load. That is the weak, import-level form, accepted as before | `prefix_830bf8d_failing.txt` |
| Changed and updated modules (23) + all 11 repo-wide guards (the 8 Epic A guards, `assignments`/`users.tests_schema_extension`, `AutoGrader.tests_error_messages`), at **023a51a** | **509 tests OK** (88.4 s) | `changed_modules_and_guards.txt` (last 200 lines; the full log is `epic-a-s7d_changed_modules_and_guards_023a51a_full.txt` in GAP-evidence-logs, chmod 600) |
| Mutation, 39 mutants in 3 batches (≤1800 s each, own DB `test_epic_a_s7d_mut`, dropped) at 023a51a, before the two wordings | **39/39 killed** by named tests, no survivors. Batch 1 (catalogue, B, C, F): 12/12. Batch 2 (D): 14/14. Batch 3 (E): 13/13. Source clean after each batch. 4 min 7 s in all | `mutation_log_batch{1,2,3}.txt`, `mutation_results_batch{1,2,3}.json` |
| After the two wordings: the modules they touch (incl. S7c's `students.tests_credits_mid_batch`, `tests_item_retry`, `tests_batch_item_results`) + `tests_reason_codes` / `tests_error_messages` / `tests_codederror_serialization` + all guards, at 41ba153 | **245 tests OK** (51.9 s) | `wording_modules_and_guards.txt` |
| Mutation batch 4: the two wordings, 7 mutants (own DB, dropped), at 41ba153 | **7/7 killed** by named tests, source clean after. Run 1 had V1/V2 survive because the new module's tests were never collected (below); the module re-run alone first showed "Ran 2 tests … OK" (`wording_test_module.txt`) | `mutation_log_batch4.txt`, `mutation_results_batch4.json` |
| ONE combined regression over every app whose production code S7d changes (SM ruling): students, classrooms, billing, users, assignments, ai_processor. ai_processor is included because C's change there is more than text: a new `PDFNotAPdfError`, and `PDFService.extract` raises it. Run in the 12G slot, timeout 1800 | **Run 1 at 41ba153: FAILED (failures=1, skipped=24), 4635 tests, 671 s.** The one failure is an S6b test that still expected the old code (below). Fixed in a test-only commit (3f3ba90); what re-runs is pending the SM/0b | `run1_failed_regression_combined.txt` (last 200 lines; full log outside the repo) |

**Runs before this record, disclosed:**
- **Dev run** (0b's grant, no result claimed, before E): it found the emailed-row clash and the test-harness fallout, both fixed above.
- **Gate run 1** (at e475967, stopped at step 2): 509 tests, 3 failures, all in the copied H-78 test module.
  - Two were a bug in that test, which also fails on beta: it checked for billing words over `logs.output`, whose lines carry the logger name `billing.license_service`. d5 fixed it at 45c36f2, re-copied here byte-identical at 023a51a.
  - One was S7d-side: S7d's add route runs its own unit and didn't emit H-78's "Skipped enrolling" line. The unit now emits that exact ids-only line (79b29f8).
  - Logs: `run1_stopped_*` (the module log is trimmed; the full copy is outside the repo).
- **Gate run 2** (at 023a51a): steps 1 and 2 passed (above), but the script stopped after step 2. Its stop check, `grep FAILED`, matched a licence-creation log line ("…teacher invitations FAILED…"), not the runner's summary. That was a script defect. The check now matches the runner's `FAILED (` only, and step 3 ran on its own without repeating steps 1–2.
- **Mutation batch 4 run 1** (at 99589dd): V1 and V2 survived because `MidBatchWordingTests` inherited only `GradingBatchFixture`, a mixin, so its tests were never collected. It is now a `(GradingBatchFixture, TestCase)` (41ba153). Logs: `run1_mutation_*_batch4_uncollected.*`.
- **Step 4 run 1** (at 41ba153): 4634 of 4635 passed. `assignments.tests_file_reason_codes.StudentUploadAnswersWithItsOwnCode.test_a_photo_labelled_as_a_pdf_is_unreadable_as_a_pdf` (S6b) still expected FILE_UNREADABLE for a JPEG uploaded as `photo.pdf`; S7d's C deliberately answers FILE_NOT_A_PDF there (the body was the approved FILE_NOT_A_PDF envelope, 422). My changed-modules set missed this module, because I chose it by the files C touched, not by a grep for the old code in tests. The test now expects FILE_NOT_A_PDF and is renamed `test_a_photo_labelled_as_a_pdf_is_not_a_pdf` (3f3ba90). That is a test-only change; no production file differs from 41ba153. The full log has exactly one FAIL/ERROR line. The trimmed logs had trailing whitespace stripped by the pre-commit hook.
- **After step 4 run 1, SM ruling (rule 15.4, test-only commit): run only the touched module, plus C's mutants against it; no 12G re-run.** First the hole that caused the miss was closed with a repo-wide grep of every test file for the old code (FILE_UNREADABLE and the unreadable error classes) and for the photo-as-PDF case (`step4_fix_grep.txt`, the commands and all hits).
  - Outside the six regressed apps the only hits are in AutoGrader: `tests_reason_codes` (the FR-A-06 code list and generic CodedError argument checks) and `tests_codederror_serialization` (a class-name list). None of them asserts the photo-as-PDF answer, and both ran green in step 2 and in the wording run.
  - Inside the six apps, every other FILE_UNREADABLE hit is a different case: a garbage PNG, a rasterizer error, a truncated PDF, or item-retry and batch-result codes. All of them passed in step 4 run 1. Every photo-as-PDF hit already expects FILE_NOT_A_PDF.
  - So the 6G re-run is `assignments.tests_file_reason_codes` alone, plus mutation batch 5 (C1–C3, a superset of the ruled C1/C3) against it.
- **The ruled re-run, at 8b42ae8** (0b's grant, 6G, rule-16 prefix): `assignments.tests_file_reason_codes` gave **22 tests OK** (`step4_fix_module.txt`). Mutation batch 5 (own DB, dropped): **C1–C3 3/3 killed**, source clean after (`mutation_log_batch5.txt`, `mutation_results_batch5.json`). The fixed test kills C1 and C2; C3 is killed by the module's damaged-PDF tests. The step-4 rule-15 record is therefore: run 1 at 41ba153 (4634/4635), plus this re-run for the one test-only change. Production is identical to 41ba153.

## Base update onto the epic after the bundle 4 merge-down (dc0475aa)

- **The merge, 32d941ef (0b; parents 08f8d9b and dc0475aa).** It conflicted in `billing/license_service.py`. The SM ruled S7d's side for both hunks; 0b resolved, and I reviewed the uncommitted merge read-only, by AST function segments, against both parents and batch-5 c4ac9e08.
  - Hunk 1 (~:818): S7d's ids-only "Skipped enrolling a teacher in license %s (school %s): %s" line, with (license_sub.id, school.id, type(exc).__name__). It equals batch-5's. The epic's older 2-arg form logged `exc` itself, whose text can carry the address.
  - Hunk 2 (`_enroll_teacher_internal`): S7d's version, plus one non-conflicting bundle 4 line (`expires_at=grace_expiry(next_refresh, license_sub.billing_cycle_end)`). The duplicate post-subscription school check is gone on both sides.
- **The H-78 fold, copied byte-identical (291d8ca7).** The source is task/h78-other-school-first-b4, 1a VERIFIED at 560eced.
  - 9675d17's hunk: `_enroll_teacher_internal` logs the individual-subscription refusal as "Teacher %s has an individual subscription: not enrolled." with teacher.id, not the address-bearing `error_msg`.
  - `billing/tests/test_other_school_before_subscription.py` is byte-identical to b9e4ccb (= 8c248e8; b9e4ccb added docs only).
- **The carried functions against batch-5 c4ac9e08** (the SM's byte-identity check):
  - `_invite_and_enroll_one_teacher` and `_get_or_invite_teacher`: **byte-identical**.
  - `_enroll_teacher_internal`: identical apart from two differences.
    - **Epic-only:** "Created CreditWallet for teacher %s" logs `teacher.id` on the epic and `teacher.email` on batch-5. The epic's is the ids-only form, so it is kept.
    - **Beta-only, expected:** H-76's `existing_monthly.is_processed = True` retirement. H-76 is in bundle 5 and arrives with the bundle 5 merge-down.
- **The guard-limits docstring** (v2's S7d note 2, SM): `sync_only_violations` now names its two blind spots, an error bound to a variable and raised later, and a task reaching `import_roster` or `add_teachers_batch` through a helper module. Comment only.
- `makemigrations --check`: no changes detected.
- **Re-run on the new base** (the changed modules + all guards + the billing regression, in 0b's slot): see below.
