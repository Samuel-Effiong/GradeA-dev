# H-230: my-students' grade and at_risk leave out withdrawn courses

Found by the Verifier by reading the user's commits fafed57f..913f248f (M1, record GAP-1a-records/user-commits-fafed57f-913f248f-review-eb.md); the SM made it a row for the small batch after batch 18. Branch task/h230-my-students-withdrawn from beta 8a64c35b.

## The fault (read on 8a64c35b)
`StudentListSerializer` (students/serializers.py) builds `grade` and `at_risk` from every enrolment in the my-students prefetch (classrooms/views.py, filtered only by `teacher_course_access_q`), WITHDRAWN ones included: the grade's and the risk's submissions (`_teacher_course_ids` -> `_cross_course_submissions`) and the risk's expected count. The row's student set already excludes a student whose every enrolment is withdrawn; the per-row figures did not. A student who withdrew from course X and stays in course Y counted X's assignments that came due afterwards as missing work and X's old grades in the grade, so my-students could call them at risk while all-students (`.active()`, dashboard/views.py) did not.

## Change
- e4d43914 tests only: classrooms.tests_h230_my_students_withdrawn. W1 X's due, unsubmitted work is not missing work (all-students part first, then my-students at_risk False); W2 the grade leaves out X's 30% (90.00); W3 min_grade=80 keeps the student. Controls: W4 enrolled_courses still lists X (older behaviour, unchanged); W5 a COMPLETED course still counts (60.00).
- 704029be fix: `StudentListSerializer._counted_enrollments` = the enrolments except WITHDRAWN, as `.active()` counts them; `_teacher_course_ids` and the risk's expected count read it. The prefetch, `enrolled_courses` and `teacher` are unchanged; the view's min_grade, max_grade, at_risk and ordering read the same memoised figures. No migration.

## Written reasons and expected sets
GAP-58-runs/h230/config.py (copied here as `chain_704029be/config.py.txt`): (r) on 8a64c35b = {W1 "True is not false", W2 "Decimal('60.00') != Decimal('90.00')", W3 "Items in the second set but not the first:"}; mutants N1 course ids from every enrolment {W2, W3}, N2 expected count from every enrolment {W1}, N3 ENROLLED only {W5 "Decimal('90.00') != Decimal('60.00')"}, run on the new module and the two my-students filter modules (ENROLLED-only fixtures).

## The chain on 704029be (a6's GRANT 14:37 WAT; logs gzipped in chain_704029be/)

Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e; config `config.py.txt` sha256 910e5bce52be7f9b56fe77475cd88ea247f106b4321c1bd5959b42ccaf9ee5c7 (Verifier pre-read: clear). One systemd-inhibit around the whole chain. Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) the h230 module from the tip, on 8a64c35b | "Ran 5 tests in 0.739s", FAILED (failures=3): W1, W2, W3; "3 failing, as expected"; "the red set is the expected one, each for its written reason" |
| (a) 26 labels | "Ran 510 tests in 144.641s", OK |
| (b) baseline (3 modules) | "Ran 23 tests in 1.759s", OK |
| N1 course ids from every enrolment | FAILED (failures=2): W2, W3; "2 failing, as expected" |
| N2 expected count from every enrolment | FAILED (failures=1): W1; "1 failing, as expected" |
| N3 ENROLLED only | FAILED (failures=1): W5; "1 failing, as expected" |

"(b) every mutant killed, each failing set and reason as written"; "h230 CHAIN END: green as written" at 14:42:44. Rule 24: the row's regression is its batch's full run.
