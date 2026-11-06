# Epic E1b evidence: a lighter grading hot path, no number moved

Branch `task/epic-e1b`, base staging **8bebace57** (refresh 19, E1a).
Plan: docs/phase2/architecture/09_epic_e1_implementation_plan.md
(task/plan-e1-c1 7c7e5c028), §2 "E1b", §0.4, §0.5, §3.4, §5.1, §5.2.
Not pushed. No migration. No new user-facing wording.

## Result in one paragraph

H-5 is re-applied by hand (not merged: the old branch is 2,500+ commits
behind and carries trailers): a `StudentCourse` save that writes only
hot-path fields validates only those fields. The final-grade receiver now
skips saves that name no field the grade reads. The lock comment says what
Grade all really does. Measured on a Grade all over 30 submissions:
recalculations 90 -> 30, queries 914 -> 474, time inside the enrollment
lock 853 ms -> 209 ms; every stored grade still equals
`grade_service.course_grade`.

## Doctrine, step by step

| Step | Evidence | Result |
|---|---|---|
| Expectations committed first | d1263cda7; EXPECTATIONS.md | written on E1a, rebased onto 8bebace57 before any run |
| Red | 26810785f; red-run.log | exactly the 6 expected reds on the base |
| Expectation correction | 932a84dc2; lockcost-corrected-red-on-base.log | see deviation 1; re-proved red on the base by a saved-copy swap |
| H-5 | a024f0dec; gates.txt (02b) | 104 tests, only the 4 skip tests red; save queries 8 -> 3, lock statements 10 -> 5 |
| Grade-field skip | f2e18e7d8; gates.txt (03) | 104 tests OK |
| Lock comment (§0.5) | fb8adf068 | comment only |
| Mutations | mutants.txt, mutants_e1b.py.txt | 8/8 killed (M9, M9b-d, M10-M12, X5), each restored and checked by sha256 |
| Measurements (Gate 6) | measurements.txt, e1b_measure.py.txt | below |
| Near run | near-run.txt | classrooms students dashboard assignments, --parallel 4, at fb8adf068: **Ran 3013, OK (skipped=63)**; the 47 extra skips are Chromium tests (finding 7) |

Gate at every commit: the E1b tests plus the whole E1a gate (parity,
snapshots, guard, query budget, parity report). The query budget did not
move.

## Measurements (same machine, base files swapped in from 8bebace57 and restored from saved copies, sha256 checked)

| Figure | Before (8bebace57) | After (fb8adf068) |
|---|---|---|
| Grade all, 30 submissions (90 saves: scheduling clear, engine save, label) | 90 recalculations, 914 queries, 852.6 ms in the lock, 1410.5 ms total | 30 recalculations, 474 queries, 208.6 ms in the lock, 551.7 ms total |
| Queries in the `final_grade` save (H5_MEASURE) | 8 | 3 |
| Statements inside the lock for a changed grade (E1B_MEASURE) | 10 | 5 |
| Lock-hold, concurrent workers (H5_MEASURE, p50 / p95) | 26.5 / 27.6 ms | 5.7 / 7.8 ms |

Timings are from a shared developer machine (load ~5); the counts are exact.

## Mutations (mutants.txt)

| Id | Planted | Killed by (among others) |
|---|---|---|
| M9 | hot-path set gains `enrollment_status` | test_status_update_keeps_the_conflict_check |
| M9b | subset check becomes intersection | test_mixed_update_fields_keep_the_conflict_check |
| M9c | written fields not validated | test_an_out_of_range_final_grade_is_still_rejected |
| M9d | else branch skips full_clean | 6 tests: every name-rule entry point, and the mixed-field save |
| M10 | `score` dropped from the grade inputs | test_each_grade_input_field_recalculates, test_the_grade_still_follows_a_score_change |
| M11 | skip applied when `update_fields` is None | test_a_save_without_update_fields_recalculates, convergence, lock-hold |
| M12 | skip applied in post_delete | test_a_delete_recalculates |
| X5 | `assignment_id`/`student_id` not grade inputs | test_a_grade_input_field_named_by_its_column_recalculates |

## Deviations and findings for the SM

1. **Lock-cost expectation corrected (932a84dc2).** The first expectation
   (at most 3 statements in the lock) was too strict: the enrollment cache
   receiver (`clear_student_course_cache`) loads the course and its teacher
   on every enrollment save, inside the lock, and E1b does not touch it. The
   test now pins "no validation inside the lock" (at most 5 statements, no
   existence or name lookup). Re-proved red on the base before H-5.
   Possible later saving: preload course and teacher in the receiver.
2. **Publishing never reaches the signal.** Single publish and Publish All
   write `is_published` through `history.record_bulk` (a queryset UPDATE),
   so no post_save fires. Plan §6 Gate 6's "Publish All takes no lock after
   the skip" was already true before E1b.
3. **What the skip really saves**: the scheduling clear, the label-only
   save after grading, and the held-regrade save. In Grade all that is two
   of every three submission saves (see the measurement).
4. **Column names.** Django passes `update_fields=["assignment_id"]` to the
   receiver as written; plan §3.4's set would have skipped it. The skip
   lists `assignment_id` and `student_id` too (X5 pins it).
5. The interrupted first H-5 gate run left a partial log
   (02-h5-gate.log); the gate was re-run whole as 02b.
6. Unchanged and for E2 (§3.4): an assignment's `total_points` change
   recalculates nothing.
7. **Headless Chromium is gone from this machine.** `~/.cache/ms-playwright`
   no longer exists, so the PDF/Chromium tests skip (63 skips, against 16
   in the E1a near run this morning). No skip marker changed between
   1e28a9952 and HEAD; the probe (near-run.txt) shows the skip reasons. Not
   reinstalled by me (machine-wide). Those tests do not touch the grade
   path, but a run with Chromium restored would close it.

Full logs: ~/Documents/Projects/GAP-a0-runs/e1b/ (red-run.log, 02b-h5-gate,
03-skip-gate, mutants/, 05/06 measurements, 07 near run).
