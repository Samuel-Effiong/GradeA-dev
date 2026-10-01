# Bundle 4 → phase2/epic-a merge-down: gates (ed)

Merge bfcf6e1 (0b; beta 67a0681 into epic ba165b4). The resolution and its follow-ups are in `RESOLUTION_ed.md`. All runs had 0b's grant, used 6G (step 3: 12G with flock), the rule-16 prefix, `timeout -k 60 1800`, and `settings_worktree`. Mutants ran on their own DB (`test_epic_a_merge_down_b4_mut`), which was dropped after every run.

| Round | Tip | Step | Result | Log |
|---|---|---|---|---|
| 1 | bfcf6e1 | 1. modules + all 11 guards | **FAILED** 304 tests, failures=3 errors=1, all in beta's `students.tests_h38_tasks_namespace`. Cause: S7b's run-time raise ahead of beta's soft return, and S6d's rubric gate against a fixture with no marking guide. Mutants not run | `run1_failed_modules_and_guards.txt` (trimmed) |
| 2 | 3603961 (f4f681e, d9c3f13) | 1. | **FAILED** 304 tests, failures=1: beta's static sweep wants `teacher_may_reach` in grade_engine_async, and the dropped block had held it. Mutants not run | `run2_failed_modules_and_guards.txt` (trimmed) |
| 3 | 6faf303 (6ef6004: the combined check) | 1. | **304 tests OK** | `r3_modules_and_guards.txt` (trimmed) |
| 3 | 6faf303 | 2. M1–M12 | **10/12 killed**. Survivors: M8 (retry_item's session gate; item_retry's own 404 masked it) and M12 (the reachable_courses half; S7b's non-teacher refusal had no test) | `r3_mutants_log.txt`, `r3_mutants_results.json` |
| 4 | 581fa46 (test-only: the two isolating tests, SM OK) | the touched modules, then M8 and M12 alone (rule 15.4) | **22 OK**. M8 killed by `test_retry_item_is_not_found_for_a_removed_teacher`; M12 killed by `test_a_school_admin_of_the_same_school_cannot_run_grading` | `r4_touched_modules.txt`, `r4_M8_*`, `r4_M12_*` |
| — | this docs tip | 3. ONE combined students/classrooms/users/assignments regression, 12G (assignments added because production changed in `assignments/tasks.py`) | see below | `regression_combined.txt` |

All 12 mutants are now killed. M1–M7 are S7b's (`docs/evidence/epic-a-s7b/h38/run_gates.py.txt`), re-run on the merged tree. M8/M9 are the new session gates on the retry routes. M10 is the ids-only refusal log line. M11/M12 are the two halves of the combined check; M11 is killed only by beta's sweep, by design. The harness is `md_b4_mutants.py.txt`; the scripts are `run_gate_r3.sh.txt` and `run_r4.sh.txt`. Full copies of the trimmed logs are in `~/Documents/Projects/GAP-evidence-logs/`, chmod 600.
