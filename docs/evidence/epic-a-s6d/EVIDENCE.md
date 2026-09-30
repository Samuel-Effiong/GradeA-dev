# Epic A S6d: the grading gates (FR-A-06 #7, #6 for empty text, #9; F1)

**Branch:** `task/epic-a-s6d`, off the epic tip `4124d73` (S4 and S6c in). Its base is updated by 0b once the CodedError serialization slice lands. **Author:** Hardening (d5). **Verifier:** v2. **Design:** `docs/phase2/architecture/08a_epic_a_s6_s7_reason_codes_and_batch_design.md` §1, §5 (the S6d row), §6.0 (F1, F5), and §6.1 (the final ruling on F6).

## What S6d delivers

| Part | What |
|---|---|
| 1. RUBRIC_MISSING (#7, 409) | `students/grading_gates.py`. F5 as the founder set it: no questions, or any question with no marking guide at all. A marking guide is a rubric with at least one level, **or a model answer**: an objective question's answer key is its model answer, which the SM confirmed, since otherwise every answer-key question would be refused. A one-level rubric is a guide and is not refused. One check in `grade_engine`, before the claim, the AI call and any charge, so all 7 grading entry points refuse. The 5 HTTP routes also check before queuing or scheduling (409, nothing dispatched). The scheduled and automatic runs re-check when they fire (`auto_grade_due_assignment`, `grade_batch_async`, and `grade_engine_async` through `grade_engine`), recording each item FAILED with the RUBRIC_MISSING message and auditing `GRADING_FAILED` with `reason_code=RUBRIC_MISSING`. |
| 2. SUBMISSION_EMPTY for empty text (#6, 422) | A raw-text edit (`PATCH submissions/<id>`, `POST …/update-async`, and the service they and the background task share) whose text is present but empty or whitespace: 422 with the coded envelope, "The submitted text has no student answers to grade.", before the billed extraction. A **missing** `raw_input` field stays a 400 validation error. Empty FILES were S6b's. |
| 3. PROVIDER_FAILURE (#9) | (to come) |
| 4. F1: the extraction refund scope | (to come) |

## Behaviour change: the 4-point record (part 2)

1. **Previous assertion** (`students/tests_async_edit_path.py`): `update-async` with whitespace text answers **400** ("raw_input is required.").
2. **New intended assertions:** whitespace text answers **422** with `reason_code` SUBMISSION_EMPTY, and nothing is queued. A request with **no** `raw_input` field still answers **400**. The PATCH route behaves the same way.
3. **Why this is correct:** FR-A-06 #6, as the founder's final ruling scopes it (08a §6.1): "SUBMISSION_EMPTY fires for empty files only (zero pages, empty text input)". Present-but-empty text is an empty submission, not a malformed request, so it gets the coded 422 its catalogue entry specifies. A missing field is still a malformed request.
4. **Tests that prove it:** `students.tests_async_edit_path.UpdateAsyncRouteTest.test_empty_or_missing_text_and_unpublished_assignment_are_refused`, and `students.tests_s6d_submission_empty` (both routes, three kinds of empty text, the missing field, the service, and the all-blank control).

## Stated gaps (the founder's final ruling, 08a §6.1)

- **Blank ANSWERS are not refused.** An all-blank paper is AI-graded and charged as today (`test_an_all_blank_paper_is_not_refused` pins it). Blank-answer detection is deferred.
- **No free manual 0.** When a submission's text is edited to nothing, the row exists, and the teacher still has no free, no-AI way to record a 0 for it. Extending `update-grade` (and waiving `HasCreditBalance` there) is the recorded follow-up, for when the frontend is ready. It is not built here (the SM confirmed, correcting an earlier instruction).
- `DUPLICATE_SUBMISSION` is still defined but not raised (S6a's gap).

## Runs

Dev runs, each under `systemd-run` MemoryMax=6G, `nice -n 10` and `timeout`, with `EXEMPT_EMAIL_DOMAINS` empty. Rule 15's gates (changed modules, a mutation battery, the owning-app regression) come once parts 3 and 4 are in.

| Run | Result | Log |
|---|---|---|
| Part 1: the new module + the 19 modules that grade | first run 290, **30 not passing**: old fixtures without a marking guide, refused as designed (they gained a `model_answer`, with no assertion changed), and two mistakes in my new tests | `c1_modules_first.log` |
| Part 1 again, with S6a's suite | 324 ran; the only failures were the pickling test, since moved to its own slice (verified by v2, merged in epic-a `3d6575c`) | `c1_modules_second.log` |
| Part 2: the new module + the 8 modules that edit submission text | 242 ran, **1 failure: the intended change** (whitespace text 400 → 422; the 4-point record is above) | `c2_modules.log` |
| Part 2: those two modules after the assertion update | 21 ran, OK | `c2_recheck.log` |
