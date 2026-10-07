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

### The gate at 356bdd34 (0b's GRANT, 2026-10-07 13:53 WAT)

356bdd34 is this branch's 466f85e6 and 0b's merge of the base-updated H-147 branch (787a81fb).
One run of `run_h148_gate.sh 356bdd34 1 787a81fb`. Script sha256 starts 43dcc62e68eccb3d: the
e22eeb5f of the section above, plus the guard module 0b names for every gate
(`AutoGrader.tests_migration_safety_check`), an exclusion that keeps `classrooms/test_views.py`
out of the script's list of production files, and, by the Senior Manager's ruling after H-141's
gate the same day, a halt at part 0 when the Ran count or the set of distinct red tests differs
from the list written above (the list is in the script, test by test). 13:53:20 to 14:04:34. It
ran beside Verifier 2's H-154 run; load 4.61 at the start, 8.00 at the start of part 1, 5.53 at
its end, 5.26 at the end. Not stopped, not repeated.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0. Reproduce-first, on the base's production files | Ran 39; 22 red, named above | **Ran 39 tests in 5.000s, FAILED (failures=22, errors=4)**: 26 lines (the two staff-invitation tests fail once per phrase), 22 distinct tests, exactly the 22 named; the script's own comparison: "step 0 is as written" | `prefix_base_production_failing_356bdd34.txt.gz` |
| 1a. makemigrations --check | no changes | no changes | `makemigrations_check_356bdd34.txt` |
| 1. Modules and guards at the tip | OK | **Ran 865 tests in 342.032s, OK (skipped=3)** | `modules_and_guards_356bdd34.txt.gz` |
| 2. Mutants | 26 KILLED with their named tests | **26 of 26 KILLED**: each exit 1, its own "Ran 39", every expected test among the failed (`expected-but-passed []` twenty-six times); SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN empty. Source clean after. | `mutation_log_356bdd34.txt`, `mutation_results_356bdd34.json`, `mutant_logs_356bdd34/` |

Nothing differed from what was written before the run. The run's console is
`gate_console_356bdd34.txt.gz`.

The three skips are in `users.tests_google_auth`, in their own words: 'CI_REQUIRE_NETWORK not
set' (one) and 'live Google contract tests are opt-in: set CI_REQUIRE_NETWORK=1' (two). None is
for want of a browser.

**The risk named under "Existing tests changed"** (a test of the 14 files relying on an account
that used to be nameless): it did not show in part 1. The owning apps' regression is still to
come and is where the rest of those apps' tests run.

Rule 20's cache modules were in part 1 and are green.

Rule 19, counted from these records (the part 0 log and `mutation_results_356bdd34.json`): of
the 39 new tests, 22 were red in part 0 and each of the other 17 failed under at least one
mutant. None is left unseen red.

Credential-pattern check before this commit (this folder only, inside the .gz too, values not
printed): six lines match, three in the part 0 log and three in mutant N17's log. All six are
the made-up constant `TEMPORARY` of `classrooms/tests_staff_invitations_promise_no_password_change.py`
(committed at 24a947cf with its allow-list mark), quoted by a failing assertion. Compared by
program, not by eye. Other failure messages quote temporary passwords the code generated for
accounts of the test database, which no longer exists.

Written 14:05 WAT. Still owed as this is committed: the regression (classrooms and users), on
its own grant.

### The regression at 56ce7ec5 (0b's GRANT, 2026-10-07 14:52:59 WAT)

56ce7ec5 is the gated 356bdd34 plus the docs-only results commit above; no code or test differs.
One run of `run_h148_gate.sh 56ce7ec5 3 787a81fb` (script sha256 starts 43dcc62e68eccb3d):
classrooms and users, serial, in 0b's quiet window, alone among this team's runs. 14:53:51 to
14:56:16, exit 0. Not stopped, not repeated.

| Written before | Found | Log |
|---|---|---|
| OK, with the risk named under "Existing tests changed" | **Ran 1127 tests in 127.952s, OK (skipped=4)** | `regression_56ce7ec5.txt.gz`; console `regression_console_56ce7ec5.txt` |

The skips, in their own words: one 'CI_REQUIRE_NETWORK not set', one 'CI_REQUIRE_REDIS not set',
two 'live Google contract tests are opt-in: set CI_REQUIRE_NETWORK=1'. None is for want of a
browser.

**The named risk did not show:** no test of classrooms or users failed for relying on an account
that used to be nameless. What that does not cover: the 14 changed files' tests outside these
two apps (three in AutoGrader, one in dashboard) ran in part 1 of the gate, not here.

One-minute load 3.75 at the start, 3.87 at the end.

Written 14:56 WAT. Nothing is owed on this row by me now but the hand-over to Verifier 1. H-152
and H-153 sit on 356bdd34 and do not contain this branch's two docs commits.

## A follow-up, tests only: five calls I had missed (found by Verifier 1)

**Everything in this section down to "Results of the follow-up" was written on 2026-10-07 at
15:30 WAT, before any run of it.**

### What was wrong, and whose miss

Verifier 1 found by reading, at 117feddc, and I confirmed by reading: two billing test modules
still posted an email alone to the add-by-email route, which this row made refuse a request
without first and last name. Five calls:

- `billing/tests/test_h38_teacher_removal.py`: `TeacherRemovalBase.setUp`;
  `SchoolAdminSeesRemovedTeachersNewDataTests.setUp`;
  `test_cannot_enrol_more_students_into_the_school_course`;
  `test_licensed_teacher_can_read_list_and_enrol_into_the_school_course`.
- `billing/tests/test_h38_part2_removed_teacher_routes.py`: `TeacherRemovalBase.setUp`.

