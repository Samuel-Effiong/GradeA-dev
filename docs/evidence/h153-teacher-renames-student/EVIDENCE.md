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

### The first gate, at 17febb89: RED. A fault in the route (2026-10-07)

17febb89 is this branch's 24ef97d8 and the Release Engineer's base update onto H-152's 1a2995f2.
One run of `run_h153_gate.sh 17febb89 1 1a2995f2` (script sha256 starts 68cd1df73b0a89aa) on
its GRANT of 15:45, 15:46:22 to 15:49:12. One-minute load 2.60 at the start, 3.06 at the end.
The script stopped itself after part 1; the mutants never started. Not repeated.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0. Reproduce-first, on the base's production files | Ran 26, all 26 red, as errors (`NoReverseMatch`) | **Ran 26 tests in 1.795s, FAILED (errors=32)**: 32 lines (sub-tests counted singly), 26 distinct tests, every one `NoReverseMatch`; "step 0 is as written" | `prefix_base_production_failing_RED_GATE_17febb89.txt.gz` |
| 1a. makemigrations --check | no changes | no changes | `makemigrations_check_RED_GATE_17febb89.txt` |
| 1. Modules and guards at the tip | **OK** | **Ran 522 tests in 133.539s, FAILED (failures=15)** | `modules_and_guards_RED_GATE_17febb89.txt.gz` |
| 2. Mutants | 24 KILLED | not run | |

The console is `gate_console_RED_GATE_17febb89.txt.gz`. The expectation above ("Modules and
guards at the tip: OK") is left as it was written; it was wrong.

**What failed.** 15 of the 26 tests of `users.tests_teacher_renames_student`, and no test of any
other module: 11 with "500 != 200" and 4 with "500 != 400". The 11 that passed are refusals
decided before the line at fault.

**Why.** From the raw log: `NotSupportedError: FOR UPDATE cannot be applied to the nullable side
of an outer join`. The route locked the student's enrolment rows with `select_for_update()` and,
in the same query, joined each row's course and the course's session
(`select_related("course", "course__session")`). A course's session may be empty, so that join
is an outer join, and PostgreSQL refuses a lock through it. Every rename that passed the
permission checks answered 500. The session was never used there. The repository has a note on
this very trap (`docs/evidence/h38_part2/select_for_update_outer_join_regression.md`); I did not
apply it. It is my fault in the production code, and the committed tests found it the first
time they ran: nothing had run this route before, as "The first red proved nothing" above says.

**The courses of those 15 tests all have a session** (`RenameBase.course_of` makes one each).
They failed all the same: PostgreSQL refuses the statement for the kind of join, whatever the
rows hold.

Reported to the Release Engineer and the Senior Manager at once; nothing re-run. The Senior
Manager ruled (15:5x): the one-statement fix; the 15 tests red by name in this run are the red
proof; one more test for a course with no session; a mutant that puts the fault back; a fresh
gate. And: every other `select_for_update` in the changes of H-148, H-152 and H-153 to be read
for the same trap.

### The fix of the lock (written 15:53 WAT, before any run of it)

- **Tests first, c2c39b72 (tests only):** class `ACourseWithNoSessionTest`, two tests: the
  teacher of a course with NO session renames its student; a name clash in that course is still
  refused and quoted. 28 tests now.
- **The change:** in `student_name`, the lock is taken on the enrolment rows only and only the
  course is joined: `StudentCourse.objects.select_for_update(of=("self",)).filter(student=student).select_related("course")`.
  The link from an enrolment to its course is never empty. Nothing else in the route changes.
- **Other locks, read in the three rows' own changes** (the added and removed lines of
  787a81fb..19f5c872, 19f5c872..1a2995f2 and 1a2995f2..17febb89, outside docs): H-148 adds none;
  H-152 adds none; H-153 adds this one. **None other.**
- **Mutants: 25.** New, `M25_the_lock_goes_through_the_session_join_again`: the first version's
  statement put back. Its expected set is not a prediction: the 15 tests that were red in the
  first gate's own run, by name (compared by program with that log), and the two new tests.
  `M19` is anchored on the changed line and was re-anchored; what it changes is the same.
  `mutate.py --check` passes, and it still refuses to load if a test is named by no mutant.

**A mutant I was asked for and do not claim:** "the lock taken without `of`" alone. By reading,
with only the course joined (a link that is never empty) PostgreSQL accepts the lock without
`of` as well; it would then also lock the course rows, which no test of mine can see. Such a
mutant would SURVIVE. So `of=("self",)` is a second defence that these tests do not isolate: it
keeps the lock off the course rows today, and it is what would keep the statement legal if a
join that can be empty were added to it again. I say so instead of counting it.

**Written before the fresh gate.** `run_h153_gate.sh <tip> 1 1a2995f2`, script sha256 now starts
5180bd2f47af4516 (the two new tests in the written red set, Ran 28, 25 mutants; nothing else changed).
Step 0: Ran 28, all 28 red, as errors (`NoReverseMatch`). 1a: no changes. 1: OK. 2: 25 KILLED
with their named tests. Rule 19 as it should stand after it: each of the 28 tests failed under
at least one mutant; in addition 15 of them have been seen red in the first gate.

**What I have NOT done:** run anything of this. The 24 earlier mutants' expected sets were
written by reading and have never been run either; a survivor or a set that differs is possible
and will be reported as it is.

### The fresh gate

(none yet)
