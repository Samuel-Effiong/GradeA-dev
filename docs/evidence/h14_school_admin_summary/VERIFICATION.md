# H-14 school-admin summary rebuild — Verification

Branch `task/h14-school-admin-summary` @ 4ad9278, off beta 4b902fc.

## Re-run myself
`dashboard.tests_h14_at_risk_equivalence`: **4/4 passed**. `dashboard` app + the 4 cache-freshness modules: **315 tests, OK (skipped=2)** (small count difference from the doc's 311 — not a failure either way, not investigated further since both are clean).
`02_mutation_battery.log` checked directly: a-d killed (2 failures each), e/f/g survived (OK), all 8 restores hash-matched (`caec4be6ad3eb84d`). Confirmed, not taken from the summary line.

## Equivalence — traced line by line, not just trusted
Walked the rewrite against the pre-fix oracle field by field: the enrollment filter conditions are byte-identical (only the read mechanism changed, `select_related` → `values_list`); `course_ids_by_student` accumulates the same way; the submissions tuple layout (`row[1:]` sliced from a 6-tuple to a 5-tuple) lines up exactly with how `student_submissions`/`graded_scores`/`submitted_count` unpack it downstream — checked the indices, not just the shape.

## Independently verified the survivor characterization (didn't take it on trust)
- **e (ordering)**: read `StudentRiskEvaluator.evaluate` myself — it does `sorted(inputs.graded_scores, key=lambda pair: pair[0])` before using the scores, so the SQL `order_by` really is redundant for everything under test. Confirmed via code, not narrative.
- **f (future assignments)**: did the arithmetic by hand from the fixture (`s_two`: c1 has 4 past-due + 1 no-due = 5 expected, c2 has 3 past-due + 1 future = 3 normally/4 mutated → total 8/9 expected, 3 submitted → 0.375 vs 0.333). Matches the doc's numbers exactly, both still far under the 0.50/0.70 thresholds — genuine gap, not a hidden bug.
- **g**: same shape, no fixture student near a threshold — confirmed no counter-evidence.

## New finding (non-blocking, not in EVIDENCE.md)
The rewrite splits one query (enrollment+student joined) into two phases: scan enrollments/submissions first, THEN `CustomUser.objects.only(...).in_bulk(at_risk_scores.keys())` at the end, followed by a plain dict index `students_by_id[student_id]`. If a `CustomUser` row is hard-deleted between the initial enrollment scan and that final `in_bulk()` call, the enrollment/submission data for that student is already in memory (so they're still evaluated and can still land in `at_risk_scores`), but `in_bulk()` simply omits the now-missing id — so `students_by_id[student_id]` raises an uncaught `KeyError`, crashing the whole rebuild. The pre-fix code had no such window (the student object was already materialized in the same query as the enrollment). Narrow (needs a real account deletion mid-rebuild) and not a security or data-correctness issue, but it's a genuine new failure mode this rewrite introduces that isn't covered by the equivalence fixture (which never deletes anything mid-test). Worth either a `.get(student_id)` + skip-if-missing, or just noting it as an accepted narrow risk.

## Verdict: VERIFIED-WITH-NOTES
Equivalence is real and independently confirmed field-by-field, not just via the test suite. The 3 survived mutants are genuinely pre-existing, characterized gaps, verified by my own arithmetic against the fixture, not taken at face value. One new narrow finding (mid-rebuild deletion race) flagged above, non-blocking.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.

## Re-check of the deletion-race fix (42b64fa): VERIFIED

Scope, as agreed: the fix hunk and its test only.
- `dashboard/services.py`: `students_by_id.get(student_id)` plus `continue` on None. This is the minimal fix, and behaviour is unchanged whenever the student row still exists.
- `test_a_student_deleted_between_the_scan_and_the_name_lookup_is_skipped` puts the race exactly where it bites. It hard-deletes an at-risk student inside the patched `QuerySet.in_bulk`, just before the name lookup. It's non-vacuous: it asserts the deletion actually happened (`deleted == [victim_id]`) and that the output equals the pre-deletion baseline minus the victim, in the same order.
- Re-ran myself: `dashboard.tests_h14_at_risk_equivalence` passes 5/5.
- My own mutant: restoring the plain `students_by_id[student_id]` index makes the new test ERROR with `KeyError: UUID(...)`, and only that test. Restored and sha-verified against 42b64fa; the worktree is clean.
- I didn't re-run the 316-test regression. The change is a 3-line guard that's only reachable when a key is missing, and the 4 existing equivalence tests still pass. I accepted the author's 316 OK (skipped=2).

Verdict: VERIFIED. The e/f/g survived mutants are still documented, pre-existing boundary-coverage gaps in the risk evaluator, not in this fix. They're recorded above and aren't a blocker.

Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.

## Typing fix for the django-stubs mypy step (c138fe8, on e6f31c9): VERIFIED
- The only behavioural-path edit is the sort key, which now reads `at_risk_scores[student.pk]` instead of `student.avg_score`. It's equivalent by construction. `avg_score` was set to `at_risk_scores[student_id]` with `student = students_by_id.get(student_id)`. `in_bulk` keys by pk, so `student.pk == student_id` (same UUID). Only found students are appended, so the lookup can't KeyError. The tuple `(score is None, score or 0.0)` is unchanged. Everything else is annotations, plus one `type: ignore[attr-defined]` on the dynamic `avg_score` attribute.
- Throwaway checkout of c138fe8: `dashboard.tests_h14_at_risk_equivalence` 5/5. My mutant (the sort key drops the None-last component and reverses the order) is KILLED by `test_same_students_scores_and_order_as_the_original` and `test_names_and_the_built_payload_match`. Restore sha-verified.
- Whole-repo mypy: c138fe8 merged into task/beta-batch-1 @53e715e reports **no errors in any h14 file**. On h14's own branch the hook still shows 2 errors, but they're in docs/evidence/authz-oauth-takeover/replay_scripts (from beta), which the batch's d52364c `exclude: ^docs/` removes. They aren't h14's.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
