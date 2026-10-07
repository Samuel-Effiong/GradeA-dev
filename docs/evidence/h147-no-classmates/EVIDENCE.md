# H-147: a student sees nothing of their classmates

Author: ed (Security Engineer). Branch `task/h147-no-classmates`, base `task/beta-batch-11` at
be05f953. Beta line. Verifier: Verifier 1. Written 2026-10-06 19:33 WAT, **before any run**; a
"Results" section is added after the runs and nothing above it is changed then.

## The rule and the rulings

Founder's representative, 2026-10-06: "Student should see nothing of their classmates."

The Senior Manager's rulings on my proposal (`~/Documents/Projects/GAP-planning/H-147-no-classmates-proposal.md`):
1. `students`, for a student, is the student's own entry only (a list of one).
2. The nested assignments switch to the student's assignment serializer in this row.
   `submission_count` is not sent to a student. The scheduling of a grading run that the teacher's
   list carried is **H-133's rule on a route H-133 did not cover**; it is closed here and tested
   as the student.
3. `student_count` (the class size): first held open, then answered by the founder's
   representative (relayed by the Senior Manager, 2026-10-06 about 19:34): **a student may see
   the size of their class as a bare number ("e.g., 24 students"), and nothing else about
   classmates.** So `student_count` is sent to a student as before. `submission_count` stays out.
4. Sessions: null for the school's id and for the creator's id.
5. The guard as proposed.
Later (19:25): load the viewer's own submissions once in the course view so the query count stays
flat; keep the existing flat-query tests unchanged and add one for the student's course answer.
A classmate's enrolment must still refresh the student's cached course answer (the class size in
it changes): that invalidation is untouched, and the existing tests that pin it
(`classrooms/tests_cache_course_roster_scope.py`, unchanged) are in the run.

## What a student was sent before this change (read in the code, not observed live)

On the course list, course detail and my-courses routes (`CourseSerializer`):
- `students`: every classmate who is not withdrawn (pending ones included), each with id, first
  and last name, active flag, profile image, enrolment status and whether their address is a
  made-up one. Only the address itself was blanked.
- `student_count`: the class size.
- `assignments`: the TEACHER's assignment list serializer, filtered to published work, with
  `submission_count` (how many classmates have handed in), `extraction_confidence`,
  `auto_grade_on_due_date`, and the three fields of a scheduled grading run
  (`scheduled_grading_at`, `grading_task_name`, `is_grading_scheduled`).
On the session list and detail (`SessionSerializer`): the id of the school that owns the session
and the id of the staff account that created it.

## What changes

