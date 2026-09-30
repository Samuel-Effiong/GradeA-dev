# Verification: Epic A S6d @ f0068ef

**Verifier:** Verification Engineer 2 (v2). **Author:** Hardening (d5). **Date:** 2026-09-30.
**Branch:** task/epic-a-s6d @ **f0068ef**:
- 2510137: RUBRIC_MISSING at all entry points;
- f750b08: empty text → SUBMISSION_EMPTY;
- eb453fa: PROVIDER_FAILURE coded `from last_error`, 503 + Retry-After, the credit clause, the audit class;
- 427c28f: F1's refund scope around `upload_answers_engine`;
- base updates 63f47f7 and **fd292d1** (the S6d × S7a resolution);
- the runner, then the evidence.

Design: 08a §1 #6/#7/#9, §5 S6d, F1/F5, §6.1 (F6 = no change; no blank-answer gate).

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, with every repo-wide guard (rule 15 addendum 2). d5's students regression (311 OK) and 17/18 battery (S11 a documented equivalent; S11b killed) are cited.

**Verdict: VERIFIED.**

## Static
- **Gate coverage, all 7 entry points** (v2's prep found 7, not 5): `ensure_gradable` runs in `grade`, `grade-async`, `schedule-grade-async` (students/views), `grade-all` and the assignment schedule route (assignments/views) at request time. `rubric_missing` runs at run time in `grade_batch_async` and `auto_grade_due_assignment`. The chokepoint `ensure_gradable` inside **`grade_engine`** also covers sync grade and every `grade_engine_async`, so a queued or scheduled single grade and **S7b's retry relaunch** are refused at run time too.
- **F5 definition** (`_has_marking_guide`): a rubric with ≥1 level, OR a non-blank model answer (SM-confirmed). Missing = no questions, or any question with no guide. A 1-level rubric is not missing.
- **fd292d1** (`git show --remerge-diff`): the hand edits in `assignments/tasks.py` match the SM's ruling. Before dispatch, the request is refused (409, zero tracked rows). At run time (the rubric removed after scheduling), each submission becomes a refused tracked item in S7a's shape (`record_refused_item`, `item_index` 1..n, RUBRIC_MISSING), recorded and audited, with no AI call, claim or charge. The two test files were taken whole (0b checked they're byte-identical to d5's tested tree).
- **F6 unchanged:** the `update_grade` body is byte-identical to the base; `users/permissions.py` (HasCreditBalance) has no diff.
- **#9:** `raise provider_failure(last_error, max_retries) from last_error` at both provider sites. The message is the spec's template plus the credit clause; the provider text is only in `detail` (logs).

## Evidence
| Check | Result |
|---|---|
| v2 probe (`tests_vf2_s6d_probe.py`) + `students.tests_s6d_{rubric_gate,submission_empty,provider_failure,extraction_refund}` + `students.tests_batch_item_results` + every guard | **143 OK** |
| R1 `questions=None` / R2 `[]` / R3 no guide (rubric `[]`, blank model answer) × H1 grade, H2 grade-async, H3 schedule-grade-async, H4 grade-all, H5 schedule-grade-all | **15/15 → 409 RUBRIC_MISSING**, each with **no AI call, no Celery dispatch, no ledger row, no claim** (`graded_at` stays None) |
| R4 control: a 1-level rubric via grade-async | **200** (not refused) |
| T1 time-of-check: an auto-grade assignment whose rubric was removed after scheduling, the beat task run | no AI call, no ledger row, submission not graded |
| v2 mutants (`vf_s6d_mutants.py`) | **4/4 KILLED**, by d5's tests as well as v2's: SM1 a whitespace model answer counted as a guide (13 failures); SM2 `Retry-After` dropped on the 503; SM3 `from last_error` dropped (the cause lost; the audit class wrong); SM4 the provider text leaked into the message params |

**Behaviour changes** (d5's 4-point records; the F7 staging check before beta applies): whitespace text 400 → 422 SUBMISSION_EMPTY; exhausted provider retries → coded PROVIDER_FAILURE 503 + Retry-After (the old "attempts failed" text only in `detail`); the credits/plan audit error class MODEL → USER.

Logs: `runs/s6d_run1.log`, `runs/s6d_run2_mutants.log`.

---

## Addendum: F1 ("always refund"), confirmed (the SM asked; it was missing above), 2026-09-30
- **Static:** `upload_answers_engine` wraps the answer extraction in `billing_refund_scope(reason="answer upload failed before the submission was persisted")` (`students/services.py:925`). The post-save notification stays outside the scope.
- **d5's test** `students.tests_s6d_extraction_refund.test_a_mid_chunk_failure_nets_the_ledger_to_zero`: a real six-page upload in two chunks; chunk 1 is charged on every outer attempt (3 charges, ledger rows grow, so not vacuous) and chunk 2 times out every time. It asserts the wallet's **used credits are back to their starting value** (net zero), `ProviderFailureError.params == {"credit_clause": REFUNDED}`, and no submission exists. The control `test_a_successful_upload_keeps_its_charges` passes.
- **v2 run @ f0068ef:** baseline 2 OK; **mutant F1M (the refund scope replaced by `if True:`) KILLED** by the mid-chunk test (`vf_s6d_f1_mutant.py`; restore sha-checked).

F1 holds. The verdict stays **VERIFIED**. Log: `runs/s6d_f1.log`. (The record committed at 433b64a predates this addendum; please re-commit this file.)
