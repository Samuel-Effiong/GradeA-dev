# Epic A S7c: credits running out mid-batch (FR-A-07, 08a §4.5, §5). Author's evidence

**Author:** 1a (grade-automator-plus-c2). **Verifier:** v2. The author does not verify this.
**Branch:** `task/epic-a-s7c`. It was cut off S7b's `923b2b8`; the change is `3bce7d6`. After S7b's H-38 fix merged into the epic (`ca35971`), 0b base-updated S7c onto it at **`5bef042`**. The S7b × S7c overlap in `students/exceptions.py` and `students/item_retry.py` was resolved by me and supplied as files; 0b ran the merge. **S7c depends on S7b** (a resume is S7b's retry). The runs below are at `5bef042`, then at the fix round's **`70500e7`** (see *Fix round*), each with the worktree clean.

## What changed
| Where | Behaviour |
|---|---|
| `students/task_tracking.py` `batch_credit_refusal` | Applies to a **plain** credit refusal (`InsufficientCreditsError`, not already coded) of a batch item whose session has another item **STARTED or SUCCESS**. That refusal becomes **INSUFFICIENT_CREDITS_MID_BATCH** ("Credits ran out after {completed} of {total} items. The finished items are saved."), and the session is marked (`credits_exhausted_at`, a conditional UPDATE, so the first refusal's time is kept). The wallet's own text (balance, estimate) never reaches the item: it stays on `__cause__` for the log only. |
| `ensure_batch_has_credits` | Grading checks the mark right after the item starts. Answer upload checks it before the billed extraction. A marked batch fails the item with the same code **before any provider call**, so it is never charged. |
| `students/item_retry.py` | **Any** retry of a batch item that wins its claim (S7b's resume, after a top-up) clears the mark; a losing claim never does. A real shortfall re-marks the batch before any charge (fix round, `70500e7`). |
| `AutoGrader/error_messages.py` | A **coded** credit refusal shows its catalogue message. A plain one keeps the fixed INSUFFICIENT_CREDITS text. |
| `BatchUploadSession.credits_exhausted_at` | Nullable; migration `students/0030`. No billing migration. Nullable, so a code-only rollback still inserts sessions (H-56). |

The first item of a batch refused before anything else ran keeps the plain **INSUFFICIENT_CREDITS** (the pre-flight meaning). session-results derives `stopped_at_item` (the first MID_BATCH item) and `resumable` (S7a).

**Resume:** grade items are retried in place with `retry-failed {"reason_codes": ["INSUFFICIENT_CREDITS_MID_BATCH"]}`. The completed items are untouched: same `graded_at`, `retry_count` 0. Upload items are re-uploaded (F4: 409 `resolution: "replace_file"`).

**Known edge (stated, not a defect):** batch items run concurrently. If two items start together on a wallet that is already empty, the second refusal sees the first STARTED and is labelled MID_BATCH ("after 0 of N"). That is truthful, since another item was underway, and nothing is charged either way.

## Coverage
| Production file | Covering modules |
|---|---|
| `students/task_tracking.py`, `students/exceptions.py`, `students/models.py` + `0030` | `students.tests_credits_mid_batch`; `students.tests_batch_item_results`; the model-change regression (addendum 1: students, users, assignments read the session) |
| `assignments/tasks.py` (the two checks) | `students.tests_credits_mid_batch`; the `assignments` regression |
| `students/item_retry.py` | `students.tests_credits_mid_batch`, `students.tests_item_retry`, `students.tests_item_retry_h38` |
| `AutoGrader/error_messages.py` | `AutoGrader.tests_error_messages`, `AutoGrader.tests_reason_codes`, `AutoGrader.tests_codederror_serialization` |
| Repo-wide guards (addendum 2: a new migration) | all eight |

## Runs
Every run was under `systemd-run --user --scope` with `MemorySwapMax=0` (6G targeted, 12G for the regression), `nice -n 10` and `timeout -k 60`, in 0b's slots. The script is `run_gates.py.txt`, the mutants `mutants.py.txt` (K1–K8 ran at `5bef042`; K9–K11 were added for the fix round), and the mutants' settings `settings_mut.py.txt`. Full, untrimmed copies of every log are kept outside the repo (`GAP-evidence-logs/epic-a-s7c/`).

| Run | Tree | Result | Log |
|---|---|---|---|
| **Reproduce-first:** the new module with S7c's 6 production files reverted to the epic base `ca35971` (the migration stays, so the column exists in the DB) | `5bef042` − S7c | **4 tests, 3 failures + 1 error, all red.** The rest of a grading batch was still charged and run (`4 != 3`). The upload batch ran its rest (`3 != 2`). The resume didn't complete the rest (`[] != [two ids]`). The error is the reverted model lacking `credits_exhausted_at`. | `logs_5bef042/repro_s7c_reverted.log` |
| **The changed modules + all 8 guards** (`tests_credits_mid_batch`, `tests_item_retry`, `tests_item_retry_h38`, `tests_batch_item_results`, `tests_reason_codes`, `tests_codederror_serialization`, `tests_error_messages`, plus `tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`, `tests_redis_test_isolation`, `audit.tests_route_coverage`, `audit.tests_history_guard`, `classrooms.tests_teacher_access_sweep`, `classrooms.tests_course_roster_scope_sweep`) | `5bef042` | **Ran 184, OK** | `logs_5bef042/changed_modules_and_guards.log` |
| **ONE model-change regression** (addendum 1): `students`, `users`, `assignments`, `-v 2`, stamped; `pg_stat_activity` snapshot first; 12G, `flock` on the machine lock, `timeout -k 60 1800`, `RACE_COST_*`/`AUDIT_BENCH` unset | `5bef042` | **Ran 1692, OK (skipped=18)**, 23:47:22–23:51:57 (262 s of tests) | `logs_5bef042/regression_students_users_assignments.log.gz` (gzipped: over the 500 KB hook limit raw); snapshot `logs_5bef042/regression_pg_stat_activity_before.txt` |

## Mutants (author's)
8 mutants, each anchor matching exactly once at `5bef042` and restored with a sha256 check. They ran on their own DB (`test_epic_a_s7c_mut`, dropped after) against `students.tests_credits_mid_batch` + `students.tests_item_retry`. **8/8 KILLED.**

| Mutant | Killed by |
|---|---|
| K1: no mid-batch conversion | 3 failures (the grading stop, the upload stop, the resume) |
| K2: the session is never marked | `test_the_rest_of_the_batch_stops_uncharged_with_its_own_code`, `test_the_rest_stops_uncharged_and_resumes_by_re_upload` |
| K3: grading doesn't check the mark | `test_the_rest_of_the_batch_stops_uncharged_with_its_own_code` |
| K4: upload doesn't check the mark | `test_the_rest_stops_uncharged_and_resumes_by_re_upload` |
| K5: a resume doesn't clear the mark | `test_resume_after_a_top_up_completes_the_rest_and_leaves_the_done_alone` |
| K6: the fixed credit message wins over the coded one | `test_the_rest_of_the_batch_stops_uncharged_with_its_own_code` |
| K7: the first refusal counts itself as "underway" | `test_a_first_item_refused_before_anything_ran_is_the_plain_refusal` |
| K8: "completed" counts every item | `test_the_rest_of_the_batch_stops_uncharged_with_its_own_code` |

Logs: `logs_5bef042/mutant_K1.log` … `mutant_K8.log`.

## Fix round: v2's pre-read (`70500e7`)
**The gap (v2, before handover; SM: fold it in now).** At `5bef042` only a retry of an INSUFFICIENT_CREDITS_MID_BATCH item cleared `credits_exhausted_at`. So after a top-up:
- (a) retrying another failed item of a stopped batch (a PROVIDER_FAILURE, say) was claimed, then stopped by the stale mark: relabelled a credits failure, with a retry spent;
- (b) `retry-failed` walks `item_index` order, so a lower-index non-MID item was launched before the first MID item's claim cleared the mark, and was stopped the same way. That's always the case under eager dispatch, and in production whenever the worker is quick.

**The fix** (`students/item_retry.py`): any retry of a batch item that **wins** its claim clears the mark. A losing claim (a race, `ItemNotRetryable`) never does. A real shortfall re-marks the batch through `batch_credit_refusal` before any charge. The now-unused `refusal_code` capture is gone.

**Tests** (`AnyRetryInAStoppedBatchResumesIt`; the grading fixture moved into a mixin, `GradingBatchFixture`, so the new class doesn't re-run the old tests):
1. a PROVIDER_FAILURE item retried after a top-up is graded (retry_count 1, mark cleared);
2. retry-failed with a lower-index PROVIDER_FAILURE first grades all three, inline (as a fast worker would);
3. a real shortfall after the clear re-marks the batch and grades nothing: each retried item is refused at the credit check, and `stopped_at_item` is item 1;
4. a losing claim leaves the mark.

| Run | Tree | Result | Log |
|---|---|---|---|
| Dev run on the WIP (no result claimed) | WIP | 21 OK | (not kept) |
| **Reproduce-first:** the 4 new tests with `students/item_retry.py` reverted to `5bef042` | `70500e7` − fix | **3 of 4 fail:** (1), (2) and (3). (4) passes there as expected: it guards the mutant K10, not the old defect | `logs_70500e7/repro_fix_item_retry_5bef042.log` |
| **The changed modules + all 8 guards** (the same list as above) | `70500e7` | **Ran 188, OK** (184 + the 4 new tests) | `logs_70500e7/changed_modules_and_guards.log` |
| **Mutants K1–K11** on `test_epic_a_s7c_mut` (dropped after), against `tests_credits_mid_batch` + `tests_item_retry` | `70500e7` | **11/11 KILLED** (below) | `logs_70500e7/mutant_K1.log` … `mutant_K11.log` |
| **ONE regression for the fix:** `students` + `users`, `-v 2`, stamped; `pg_stat_activity` snapshot first; 6G, `timeout -k 60 1800` | `70500e7` | **Ran 1033, OK (skipped=5)**, 00:01:25–00:03:22 (104 s of tests) | `logs_70500e7/regression_students_users_70500e7.log.gz`; snapshot `logs_70500e7/regression_pg_stat_activity_before_70500e7.txt` |

| New mutant | Killed by |
|---|---|
| K9: only a MID_BATCH retry clears the mark (the pre-fix rule restored) | tests (1), (2), (3) |
| K10: a losing claim clears the mark too | test (4) only |
| K11: the mark is cleared after the launch instead of before | tests (1), (2), (3) and the original resume test |

K1–K8 were all killed again at `70500e7`. Under K1 and K2 the new class's setUp assertion fails too, so they're killed more times; the per-mutant logs show which tests.

**Regression scope for the fix (0b):** `students` + `users`. The retry routes calling `retry_item`/`retry_failed` live in `users/views.py`; `assignments` doesn't import the changed module, and its model-change coverage is the `5bef042` run above (the fix changes no model).
