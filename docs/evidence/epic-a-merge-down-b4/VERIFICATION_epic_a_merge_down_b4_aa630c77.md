# Verification: bundle 4 → Epic A merge-down (0b merge, ed follow-ups)

- **Branch:** task/epic-a-merge-down-b4 at **aa630c77**. Production is identical to 581fa46. 05dedbbf..aa630c77 is evidence only.
- **Commits:**
  - bfcf6e1: 0b's merge;
  - f4f681e + 6ef6004: grade_engine_async, ed;
  - d9c3f13 and 581fa46: test-only, ed.
- **Verifier:** v2 (independent), 2026-10-01. No v2 test run (rule 15).
- **Verdict:** **VERIFIED**

## The merge, bfcf6e1 (static)
- **Remerge-diff:**
  - billing/services.py: one merged comment, no code change.
  - users/views.py: both sides' imports kept.
- **Added-line survival:** 21 lines are reported lost, and every one was replaced on purpose:
  - beta's "202, retried nothing" assertions became 404 assertions;
  - the item-level reachability checks moved below the new session gate, into the retry service.
- **The new session gate (`teacher_may_reach`):** its 404 text equals get_object_or_404's, so it is no existence oracle. The in-claim race test patches both gates with lambdas. Rule 14 holds.

## ed's follow-ups (ed authored them; v2 verifies)
- **f4f681e + 6ef6004 (assignments/tasks.py):**
  - grade_engine_async has ONE check: `not teacher_may_reach(...) or not reachable_courses(...).exists()`, so it fails closed. It logs the ids-only "Grading refused (H-38)" line, then raises S7b's CourseNotReachableError, which the existing handler (tasks.py:496) marks as the coded failure.
  - Beta's soft-return block is dropped. This is an intentional divergence (SM ruling), recorded in the code comment and RESOLUTION_ed.md.
  - teacher_may_reach and COURSE_NOT_FOUND are still used by the batch paths, so nothing is orphaned.
- **d9c3f13:**
  - Beta's queued-grading test now asserts the coded outcome: a CourseNotReachableError whose text is COURSE_NOT_FOUND, task.error, no grade_engine call and an unchanged ledger. That is stronger than the old dict check.
  - The fixture gains a one-level marking guide so S6d's rubric gate lets the beat dispatch. This is an adaptation, not a weakening.
  - The `patch(... grade_engine)` mock is only checked with assert_not_called; rule 14 holds.
- **581fa46:**
  - M8: retry_item's 404 must equal a missing session's message.
  - M12: a same-school school admin is refused, with an ids-only log and no emails.
  - Test-only; grade_engine has a side_effect function (rule 14).

## Gates (ed's, read, not repeated)
| Round | Result | Log |
|---|---|---|
| r1 modules + guards | FAILED (3F, 1E): the behaviour clash, disclosed | run1_failed_modules_and_guards.txt |
| r2 | FAILED (1F): beta's static sweep, disclosed | run2_failed_modules_and_guards.txt |
| r3 modules + guards | **304 OK** | r3_modules_and_guards.txt |
| r3 mutants M1–M12 | 10/12; M8 and M12 survived | r3_mutants_results.json |
| r4 touched modules + M8, M12 | **22 OK**; M8 and M12 killed | r4_touched_modules.txt, r4_M8/M12_* |
| Step 3: ONE combined regression (students, classrooms, users, assignments), 12G + flock, at 05dedbbf | **2095 OK** (skipped=18), 314.6 s | regression_combined.txt |

**S7b's mutants, the question v2 left open on bfcf6e1:**
- **M1** (the request-time reachability check removed) is killed at the **service** level by `test_the_service_refuses_the_item_by_its_own_course` and `test_an_item_with_no_assignment_is_judged_by_its_batchs_course`. The route gate no longer hides it.
- M2–M7 are killed as at S7b.
- **M10** (the ids-only log line dropped) is killed by `test_routes_the_refused_run_and_the_beat_log_ids_only`.
- **M11** (the teacher_may_reach half dropped) is killed by the dispatch sweep. **M12** (the reachable_courses half dropped) is killed by the school-admin test.

## Notes (none blocks)
1. **Future merge-downs** must keep the dropped soft-return as a divergence. RESOLUTION_ed.md says so, and 0b's merge checklist should carry it.
2. **The regression's app set** is the four apps the follow-ups touch. billing's side of the merge is a comment only.