80 of the 81 tests of the two modules pass through one of them (16 of 17; 64 of 64). Neither
module was in this row's gate (billing had `billing.tests.test_license_service` only) or in its
regression (classrooms and users). They would have been red in the batch's full run.

**Why I missed them:** under "Existing tests changed" above I wrote "the route's 33 existing
calls in 14 test files". I had searched for the route's NAME (`course-students`). These two
modules write the path by hand (`f"{API}/course/{id}/students"`), and I had not searched for
the path. So "33 calls in 14 files" was the calls that use the name, not the whole tree. That
sentence is left as written; this section corrects it.

### The search, this time of the whole tree (0b's GRANT, 2026-10-07 15:27; 15:27:29 to 15:27:35)

`git grep -nE` over every tracked `.py` file of the worktree at 117feddc (the `docs/` folder
left out of the hits), by the script `route_callers_search.py.txt` in this folder (sha256 of
the script as run starts d9e1d142c7153e6a); its whole output is `route_callers_search_117feddc.txt.gz`
(gzipped: 24 of its lines end in whitespace). The terms, so that the search can be judged and
not only its hits:

| | Term | Pattern | Lines / files |
|---|---|---|---|
| T1 | the route's name | `course-students` | 26 / 15 |
| T2 | the raw path, any spelling: `students`, an optional slash, then a quote (catches `"/students"`, `"students/"`, an f-string ending `/students"`, a `"/students"` piece joined on) | `students/?["']` | 252 / 90 |
| T3 | the route's own definition | `url_path="students"` | 1 / 1 |
| T4 | the body helper | `add_by_email` | 69 / 15 |
| T5 | the form | `AddStudentToCourseSerializer` | 6 / 3 |
| T6 | the service under the route | `enroll_student_by_email` | 64 / 22 |
| T7 | the class-list import (it takes names per row and requires nothing new; listed for completeness) | `bulk-add-students` or `bulk_add_students` | 21 / 14 |

T2 is wide on purpose and most of its 252 lines are not this route (dictionary keys named
"students", the dashboard routes, migrations). I went through them: the lines that name the
route or a path ending in `/students` for a course are **31, in 17 test files**:

- by the name, 26 lines in 15 files: `AutoGrader/tests_cache_matrix_concurrency.py`,
  `tests_cache_matrix_selftest.py`, `tests_probe_h1s3_commit_race.py`; `classrooms/test_views.py`,
  `tests.py`, `tests_cache_course_roster_scope.py`, `tests_concurrency_and_resilience.py`,
  `tests_course_payload_student_exposure.py`, `tests_cross_school_enrollment.py`,
  `tests_h71_student_add_role.py`, `tests_h99_placeholder_email.py`, `tests_roster_ready_to_use.py`,
  `tests_security_penetration.py`, `tests_teacher_names_student_on_add.py`;
  `dashboard/tests_cache_matrix_status_summary.py`;
- by the hand-written path, 5 lines in 2 files: the two billing modules above.

**Helpers that wrap the post** (the script lists every function that holds a site and is not a
test method, and its callers): `_enroll`, `enrol_as`, `writer`, `enrol_newcomer`, `enroll`,
`add_by_email` (a method of that name in the cross-school module), `routes` and the `post`
wrappers of the H-71 and H-99 modules, `teacher_enrolls_student_in_course_b`, and this row's own
`add`. Each builds or is handed `add_by_email(...)`. Where a test keeps the address of the route
in an attribute (`self.url`, `self.single_url`, a local `url`), I read every post made with it
in that file: `classrooms/tests.py`, `test_views.py`, `tests_roster_ready_to_use.py`.

**Result: every post to the route in the 15 files found by name sends a name; the five posts in
the two billing files did not. No other post without a name exists in the tree as I read it.**

What the search cannot see: a path assembled from pieces none of which contains "students"
followed by a quote, and callers outside `.py` files. I know of neither.

### What changes (tests only, 1d97842d)

The five calls send `add_by_email(...)`, as the 33 changed earlier do. No assertion is changed.
No production file is changed, so this row's gate and its regression stand as run and are not
repeated (Senior Manager, 2026-10-07).

**One of the five would have gone on passing for the wrong reason had only the status been
loosened:** `test_cannot_enrol_more_students_into_the_school_course` posts as a teacher removed
from the school and wants 403 or 404. The view validates the form first
(`classrooms/views.py`, `students`, `if not serializer.is_valid(): raise ValidationError`) and
looks the course up after (`get_object_or_404(self.get_queryset(), pk=...)`). With an email
alone the answer is 400 from the form, before the course is looked at. With a complete form the
removed teacher is refused by the lookup: 404, which is what the test is about. **[R]**, to be
shown by the run.

### Written before the run

Script: `~/Documents/Projects/GAP-ed-scripts/run_h148_followup.sh <tip> 117feddc` (sha256 starts
fd68b250cd6978fd as this is written). No mutant step and no makemigrations step: no production file
differs from 117feddc (the script checks that).

**A. Before** (the two billing modules with their two test files as at 117feddc, on the tip's
production code): Ran 81, FAILED, **80 red**: every test of every class built on
`TeacherRemovalBase` in the two modules (the names, one per line, are in `followup_red_written.txt`
and in the script, which halts on any difference). **Green, 1:**
`IndividualCoursesSurviveSchoolMembershipTests.test_private_course_stays_reachable_through_join_and_removal`,
which adds nobody by email.

**B. After** (as committed): the 17 modules the search found calling the route, in AutoGrader,
billing, classrooms and dashboard, and rule 20's `AutoGrader.tests_cache_bespoke_1114`: OK. No
count written.

Rule 19: the red proof here is A. It shows that the 80 tests depend on the corrected calls. It
does not show more than that; in particular the 404 of the removed teacher's add is shown by B
being green on that test, and by nothing stronger.

### Results of the follow-up

(none yet)
