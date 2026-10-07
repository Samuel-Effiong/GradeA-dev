# H-148: the teacher names a student when adding them by email

Author: Security Engineer (ed). Branch `task/h148-teacher-names-student`, stacked on H-147's
branch (both change `classrooms/serializers.py`); merges after it. Beta line. Verifier:
Verifier 1.

**Everything down to the heading "Results" was written on 2026-10-07 at 11:48 WAT, before any
run.** Nothing in it was observed in a run. **[R]** = read by me in the code.

## The rule and the decisions

Founder's representative, 2026-10-06: a student does not name themselves; only the teacher names
a student. 2026-10-07, option 1: the add-by-email form takes first and last name, REQUIRED, typed
by the teacher; middle name optional. The invitation email loses the sentence that the student
will be asked to choose their password. The teacher's email address stays in the "added" and
"removed" emails. Senior Manager, same day: the fill rule and the answer's two keys (below); a
name-clash refusal is only ever sent to a teacher of that course.

Proposal, with a correction of mine and every ruling:
`~/Documents/Projects/GAP-planning/H-148-student-rename-proposal.md`.

## What was wrong

- The add-by-email form took an email only; a new student account was created with an EMPTY
  name. **[R]**
- A student cannot change their own name: `CustomUserSerializer.validate` has refused it since
  2026-07-09 (and for a super admin too: the rule looks at whose account it is). **[R]** No test
  held that rule. I had reported the opposite on 2026-10-06; corrected in the proposal.
- No teacher route sets a name. **[R]** So a student added by email had no name and no way to
  one, and a teacher-uploaded paper, matched by name, matched nobody. **[R]** for the matching
  rule (read for the proposal).

## What changes

