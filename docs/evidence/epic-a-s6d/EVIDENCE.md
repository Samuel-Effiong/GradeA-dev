# Epic A S6d: the grading gates (FR-A-06 #7, #6 for empty text, #9; F1)

**Branch:** `task/epic-a-s6d`, off the epic tip `4124d73` (S4 and S6c in). Base updates by 0b: epic-a `3d6575c` (`63f47f7`: the CodedError serialization slice and auth-lock) and epic-a `fc52af3` (`fd292d1`: S7a and the bench move; the conflict is resolved below). **Author:** Hardening (d5). **Verifier:** v2. **Design:** `docs/phase2/architecture/08a_epic_a_s6_s7_reason_codes_and_batch_design.md` §1, §5 (the S6d row), §6.0 (F1, F5), and §6.1 (the final ruling on F6).

## What S6d delivers

| Part | What |
|---|---|
| 1. RUBRIC_MISSING (#7, 409) | `students/grading_gates.py`. F5 as the founder set it: no questions, or any question with no marking guide at all. A marking guide is a rubric with at least one level, **or a model answer**: an objective question's answer key is its model answer, which the SM confirmed, since otherwise every answer-key question would be refused. A one-level rubric is a guide and is not refused. One check in `grade_engine`, before the claim, the AI call and any charge, so all 7 grading entry points refuse. The 5 HTTP routes also check before queuing or scheduling (409, nothing dispatched). The scheduled and automatic runs re-check when they fire (`auto_grade_due_assignment`, `grade_batch_async`, and `grade_engine_async` through `grade_engine`), recording each item FAILED with the RUBRIC_MISSING message and auditing `GRADING_FAILED` with `reason_code=RUBRIC_MISSING`. |
| 2. SUBMISSION_EMPTY for empty text (#6, 422) | A raw-text edit (`PATCH submissions/<id>`, `POST …/update-async`, and the service they and the background task share) whose text is present but empty or whitespace: 422 with the coded envelope, "The submitted text has no student answers to grade.", before the billed extraction. A **missing** `raw_input` field stays a 400 validation error. Empty FILES were S6b's. |
| 3. PROVIDER_FAILURE (#9, 503 + Retry-After, retryable) | `ai_processor/exceptions.py`. When grading (`extract_grade_with_retry`) or answer extraction (`extract_answer_with_retry`) cannot finish after its retries, it raises `ProviderFailureError` **`from` the last attempt's error**. The grading retry used to raise a bare `Exception` with no `__cause__`, so a grading timeout couldn't be told from a code fault. The synchronous routes (grade; the raw-text edit) answer 503 with `Retry-After: 30` and the coded envelope, with no provider text in the body. The message's credit clause is "The credits were refunded." when the run's refund scope holds a charge (`billing.refunds.charges_in_open_scope`), otherwise "No credits were charged." (F1: a failed call itself is never charged). The async audit event carries `reason_code=PROVIDER_FAILURE`, and is PROVIDER when the cause is a recognised infra failure and MODEL when it is not (unusable output). Assignment extraction and generation are outside S6d. |
| 4. F1: the extraction refund scope | `upload_answers_engine` now runs its extraction and the submission's save inside one `billing_refund_scope`, as grading and the raw-text edit already did. Every chunk charged (on every outer attempt) is refunded if the upload fails before the submission is persisted. The teacher notification stays outside the scope, so a failed notice never refunds a saved submission. `students/tests_s6d_extraction_refund.py`: a six-page upload whose chunk 2 times out on every attempt, while chunk 1 is charged three times, **nets the ledger to zero**, and says "The credits were refunded."; a successful upload keeps its charges. |

## The merge with S7a (`fd292d1`, the SM's ruling)

S7a (`a52d133`) rewrote batch-grading dispatch into `_dispatch_tracked_grading` (a tracked processing task per item, `item_index` 1..n), exactly where S6d part 1 put the run-time RUBRIC_MISSING re-check. So `assignments/tasks.py` conflicted (4 hunks). The SM ruled:

1. **HTTP routes:** RUBRIC_MISSING is an assignment-level condition, so it gets a whole-request 409 before `_dispatch_tracked_grading`, with **zero** tracked rows, no claim and no charge. That is not S7a's per-item shape. The route checks already did this, and the tests now assert zero tracked rows.
2. **The run-time re-check** (`grade_batch_async`, `auto_grade_due_assignment`, when the rubric was removed after scheduling): it runs before `_dispatch_tracked_grading`, and records each submission as a refused tracked item in S7a's per-item shape through S7a's `record_refused_item` (`item_index` 1..n, `reason_code` RUBRIC_MISSING, never dispatched). It keeps the legacy results entry that a failed tracked item also gets, and audits `GRADING_FAILED` with the item's id.

The resolution was made and tested in a disposable worktree (d63792f + the merge; 48 OK: `rule15/merge_check_d63792f+fc52af3.log`). 0b applied the three resolved files byte-identical: `assignments/tasks.py`, `students/tests_s6d_rubric_gate.py`, and S7a's `students/tests_batch_item_results.py`, whose fixture question gained a `model_answer` (1a agreed) or the gate would refuse its batch tests. 1a (S7a's author) also agreed the S7c boundary: S7c's credits check runs first in `grade_engine_async`, then this gate inside `grade_engine`.

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

## Rule 15: the author's gates (at `3c5aa16`, after the S7a merge)

| Run | Result | Log |
|---|---|---|
| Changed modules after the merge: the repo-wide guards (addendum 2: `tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`, `audit.tests_history_guard`, `audit.tests_route_coverage`), the four S6d modules, S7a's `tests_batch_item_results` and `tests_grading_audit_events` | **105 ran, OK**; the wildcard guard is green with the bench move | `rule15/guards_and_s6d_3c5aa16.log` |
| Mutation battery (`run_mutants.py`): 17 mutants, then S11b | **16 of 17 killed** at `3c5aa16`; S11b (added at `5eea559`) killed. **17 of 18, with one equivalent** | `rule15/mutation_battery_3c5aa16.log`, `rule15/mutation_S11b_5eea559.log`, `results.tsv`, `logs/` |
| **ONE owning-app regression: `students`** (it owns `grade_engine`, the gates, the upload/extraction service and both S6d route sets; the `ai_processor`, `assignments` and `billing` changes are covered by the dev runs' 40 modules) | **311 ran, OK** (1 skipped, pre-existing) | `rule15/app_students_3c5aa16.log` |

**The equivalent mutant.** S11 removes the empty-text check from the PATCH route. The shared service (`update_submission_from_raw_text`) raises the same SUBMISSION_EMPTY before any extraction or charge, and the route turns it into the same coded 422, so nothing observable changes; the route check is defence in depth there. On `update-async`, the route check is what stops a task being queued: S11b removes it and is killed.

| Mutant | Guard | Result |
|---|---|---|
| S01 | grade_engine refuses before the claim | KILLED (FAILED (failures=16, skipped=1)) |
| S02 | a model answer is a marking guide | KILLED (FAILED (failures=8, skipped=1)) |
| S03 | ANY question without a guide is missing | KILLED (FAILED (failures=6, skipped=1)) |
| S04 | a one-level rubric is a guide | KILLED (FAILED (failures=2, skipped=1)) |
| S05 | grade-async refuses before queuing | KILLED (FAILED (failures=12, skipped=1)) |
| S06 | schedule-grade-async refuses before scheduling | KILLED (FAILED (failures=9, skipped=1)) |
| S07 | grade-all refuses before queuing | KILLED (FAILED (failures=6, skipped=1)) |
| S08 | schedule-grade-all refuses before scheduling | KILLED (FAILED (failures=3, skipped=1)) |
| S09 | a scheduled batch re-checks at run time | KILLED (FAILED (failures=1, skipped=1)) |
| S17 | a run-time refusal records S7a's coded tracked item | KILLED (FAILED (errors=2, skipped=1)) |
| S10 | auto-grade re-checks at run time | KILLED (FAILED (failures=1, skipped=1)) |
| S11 | empty text is SUBMISSION_EMPTY at the route | **SURVIVED**: an equivalent mutant (below) |
| S12 | the service refuses empty text as SUBMISSION_EMPTY | KILLED (FAILED (errors=3, skipped=1)) |
| S13 | the credit clause says refunded when a charge is in scope | KILLED (FAILED (failures=2, skipped=1)) |
| S14 | the grading failure keeps its cause | KILLED (FAILED (failures=2, skipped=1)) |
| S15 | an upload's chunk charges are refunded (F1) | KILLED (FAILED (failures=1, skipped=1)) |
| S16 | a non-infra provider failure is MODEL | KILLED (FAILED (failures=1, skipped=1)) |
| S11b | update-async refuses empty text before queuing | KILLED (FAILED (failures=5, skipped=1)) |

## For the verifier (v2)

- **The S7a merge** (`fd292d1`): check the remerge diff and both behaviours (the 409 with zero tracked rows; the run-time coded items).
- **F5's definition:** a model answer counts as a marking guide (the SM confirmed; the founder is told).
- **Behaviour changes** carrying 4-point records: whitespace text 400 → 422; exhausted retries give a coded PROVIDER_FAILURE (the old text is now in `detail`); the credits/plan refusals' audit class MODEL → USER.
- **Not in S6d, by the founder's final ruling:** blank-answer detection; a free manual 0 (`update-grade`).
- **Epic-base items, not S6d:** the bench wildcard (fixed by ed's move, and green here).
