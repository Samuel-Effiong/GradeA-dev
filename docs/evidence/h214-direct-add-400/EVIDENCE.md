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