`classrooms/serializers.py`
- `CourseSerializer.get_students`: for a student, returns `_own_entry` first: the student's own
  entry, built from the one enrolment that is theirs (an allow-list, not "the roster with the
  others taken out"). The entry keeps the shape it had. The older blanking of classmates'
  addresses is gone with the classmates.
- `CourseSerializer.get_student_count`: not changed, but for a comment: the class size is sent to
  a student, by the founder's representative's answer.
- `CourseSerializer.get_assignments`: for a student, `AssignmentListStudentSerializer`, the shape
  the assignment list route already gives a student (id, title, course, course_title, topic,
  due_date, status, score, total_points, grade_letter, remaining_attempts). Drafts stay absent.
- `SessionSerializer.to_representation`: for a student, `school` and `created_by` are null.

`classrooms/views.py`, `CourseViewSet.get_queryset`: for a student, the submissions loaded with
the assignments are the student's own, whole rows, as `viewer_submissions`, in place of the
id-only rows of everyone that the teacher's count needs. One query either way.

`assignments/serializers.py`, `AssignmentListStudentSerializer._get_submission`: when the caller
loaded `viewer_submissions`, reads the requester's own row from it; otherwise the query it always
made. On the assignment list route nothing is loaded and nothing changes.

## What a student page will notice (for the frontend; I cannot read the frontend)

- A course's `students` holds the student's own entry only.
- A course's `student_count` is unchanged (the class size).
- A course's `assignments[]` has the student's shape. Gone: `instructions`, `question_count`,
  `assignment_type`, `created_at`, `auto_grade_on_due_date`, `extraction_confidence`,
  `submission_count`, `scheduled_grading_at`, `grading_task_name`, `is_grading_scheduled`. New:
  `course_title`, `score`, `grade_letter`, `remaining_attempts`. **`status` changes meaning:** it
  was the assignment's own status (always PUBLISHED for what a student is shown); it is now the
  student's state on it (NOT SUBMITTED, SUBMITTED, GRADED, OVERDUE). This is the change a page is
  most likely to notice.
- A session's `school` and `created_by` are null.

## What it does NOT do

- It does not hide the class size: allowed. A student can therefore still tell THAT someone
  joined or left the class (the number moves), not who.
- `remaining_attempts` in the nested view is computed by this serializer from the attempt count
  alone (3 minus attempts), not through H-133's function, so it does not move at grading. It is
  the same number the assignment list route already shows a student.
- `score` and `grade_letter` in the nested view are shown only once released (the serializer's
  existing rule). `status` shows GRADED only once released.
- The student's cached course answers are still keyed on the course's roster generation, so a
  classmate's enrolment refreshes them: needed, because the class size in them changes.
- The view still loads the whole roster from the database for a student (the enrolments
  prefetch); the serializer does not send it. Narrowing that load would be a second defence with
  nothing to tell it apart from the first in a route test, so it is left out.
- Other routes: my proposal lists what I read (own enrolments, own submissions, assignments, the
  student dashboard, task status) and found nothing about another student there. They are not
  changed and not re-tested here.
- Cached course and session answers outlive the deployment for up to five minutes.
- Nothing was observed on a live or staging service.
- Hand-built rosters are not seen by the guard (its docstring lists what it does not show).

## Tests

Red tests first, their own commit: 96dc7cd2 (`classrooms/tests_student_sees_no_classmates.py`,
nine tests; one of them, `test_the_class_size_is_not_sent_to_a_student`, held the holding answer
and is replaced in the fix commit by `test_the_class_size_is_sent_as_a_bare_number`, after the
founder's representative's answer). The fix commit adds five more to that module (the query count; four on whose
submission is read), the guard (`AutoGrader/tests_student_classmates_guard.py`, seven tests), and
rewrites older tests that held the older rule:
- `classrooms/tests_course_payload_student_exposure.py`: the shared check now requires the roster
  to be the viewer's own entry (it required classmates present with blank addresses); the nested
  keys are the student's; the two-course test no longer expects the classmates' ids. `test_payload_shape_is_unchanged_for_students` is renamed
  `test_payload_shape_for_students`. The flat-query test is untouched.
- `classrooms/tests_cache_course_roster_scope.py` is NOT changed: a classmate's three course
  answers must still change after an enrolment or a removal (the class size), and be fresh.

**The guard is a new repository-wide module: `AutoGrader.tests_student_classmates_guard`.** It
joins the guard list of every later grant.

## Written before the runs

**Reproduce-first** (the new module, the guard and the rewritten module on the three
production files as at the base): red, and exactly these.
- New module, 9 of 14 red: `test_the_roster_is_the_students_own_entry_on_every_course_route`,
  `test_the_class_size_is_sent_as_a_bare_number` (red for its second half: the number beside a
  roster of one),
  `test_a_classmate_of_any_enrolment_status_is_absent`,
  `test_the_nested_assignments_are_the_students_own_view`,
  `test_the_students_own_status_on_an_assignment_is_shown`, `test_a_student_is_sent_neither_id`,
  `test_the_students_own_paper_shows_as_submitted`,
  `test_a_classmates_paper_changes_nothing_the_student_reads`,
  `test_the_view_loads_only_the_viewers_own_submissions` (an error: the attribute is not there).
  Green there: the three teacher tests, the query-count test (the teacher's list was already
  flat) and `test_the_serializer_reads_only_the_requesters_own_of_what_was_loaded` (the old
  serializer asks the database and gets the right answer).
- Guard, 3 of 7 red: `test_rule_1_every_roster_site_is_classified` (its check that the scan sees
  `_own_entry`), `test_rule_1_no_classified_site_has_gone`,
  `test_rule_1_the_course_answer_serves_the_student_first`.
- `tests_course_payload_student_exposure`, 9 of 15 red: the three "hides drafts" tests,
  `test_unpublished_assignment_is_hidden_from_students_too`, `test_payload_shape_for_students`,
  `test_each_course_carries_its_own_filtered_data`, and the three of
  `CachedCoursePayloadIsPerRole`. `test_withdrawn_classmate_is_still_excluded` is green there:
  it holds what was already true.
Ran 36; 21 red in all (9 + 3 + 9).

**Modules and guards at the tip:** OK.

**Mutants: 13, each KILLED with the tests named in `mutate.py`.** In words: the whole roster
given (R1); the own entry made of every enrolment (R2); no class size sent (R3); the teacher
answered as a student (R4); the teacher's assignment list given (A1); the view not loading the
viewer's submissions (A2); the serializer not using them (A3); the view loading everyone's (A4);
the serializer reading any loaded row (A5); the loaded row never found (A6); the school's id sent
(S1); the creator's id sent (S2); the teacher losing the ids (S3).

**Rule 19, as planned:** of the new module's 14 tests, 9 are red in the reproduce-first step; the
other 5 must each fail under a mutant: the three teacher tests under R4 and S3, the query-count test under A2 and A3, the serializer test under A5 and A6. Of the guard's 7, 3 are red in the
reproduce-first step; the two made-up-file tests and the two rule-2 tests check the guard's own
machinery and the table, are not expected red in any run here, and are not claimed as evidence
that the change works.

**Regression (one, on a quiet machine): classrooms and assignments.** Expected green. Read by me
for expectations that depend on what a student is sent in a course: the two rewritten modules,
`classrooms/tests_query_budget.py` (its roster assertions are the teacher's), and the labels of
the cache matrices' reads. NOT read line by line: the other modules that call the course or
session routes (about thirty). A red there is a difference from this text and is reported, not
re-run.

## Not done

- No run of any kind yet.
- The frontend note above is a draft for the Senior Manager, not sent to anyone.
