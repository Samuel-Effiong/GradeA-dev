# H-214: the direct add answers a refusal with 400, not 500 (expectations written BEFORE any run)

Written by ed (Security Engineer), 2026-10-09 18:4x WAT. Nothing has been run. Base: beta b4fda750.

## The fault (by reading)
`CourseViewSet.direct_add_student` (classrooms/views.py) calls `serializer.save()` inside a `try` whose last clause is `except Exception` -> 500 "Unable to add student to course". `DirectAddStudentSerializer.create` refuses by raising DRF's `serializers.ValidationError` ("Student is already enrolled in this course.", the school gate via `check_existing_account_may_join`, "Course context is required."). DRF's ValidationError is not a Django `ValidationError`, so it skipped the second clause and landed in the blanket one: a teacher's mistake answered 500.
Siblings: `students` (single add) and `remove_student` have the same blanket clause, but their `try` wraps only `services.*` calls, and `classrooms/services/*.py` imports nothing from rest_framework (grep: no hit), so no DRF exception can reach them. `bulk_add_students` has no blanket clause. Only the direct add is affected.

## Change
One clause `except APIException: raise` before `except Exception` (import added). Nothing else.

## Tests (commit 5d20bd32, before the fix) and written reasons
| Test | Base (unfixed views.py) | Written reason fragment |
|---|---|---|
| tests_h214_direct_add_answers::test_an_already_enrolled_student_is_a_400_with_the_reason | RED | `500 != 400` |
| ::test_a_student_of_another_school_is_a_400_and_stays_out | RED | `500 != 400` |
| ::test_a_drf_exception_that_is_not_a_validation_error_also_passes_through | RED | `500 != 403` |
| ::test_an_ordinary_student_is_still_added | green | control |
| ::test_an_unexpected_failure_is_still_a_500_with_the_fixed_message | green | control |
| tests_h99_placeholder_email::test_when_every_attempt_collides_nothing_is_attached (`>= 400` tightened to `== 400`, stale comment fixed; Verifier 1's note) | RED | `500 != 400` |
| tests_cross_school_enrollment::test_direct_add_is_rejected (tightened from (400, 500) to 400) | RED | `500 != 400` |
Hand traces: already-enrolled: the typed name "Other Pupil" clashes with no enrolled row, `validate_email` passes a STUDENT, `create` finds the enrollment and raises the DRF ValidationError. Other school: `schools_associated_with` is {other} != {teacher's school} so `EnrollmentError` -> DRF ValidationError.
Old behaviour grepped: no other test expects 500 from the direct add (greps for `Unable to add student`, `direct-add-student` users: h71, h99, tenancy, security_penetration, billing h38 part 2, student_classmates_guard; none asserts 500; the h99 tests assert < 300).

## Expected sets
Step 0 (base): the six reds/greens above; everything else in classrooms.tests_h214_direct_add_answers and classrooms.tests_cross_school_enrollment green.
Mutants (against the fixed file):
- M1 remove the clause: killed by exactly the five reds above (the four plus the h99 all-collide test).
- M2 narrow it to `except ValidationError: raise`: killed by the 403 test only.
- M3 widen it to `except Exception: raise`: killed by the 500 control (the test client re-raises RuntimeError -> ERROR).
Gate list for the grant: classrooms.tests_h214_direct_add_answers, classrooms.tests_cross_school_enrollment, classrooms.tests_h99_placeholder_email, classrooms.tests_h71_student_add_role, classrooms.tests_tenancy_and_roster, classrooms.tests_security_penetration, classrooms.tests_h203_admin_power_routes (after H-203 merges; its `DIRECT_REFUSALS` either-or then tightens to 400 in the same bundle), AutoGrader.tests_student_classmates_guard. Then one classrooms regression.
No payload-shape change (rule 20 not touched: the success answer is the same).

## Base update onto beta fafed57f (after the Phase 2 promotion)

The branch took beta fafed57f by a clean merge (no conflicts). H-203's route test `classrooms/tests_h203_admin_power_routes.py` accepted 400 or 500 for the direct add; it now accepts only 400 (`DIRECT_REFUSALS`), the same row's change. Hand-checked: the refusals on that route are `serializers.ValidationError`, which H-214 lets through as 400. `classrooms.tests_h203_admin_power_routes` is in the gate list. pre-commit over the three touched files: all hooks passed.

## The reason assertion read from its field (Builder 58, 2026-10-14, SM's order; base beta 913f248f via a6's update 38312a61)

`test_an_already_enrolled_student_is_a_400_with_the_reason` searched `str(response.data)` for "already enrolled". That substring also matches the duplicate-name refusal in `DirectAddStudentSerializer.validate` (classrooms/serializers.py ~701: "A student with the exact name ... is already enrolled in this course."), so the test could not tell the two refusals apart. On this path `create()` raises `serializers.ValidationError("Student is already enrolled in this course.")` and DRF answers it as a one-item LIST. The test now asserts `len(response.data) == 1` (non-empty, one sentence) and then `str(response.data[0]) == "Student is already enrolled in this course."`. Nothing else changed.

Written reasons (before any run):
- Base 913f248f (views.py unfixed): the test is still RED at its status line, fragment `500 != 400` (unchanged).
- New mutant M4 (classrooms/serializers.py ~731): the create() sentence replaced by the duplicate-name form `f"A student with the exact name {email!r} is already enrolled in this course."`. Killed by this test ONLY, fragment `!= 'Student is already enrolled in this course.'` inside its own FAIL block. The old substring assertion passed M4. Every other test in the gate list stays green (none reads this sentence: grep `Student is already enrolled` over the tests at the tip).

## Chain on 4a479919 (Builder 58, 2026-10-14, a6's GRANT 11:39; logs gzipped in chain_4a479919/)

Runner: GAP-58-runs/row_chain.py (copy `row_chain.py.gz`, sha256 b999a8fd3cf97f36670b14c3bda434250db0ec0ca756171249a7a6ce86251b5b), config `config.py.txt` (sha256 ed0465e8baa87838e8dd0174787b01fa09beda39ac4f3051b97162b8484ff5aa), both read by the Verifier and a6 before the grant. One systemd-inhibit around the whole chain.

| Stage | Result (copied from chain.status / the logs) |
|---|---|
| (r) the 4 row modules from the tip, on 913f248f | exit=1, "7 failing, as expected", "the red set is the expected one, each for its written reason" |
| (a) 19 labels on the tip | exit=0, "Ran 335 tests in 86.918s", OK |
| (b) baseline | exit=0 |
| M1 clause removed | "7 failing, as expected", fragments found |
| M2 narrowed to ValidationError | "1 failing, as expected", fragment found |
| M3 widened to Exception | "1 failing, as expected", **WRONG REASON** (below) |
| M4 create() sentence swapped | "1 failing, as expected", fragment found |

The chain ended "STOP: (b) 1 mutant(s) not killed as written" at 11:44:47 because of M3.

### M3: a trace fault (SM ruling 2026-10-14: the kill stands, no re-run)

M3 was killed by the expected test (`test_an_unexpected_failure_is_still_a_500_with_the_fixed_message`; Ran line and the named test are in `b_M3.log.gz`), but the written reason was wrong. The trace assumed the test client re-raises the RuntimeError (fragment `RuntimeError: database exploded`). Actual reason, from M3.log lines 961-968: the RuntimeError reaches `custom_exception_handler`'s "Unhandled 500" branch, which answers a bare `Response(status=500)` with no data, so the status assertion passes and `response.data["message"]` raises `TypeError: 'NoneType' object is not subscriptable` (an ERROR, "FAILED (errors=1)"). The same wrong reason was in this file's earlier "M3" line.

Lesson: before writing the reason for an exception that escapes a view, read how the project's exception handler answers it (here: a bare 500, no body); the test client re-raises only what no handler turns into a response.
