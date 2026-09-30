# Epic A S6d: the grading gates (FR-A-06 #7, #6 for empty text, #9; F1)

**Branch:** `task/epic-a-s6d`, off the epic tip `4124d73` (S4 and S6c in). Its base is updated by 0b once the CodedError serialization slice lands. **Author:** Hardening (d5). **Verifier:** v2. **Design:** `docs/phase2/architecture/08a_epic_a_s6_s7_reason_codes_and_batch_design.md` §1, §5 (the S6d row), §6.0 (F1, F5), and §6.1 (the final ruling on F6).

## What S6d delivers

| Part | What |
|---|---|
| 1. RUBRIC_MISSING (#7, 409) | `students/grading_gates.py`. F5 as the founder set it: no questions, or any question with no marking guide at all. A marking guide is a rubric with at least one level, **or a model answer**: an objective question's answer key is its model answer, which the SM confirmed, since otherwise every answer-key question would be refused. A one-level rubric is a guide and is not refused. One check in `grade_engine`, before the claim, the AI call and any charge, so all 7 grading entry points refuse. The 5 HTTP routes also check before queuing or scheduling (409, nothing dispatched). The scheduled and automatic runs re-check when they fire (`auto_grade_due_assignment`, `grade_batch_async`, and `grade_engine_async` through `grade_engine`), recording each item FAILED with the RUBRIC_MISSING message and auditing `GRADING_FAILED` with `reason_code=RUBRIC_MISSING`. |
| 2. SUBMISSION_EMPTY for empty text (#6, 422) | A raw-text edit (`PATCH submissions/<id>`, `POST …/update-async`, and the service they and the background task share) whose text is present but empty or whitespace: 422 with the coded envelope, "The submitted text has no student answers to grade.", before the billed extraction. A **missing** `raw_input` field stays a 400 validation error. Empty FILES were S6b's. |
| 3. PROVIDER_FAILURE (#9, 503 + Retry-After, retryable) | `ai_processor/exceptions.py`. When grading (`extract_grade_with_retry`) or answer extraction (`extract_answer_with_retry`) cannot finish after its retries, it raises `ProviderFailureError` **`from` the last attempt's error**. The grading retry used to raise a bare `Exception` with no `__cause__`, so a grading timeout couldn't be told from a code fault. The synchronous routes (grade; the raw-text edit) answer 503 with `Retry-After: 30` and the coded envelope, with no provider text in the body. The message's credit clause is "The credits were refunded." when the run's refund scope holds a charge (`billing.refunds.charges_in_open_scope`), otherwise "No credits were charged." (F1: a failed call itself is never charged). The async audit event carries `reason_code=PROVIDER_FAILURE`, and is PROVIDER when the cause is a recognised infra failure and MODEL when it is not (unusable output). Assignment extraction and generation are outside S6d. |
| 4. F1: the extraction refund scope | `upload_answers_engine` now runs its extraction and the submission's save inside one `billing_refund_scope`, as grading and the raw-text edit already did. Every chunk charged (on every outer attempt) is refunded if the upload fails before the submission is persisted. The teacher notification stays outside the scope, so a failed notice never refunds a saved submission. `students/tests_s6d_extraction_refund.py`: a six-page upload whose chunk 2 times out on every attempt, while chunk 1 is charged three times, **nets the ledger to zero**, and says "The credits were refunded."; a successful upload keeps its charges. |

## Behaviour change: the 4-point record (part 2)

1. **Previous assertion** (`students/tests_async_edit_path.py`): `update-async` with whitespace text answers **400** ("raw_input is required.").
2. **New intended assertions:** whitespace text answers **422** with `reason_code` SUBMISSION_EMPTY, and nothing is queued. A request with **no** `raw_input` field still answers **400**. The PATCH route behaves the same way.
3. **Why this is correct:** FR-A-06 #6, as the founder's final ruling scopes it (08a §6.1): "SUBMISSION_EMPTY fires for empty files only (zero pages, empty text input)". Present-but-empty text is an empty submission, not a malformed request, so it gets the coded 422 its catalogue entry specifies. A missing field is still a malformed request.
4. **Tests that prove it:** `students.tests_async_edit_path.UpdateAsyncRouteTest.test_empty_or_missing_text_and_unpublished_assignment_are_refused`, and `students.tests_s6d_submission_empty` (both routes, three kinds of empty text, the missing field, the service, and the all-blank control).

## Behaviour change: the 4-point record (part 3, the exhausted-retry failure)

1. **Previous assertions:** exhausting the grading or answer-extraction retries raised a plain `Exception` whose message was "All 3 attempts failed. Last error: …" (`ai_processor.tests_answer_chunk_merge`, `tests_answer_benchmark_failures`, `tests_answer_extraction_gate` and `tests_grading_benchmark` asserted that text in `str(error)`).
2. **New intended assertions:** it raises `ProviderFailureError` (PROVIDER_FAILURE). `str(error)` is the coded message ("The grading service couldn't finish this item. …"), the attempts text is in the log-only `detail`, and the cause is on `__cause__`.
3. **Why this is correct:** FR-A-06 #9 and 08a §5 (the S6d row: "ProviderFailureError `from last_error`"). The old text, with the provider's raw error inside it, is exactly what must not reach a user (QA-ERR-03); it stays in the logs through `detail` and `__cause__`.
4. **Tests that prove it:** the four tests above, updated to assert the class and `detail`; `students.tests_s6d_provider_failure` (the sync 503 with no provider text, the cause kept, the async audit class and code, and the credit clause both ways).

## Behaviour change: the 4-point record (part 3, the refusals' audit class)

1. **Previous assertion** (`assignments.tests_grading_audit_events`): a grading run refused for credits or plan emits `GRADING_FAILED` classed **MODEL** (the test called it a "model refusal").
2. **New intended assertion:** classed **USER**, for both refusals (`InsufficientCreditsError`, `AIFeatureNotAvailableError`).
3. **Why this is correct:** 08a §2.2 records this as a misclassification: credits and plan are the user's to resolve, and their catalogue entries (`INSUFFICIENT_CREDITS`, `AI_FEATURE_NOT_AVAILABLE`) are class USER. `_grading_failure_error_class` now takes the class from the catalogue through `reason_of`.
4. **Test that proves it:** `test_a_credits_or_plan_refusal_emits_exactly_one_failed_event_classed_user` (both refusals).

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
| Parts 3 and 4: the 40 modules that reach the changed code, plus the repo-wide guards (rule 15 addendum 2: `tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`, `audit.tests_history_guard`, `audit.tests_route_coverage`) | 570 ran, **5 not passing**: 4 errors were my two audit tests deleting `AuditEvent` rows, which the append-only model refuses (they now assert only on new events); 1 was the wildcard guard naming `audit/bench_volume.py`, the epic base's known S8 harness issue (ed's `47b21e1` moves it; not S6d) | `c34_modules.log` |
| The two audit-test modules after the fix | 14 ran, OK | `c34_recheck.log` |
