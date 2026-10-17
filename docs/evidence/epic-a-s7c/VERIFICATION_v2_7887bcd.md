# Verification: Epic A S7c, credits running out mid-batch (1a), including v2's pre-read finding

- **Branch:** task/epic-a-s7c at **7887bcd**:
  - code 3bce7d6 (S7c);
  - 5bef042 (base update onto epic ca35971, with S7b);
  - 70500e7 (the retry fix for v2's pre-read finding);
  - 7887bcd (evidence only).
- **Verifier:** v2 (independent), 2026-10-01.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Base update 5bef042 (S7c × S7b)
- `git show --remerge-diff`: conflicts in 2 files, both resolved correctly.
  - students/exceptions.py keeps both classes (InsufficientCreditsMidBatchError, CourseNotReachableError).
  - students/item_retry.py keeps S7b's final claim (state conditions on the UPDATE's own row, reachability as `pk__in`).
- **Added-line survival** (`vf_merge_survival.py 5bef042`): base 923b2b8, sides 3bce7d6 / ca35971, **0 lines lost**.
- **S7b's probe re-run on the merged tree:** H1, H2, H3, T5 and R1 all behave as at S7b's verification (404, skipped, provider not called, 202 with the same id and reference).

## v2's pre-read finding: fixed (70500e7)
- **Finding:** only a retry of a MID_BATCH item cleared `credits_exhausted_at`, and launches dispatch at once. So after a top-up, a retry of a non-MID item (alone, or earlier in retry-failed's item order) was stopped as MID_BATCH. It was uncharged, but the resume was wasted, it used up a retry, and it was mislabelled.
- **Fix:** any retry that **wins** its claim clears the mark. The clear sits after `if not claimed: raise`, and before the launch. A real shortfall re-marks through `batch_credit_refusal` before any charge.
- **1a's tests:**
  1. a non-MID retry after a top-up;
  2. retry-failed with a lower-index non-MID item first;
  3. a real shortfall re-marks and charges nothing;
  4. a losing claim leaves the mark.
- **Reproduce-first:** (1)–(3) fail on 5bef042's item_retry.
- **Mutants:** 1a's K9 is v2's planned mutant (the old `refusal_code == MID_BATCH` rule restored), killed by (1)–(3). K10 (a losing claim clears) is killed by (4). K11 (clear after the launch) is killed by (1)–(3) + the resume test. v2 read the logs and did not repeat them.

## What was run
Rules 12 and 13 (6G, timeout 1800), 0b's grants, scratch worktree detached at 7887bcd, DB test_vf2_s1. 1a's regressions were not repeated (rule 15): 1692 OK at 5bef042, and students + users 1033 OK at 70500e7.

| Run | Result | Log |
|---|---|---|
| v2 S7c probe (V3, V5, V7, V8) + v2 S7b probe + `tests_credits_mid_batch`, `tests_item_retry`, `tests_item_retry_h38` + epic guards (below) | 143 run, **142 OK**. 1 error: **v2's own probe fixture** (V3 created a second submission for a student who already had one; unique constraint `unique_student_submission_per_assignment`). Not an S7c defect. | runs/s7c_7887bcd.log |
| V3 re-run after the fixture fix (it now grades an existing submission outside the batch), with 0b's grant | **1 OK** | runs/s7c_7887bcd_V3.log |

**Guards run (epic set):**
- AutoGrader.tests_no_wildcard_invalidation
- AutoGrader.tests_cache_invalidation_coverage
- AutoGrader.tests_reason_codes
- AutoGrader.tests_migration_rollback_defaults (migration 0030 is a nullable column)
- audit.tests_route_coverage
- audit.tests_history_guard
- classrooms.tests_teacher_access_sweep
- classrooms.tests_course_roster_scope_sweep

## v2 probe results
- **V3:** a single (non-batch) grade refused for credits, while an item of the batch had SUCCEEDED, is still the plain **INSUFFICIENT_CREDITS** with the fixed wallet message. No session is marked.
- **V5:** item 1 STARTED but not finished, item 2 refused → MID_BATCH, "Credits ran out after **0** of 4 items. The finished items are saved.", and the session is marked. No wallet text.
- **V7:** the mark is per session. Batch A is stopped; an item of batch B (same teacher and assignment) reaches the provider once and succeeds. B is unmarked and A stays marked.
- **V8:** a retry refused before its claim (a SUCCESS item) → 409, and the mark stays. Only a won claim clears it.

**Also confirmed by reading:**
- An H-38-unreachable retry is a 404 before the claim, so it never clears the mark.
- Charging happens inside `grade_engine`, after `ensure_batch_has_credits`, so a stopped item never reaches it (1a's call-count assertions).
- Upload items resume by re-upload (F4); an in-place retry is 409 `replace_file`.

## Notes (none blocks)
1. **"after 0 of N items" (V5).** With parallel workers, a refusal while another item is only STARTED is mid-batch by design, and the message reads "Credits ran out after 0 of 4 items. The finished items are saved." QA may want different wording when `completed` is 0; that's a catalogue text question, not a code defect.
2. **Rule 14:** 1a's new mocks are `side_effect` functions (`grade`, `run_inline`, `extract`) plus `HasCreditBalance.has_permission` with `return_value=True`. No bare MagicMock reaches a response.
3. **Fixture error in v2's own probe**, disclosed above (0b's instruction). It was fixed test-only and re-run under a separate grant.
