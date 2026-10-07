# H-153: a teacher renames a student

Author: Security Engineer (ed). Branch `task/h153-teacher-renames-student`, stacked on H-152's
branch (which is on H-148's, which is on H-147's); merges last of the four. Beta line. Verifier:
Verifier 1.

**Everything down to the heading "Results" was written on 2026-10-07 at 13:17 WAT, before any
run.** Nothing in it was observed in a run. **[R]** = read by me in the code.

## The rule and the decisions

Founder's representative, 2026-10-06: a student does not name themselves; only the teacher names
a student. User, 2026-10-07 (through the Senior Manager): either of two teachers who share a
student may rename; a school admin may NOT; a student with no current teacher: the super admin
may; a student who already has no name is named by their teacher this way, with no step where a
student names themselves. Senior Manager, same day: the route is on the account; the answers must
not tell an outsider whether an id is a student of another teacher or school; one exact name per
course in every course the student is in, and a refusal that quotes a name goes only to a teacher
of the course where the clash is; each rename is a log line with ids only, no model and no
migration on this line (the audit event follows on the Phase 2 line).

## What was missing

Nobody could change a student's name once the account existed: the account edit
(`CustomUserSerializer.validate`) refuses any change to a student account's name whoever asks, a
super admin included (H-148 gave that rule its first tests). **[R]** A student added before H-148
by email alone, or converted from an old invitation (H-152), has no name, and a nameless
student's paper cannot be matched when a teacher uploads it.

## What changes

`users/views.py`: one new action on the account view, `PATCH /users/<student id>/student-name`
(`first_name`, `last_name`, optional `middle_name`), and `users/serializers.py`: its body,
`StudentNameSerializer` (each name at least two letters, trimmed; the forms' rule).

In the order the code decides:

1. The account is looked up through the view's own scoped query (`get_object`): **404 for every
   account the caller cannot already read**, existing or not, with the same bytes.
2. The caller must be a super admin or hold the TEACHER role now: otherwise 403. (A school admin
   and the student can read the account; they are refused here.)
3. The account must be a student's: otherwise 400.
4. A teacher must have the student CURRENTLY (enrolled or pending) in a course that teacher can
   reach: otherwise 403. Withdrawn and completed places do not count. A super admin needs none.
5. The name-clash rule, under a lock on the student's enrolment rows: in EVERY course where the
   student has a row, whatever its status, no other row may hold the same exact name
   (`StudentCourse.find_name_conflicts`, the function the enrolment's own check uses). A clash in
   a course the caller has the student in (or any clash, for a super admin) is refused with the
   usual sentence that quotes the name; a clash only elsewhere is refused with "This name cannot
   be used for this student." and neither the name nor the course.
6. The three names are saved with an ordinary save of the account (what follows such a save,
   cache refreshes included, is whatever follows any other; the existing cache tests rename an
   account the same way, and I did not read further), and one line is logged on `users.student_names`: `student_renamed actor=<id> student=<id>
   courses=<ids or "none">`. No line for a refusal.

It does not go through `CustomUserSerializer`, whose rule refuses by whose account it is. That
rule is unchanged: the account edit still refuses a student's name change for everyone.

**Why "every row, whatever its status".** I first wrote a test that let a rename ignore a
WITHDRAWN classmate. Reading the enrolment's own check showed that it counts every row of a
course and runs on every save of an enrolment, so such a rename would leave two rows of one name
in a course and the next save of either (a withdrawal, a reinstatement) would fail. **[R]** The
test was replaced before the change (8c2266b4).

## What the answers tell, and to whom

- 403 is only ever said about an account the caller can already read through the ordinary account
  route: a teacher's own student, past or present; a school admin's school; the caller's own.
- A teacher whose rename clashes in another teacher's course learns that the name cannot be used,
  and not why. That is the cost of not confirming a name in a class the caller does not teach.
- The log line holds ids only: no name, no email address.

## For the frontend

- New: `PATCH /users/<student id>/student-name` with `first_name`, `last_name`, optional
  `middle_name`; 200 answers `id` and the three names. 400 for a missing, blank or one-letter
  name, for a clash, and for an account that is not a student's; 403 and 404 as above.
- The roster page can offer "rename" to a teacher for every student currently in one of their
  courses, and "name this student" for the nameless ones.
- Unchanged: the account edit still refuses a student's name change (400), for everyone.
- I cannot read the frontend.

## What it does NOT do

- No school admin rename (decided). No self-naming step (decided).
- No database record of a rename on this line: a log line. Whether the hosting service keeps
  that log, and for how long, I do not know.
- Stored answer documents are not rewritten: a student's document is rebuilt from the current
  name before release (H-130); a released or staff copy keeps the name it was built with.
- A paper already uploaded and matched stays with its student; a paper bearing the OLD name will
  no longer match after a rename. Not tested here: matching is `students/services.py`'s, and its
  rule is unchanged.
- The one-letter-name question (H-151) applies here too: the same two-letter minimum.
- Nothing was observed on a live or staging service.

## Tests

`users/tests_teacher_renames_student.py`, 26 tests, real tokens, real route. 24 were committed
first, tests only (25624e96, 68a118db, 8c2266b4); **two were added with the change**, because
writing the mutants showed two rules that no test isolated:

- `test_a_school_admin_who_is_still_named_as_a_courses_teacher_may_not`: an account that taught a
  course and was then made a school admin is still that course's teacher on paper; only the role
  check refuses it.
- `test_a_former_teacher_may_not_though_the_student_has_another_teacher_now`: withdrawn from this
  teacher's course, enrolled in another's; only the "the caller's own course" condition refuses.

**The first red proved nothing, and I say so:** when the tests were committed the route did not
exist, so each would have ended in an error at the URL lookup. Every one of the 26 is therefore
named by at least one mutant, and `mutate.py` refuses to load if one is not.

## Written before the runs

Gate script: `~/Documents/Projects/GAP-ed-scripts/run_h153_gate.sh` (sha256 starts
3bc9fb7cdc21f538 when this was written). Its base argument is the tip this branch is stacked on.

**0. Reproduce-first** (the module on the production files as at the base): Ran 26, FAILED, **all
26 red, as errors** (`NoReverseMatch` for `user-student-name`). Not evidence test by test.

**1a.** `makemigrations --check`: no changes.

**1. Modules and guards at the tip**: OK. No count written. Rule 20: a rename changes what cached
account and roster routes return, through the ordinary save of the account;
`AutoGrader.tests_cache_bespoke_1114`, `tests_cache_user_fanout` and
`tests_cache_matrix_tenant_isolation` are in the list. I expect no effect on them: no cached
route's own code is changed.

**2. Mutants**: 24, each KILLED with at least the tests `mutate.py` names (`--check` passes):
six on the rename itself, nine on who may, six on the name clash, three on the log line.

**3. Regression** (users, classrooms, serial; own grant): OK.

Rule 19, as it should stand after these runs: each of the 26 tests failed under at least one
mutant; none counted on the strength of step 0.

## Not done

Nothing run. No frontend read. `origin/main` not read.

## Results

(none yet)
