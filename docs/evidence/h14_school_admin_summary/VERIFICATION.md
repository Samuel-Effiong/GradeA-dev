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
