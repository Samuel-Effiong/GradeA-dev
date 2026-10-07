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

## Added 2026-10-07 11:38 WAT, still before any run: rule 20 (the cache tests)

New team rule 20 (Senior Manager, 2026-10-07, after batch 11's full run was red on a
cache-contract test that a serializer change of another row broke): a change to what a serializer
or a cached route returns runs the AutoGrader app's cache tests with its gate.

This row changes what the course answer and the session list return to a student. Added to step 1
of `run_h147_gate.sh` (sha256 now starts 0624551ec8df1146): `AutoGrader.tests_cache_bespoke_1114`
(named by the Release Engineer for every such change) and the seven other AutoGrader cache
modules that read a course, session or roster route: `tests_cache_user_fanout`,
`tests_cache_matrix_tenant_isolation`, `tests_cache_collateral_damage`,
`tests_cache_generation_wiring`, `tests_cache_dashboard_wide`, `tests_cache_dashboard_freshness`,
`tests_cache_matrix_concurrency`. `tests_cache_matrix_selftest` and
`tests_cache_invalidation_coverage` were in already. Not added: `tests_cache_commit_race`,
`tests_cache_commit_race_cost`, `tests_cache_matrix_scale`, `tests_cache_matrix_measurement`
(measurements; they come with the batch's full run).

Expected: OK. By reading, where these modules read a course route they do it as a TEACHER, or
compare a student's answer with itself before and after another tenant's change; I found none
that expects a student to see a classmate. I read the places a grep for the course, roster and
session names found, not every line of the eight modules: a red here would be a real finding
about this row, reported as it is.

## Results

### The gate at 787a81fb (0b's GRANT, 2026-10-07 13:09:26 WAT)

787a81fb is this branch's 085ea0ee and 0b's base update onto the pushed batch 11 (beta
d7143538); no file was changed by both sides. One run of `run_h147_gate.sh 787a81fb 1 d7143538`
(script sha256 starts 073865d90e4bca2f: the 0624551e of the section above plus one guard module
0b named, `AutoGrader.tests_migration_safety_check`). 13:09:59 to 13:22:38. It ran beside the
Next-stage Builder's battery; load 9.41 at the start, 14.78 at the start of part 1, 10.73 at its
end, 5.99 at the end. Not stopped, not repeated.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0. Reproduce-first, on the base's production files | Ran 36; 21 red: 9 + 3 + 9, named above | **Ran 36 tests in 30.084s, FAILED (failures=26, errors=1)**: 27 lines, 21 distinct tests, exactly the 21 named (three of them fail once per course route, hence more lines than tests). Source restored. | `prefix_base_production_failing_787a81fb.txt.gz` |
| 1a. makemigrations --check | no changes | no changes | `makemigrations_check_787a81fb.txt` |
| 1. Modules and guards at the tip | OK | **Ran 527 tests in 376.095s, OK** (no skip) | `modules_and_guards_787a81fb.txt.gz` |
| 2. Mutants | 13 KILLED with their named tests | **13 of 13 KILLED**: each exit 1, its own "Ran 36", every expected test among the failed (`expected-but-passed []` thirteen times); SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN empty. Source clean after; the mutants' database dropped. | `mutation_log_787a81fb.txt`, `mutation_results_787a81fb.json`, `mutant_logs_787a81fb/` |

Nothing differed from what was written before the run. The run's console is
`gate_console_787a81fb.txt.gz`.

Rule 20's cache modules were in part 1 and are green: the risk I named in the 11:38 section did
not show.

Rule 19, counted from these records (the part 0 log and `mutation_results_787a81fb.json`):

- New module, 14 tests: 9 red in part 0 and the other 5 under a mutant. All 14 seen red.
- Rewritten exposure module, 15 tests: 9 red in part 0, 3 more under a mutant. **Never seen red,
  and not claimed as evidence: 3**, which hold what was true before this row and is untouched by
  it: `test_query_counts_stay_flat_as_the_roster_grows`,
  `test_school_admin_and_superadmin_cannot_reach_course_payloads`,
  `test_student_keeps_their_own_email`.
- New guard, 7 tests: 3 red in part 0. **Never seen red, and not claimed as evidence: 4**:
  the two self-tests of the scanner (`test_rule_1_the_order_check_fails_when_the_roster_comes_first`,
  `test_rule_1_the_scan_sees_a_site_when_there_is_one`) and the two tests of the actions table
  (`test_rule_2_each_refusal_is_where_the_table_says`, `test_rule_2_every_action_is_classified`).
  No mutant of mine changes a view's permissions or adds an action, so nothing here shows those
  two can fail. I say so and leave it to the verifier whether a probe is wanted.

Still owed as this is committed: the regression (classrooms and assignments), on its own grant.

### The regression at 33a7aace (0b's GRANT, 2026-10-07 14:37:56 WAT)

33a7aace is the gated tip plus the docs-only results commit above it; no code or test differs. One
run of `run_h147_gate.sh 33a7aace 3 d7143538` (script sha256 starts 073865d90e4bca2f): classrooms and assignments, serial, in 0b's quiet window, alone among
this team's runs. 14:38:27 to 14:42:37, exit 0. Not stopped, not repeated.

| Written before | Found | Log |
|---|---|---|
| OK | **Ran 1097 tests in 231.259s, OK (skipped=13)** | `regression_33a7aace.txt.gz`; console `regression_console_33a7aace.txt` |

The skips, in their own words: eight 'load tests are opt-in: set RUN_LOAD_TESTS=1', one 'set RUN_LOAD_TESTS=1 to build the 6,000-student school', four 'Real AI call is opt-in and billed: set RUN_REAL_AI=1'. None is for want of a browser.

One-minute load 5.74 at the start, 3.32 at the end. 0b asked for a start at a one-minute load of 4.0 or lower. I read 3.51 at 14:38:21 and started; six seconds later the console recorded 5.74: a process of the other project on this laptop had begun in the same seconds. The run was left to finish and is green; no test with a clock in it failed.

Written 14:54 WAT. Nothing is owed on this row by me now but the hand-over to Verifier 1.
