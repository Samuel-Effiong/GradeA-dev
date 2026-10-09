# Score-printing stack: the gates of every row, and the stops on the way

Written 2026-10-09 from the files in this folder (raw, gzipped without change; scripts are stored as `.txt`). Rows and tips gated: bottom `aea4854e` (H-139/H-140/H-142 stored-document score printing), H-144 `81cd0e08`, H-145 `76b69102`, H-146 `c8ec622d`. Every run was on the Release Engineer's explicit grant, one outer `systemd-inhibit`, own database, 6G cap, output to a file, rules 12/13/17/18/20/22.

| Row | Tip | (r) red | (a) modules | (b) mutants | (c) regression |
|---|---|---|---|---|---|
| bottom | `aea4854e` | as written | 722 tests OK | 10/10 killed, restore verified; **M10 failed 2 tests, I had written 1** (below) | 3 apps (AutoGrader, students, assignments), 18:16:31 to 18:22:07 Oct 8: Ran 1820 OK, skipped 16 |
| H-144 | `81cd0e08` | as written | 734 OK | 9/9 killed, sets as written | by the stack run at the top |
| H-145 | `76b69102` | 8 red, each for its written reason | 759 OK | 12/12 killed, sets as written | by the stack run at the top |
| H-146 | `c8ec622d` | 7 red, each for its written reason | 818 OK | 3/3 killed (V1 6, V2 7, V3 2 failing), sets as written | 18:38:07 to 18:45:09 Oct 8: Ran 1858 OK, skipped 16 (the stack's one regression, three apps, rule 20 cache tests in it) |

## Stops, each with its one-sentence cause (all mine, none in the shipped code)
- Bottom, chain on `330f3efd` (`earlier_stops/chain_330f3efd/`, regression log in `earlier_stops/`): the chain itself was green; its regression (c) failed one test, `students.tests.StudentSubmissionGradeUpdateTest.test_teacher_can_update_grade`, because the changed route's existing test module was not in my gate list (the route-change rule of 2026-10-07 was added from this).
- Bottom, chain on `de6296de` (`earlier_stops/chain_de6296de/`): stopped at (a) on `students.tests_manual_grade_unreadable_answers...test_the_fault_is_logged_with_the_id_and_the_type_and_nothing_else`: my test asserted a student's name was not in a log line while the name was empty (a "not in" against an empty value, the same slip as in H-144 and H-165); the fix put a non-empty proof before every "not in" and a table of every "not in" assertion went to the Senior Manager.
- H-145, run 1 (`h145_76b69102/first_run_ffd1f6c1/`): the route test queued two tasks with one fake celery id; it failed in set-up at (r) and at (a) and had never reached its assertion.
- H-145, run 2 (`second_stop_76b69102/`): stopped at (r): my written red set still listed a test that has been a control since `c7d28c13` (it passes on the old code), found because (r) now takes the tip's module.
- H-145, run 3: green. The lesson, now rule 22: a red matched by test NAME is not a red for its own reason; (r) looks for a written fragment of the failure inside the test's own block.

## The corrected set for mutant M10 (made after the regression, its own commit)
See `CORRECTED_SET_M10_made_after_the_run.md`. The written file and the run's raw results are unchanged; Verifier 2 may ask for M10 alone to be re-run.

## What stays unsaid by these runs
- No browser, frontend or real model was involved.
- A test that is a control (green at (r) on purpose) is named as such in each row's `expected_kills.py` (stored here as `.txt`).