- `classrooms/serializers.py`, `AddStudentToCourseSerializer`: `first_name` and `last_name`
  (required, at least two letters, the direct add's rule) and `middle_name` (optional).
- `classrooms/views.py`, the `students` action: passes the names on, and a successful add's
  answer gains `student_name` (`first_name`, `middle_name`, `last_name`: the name that stands on
  the account) and `typed_name_used` (true when that name is the one typed).
- `classrooms/services/enrollment.py`, `enroll_student_by_email`: a new keyword
  `fill_empty_name`, which the single add and the class-list import pass
  (`classrooms/services/roster_import.py`; the import by the Senior Manager's ruling of
  2026-10-07: the same teacher giving the same name). With it, an account that already exists
  and has NO name (no first name and no last name; half a name is a name) is given the typed
  one. A stored name is never replaced. Refusals for a staff, switched-off, other-school or
  already-enrolled address come before any name is looked at, as they did.
- `classrooms/services/notifications.py`: the sentence "You'll be asked to choose your own
  password the first time you log in." leaves the student invitation.
- `classrooms/serializers.py` (the school admin's invitation) and `billing/license_service.py`
  (the invitation of a teacher added under a school licence): the same sentence leaves them
  too. User's decision of 2026-10-07 12:45, after I reported that the server makes nobody change
  a temporary password (`users/authentication.py`: the enforcement was removed by a product
  decision, for all three roles). **[R]** Added to this row on 2026-10-07 at 12:50, still before
  any run.
- No model, no migration.

**The name clash** needed no new code: saving an enrolment already refuses a second student of
the same exact name in a course (`StudentCourse.clean`), and the add is one transaction, so a
refused add leaves no account, no enrolment and no filled name behind. **[R]** The refusal is
read by the teacher who typed the name.

## For the frontend (the Release Engineer has these lines for the push package)

- **BREAKING for the current page:** add by email (`POST /course/<id>/students`) now REFUSES a
  request without `first_name` and `last_name` (400). `middle_name` is optional.
- A successful add's answer gains `student_name` and `typed_name_used`. `typed_name_used` is
  false when the address already had an account with a name, which is kept; the page can then
  tell the teacher which name stands.
- The student invitation email no longer says the student will be asked to choose their own
  password; nor do the school admin's and the licensed teacher's invitations.
- I cannot read the frontend.

## What it does NOT do

- **The old activation door** (`POST /auth/register/student`) still takes a name from the
  student. Whether it is closed outright is the user's open question; the tests plan for both
  forms is in the proposal.
- **The teacher's rename route** does not exist yet (three details are with the user). So a
  student who is ALREADY nameless and ALREADY enrolled is not cured by this row: a second add
  answers "already enrolled". They are cured only when a teacher adds them to another course, or
  by the rename route, or by the founder. The founder's read-only count is
  `GAP-planning/H-148-nameless-students-count.sql`, not run.
- **The class-list import** fills too (ruled 2026-10-07), for a row that carries an email. A row
  without an email is matched by name as before and is not touched. The row of the import's
  answer already showed the account's full name; it now shows the filled one.
- **A direct add with an email** is unchanged (open with the user).
- The two-letter minimum refuses a genuine one-letter name: logged as H-151 (LOW, product).
- Nothing was observed on a live or staging service.

## Tests

`classrooms/tests_teacher_names_student_on_add.py`, 25 tests (21 at 512656d1; four for the
class-list import in a second tests-only commit), through the routes as the teacher;
the outgoing mail is captured, nothing else replaced. `users/tests_student_cannot_name_themselves.py`,
12 tests, real tokens, real routes: the first tests of the old refusal, and three pins of Google
sign-in. `classrooms/tests_staff_invitations_promise_no_password_change.py`, 2 tests (a third
tests-only commit, 24a947cf). Both committed first, tests only, at 512656d1; one of the 12 was then made sharper with
the change (the teacher now tries to edit their OWN student's account, so what refuses is the
edit rule and not the account being out of sight).

**Existing tests changed, and why.** The route's 33 existing calls in 14 test files sent an email
alone, which is now refused before anything those tests are about. Each now sends
`add_by_email(email)`: the address and a name made from the address (helper
`classrooms/tests_support_add_by_email.py`, no test in it). No assertion of those files is
changed. Files: `classrooms/tests.py`, `test_views.py`, `tests_cache_course_roster_scope.py`,
`tests_h99_placeholder_email.py`, `tests_course_payload_student_exposure.py`,
`tests_cross_school_enrollment.py`, `tests_concurrency_and_resilience.py`,
`tests_security_penetration.py`, `tests_roster_ready_to_use.py`, `tests_h71_student_add_role.py`;
`AutoGrader/tests_cache_matrix_selftest.py`, `tests_cache_matrix_concurrency.py`,
`tests_probe_h1s3_commit_race.py`; `dashboard/tests_cache_matrix_status_summary.py`.
**A risk I name instead of hiding:** some of those tests may assert something about an account
that used to be nameless and is now named, or filled. I did not read every assertion in the 14
files. If the run shows such a test red, it is reported as it is and judged one by one.

## Written before the runs

Gate script: `~/Documents/Projects/GAP-ed-scripts/run_h148_gate.sh` (sha256 starts
e22eeb5f789b8963 after the staff invitations were added at 12:50). Its base argument is the tip this branch is stacked on.

**0. Reproduce-first** (the three new modules on the production files as at the base): Ran 39,
FAILED, **22 red**: the two staff-invitation tests, and 20 in
`classrooms.tests_teacher_names_student_on_add`: every test of that
module except `test_a_different_middle_name_is_a_different_name`, the three of
`ARefusalDoesNotDependOnTheNameTest`, and the import's
`test_a_stored_name_stands_and_the_row_shows_it`. The 12 tests of the users module are GREEN there: they
hold a rule that already stood.

**1a.** `makemigrations --check`: no changes.

**1. Modules and guards at the tip**: OK. No count written. Rule 20: the AutoGrader cache modules
are in the list (`tests_cache_bespoke_1114` and eight others).

**2. Mutants**: 26, each KILLED with at least the tests `mutate.py` names
(`--check` passes). Fifteen on this row's six files (the form, the route, the fill rule, the
import, the three emails); three that make a refusal name the account; two on the enrolment's name clash; five on
the old account-edit rule; one on Google sign-in. Eight of the 26 mutate files this row does not
change: they are the red proof of the tests that are green from the start.

**3. Regression** (classrooms, users, serial; own grant): OK, with the risk named above. The
billing app is not in it: this row changes one sentence of one email there, and
`billing.tests.test_license_service` (which reads that email) is in step 1.

Rule 19, as it should stand after these runs: of the 39 new tests, 22 red in step 0; the other
17 each under a mutant (`mutate.py` names which). None is left unseen red.

## Not done

Nothing run. No frontend read. `origin/main` not read beyond the account-edit rule.

## Results

(none yet)
