# H-152: the old activation door is closed

Author: Security Engineer (ed). Branch `task/h152-old-activation-door-closed`, stacked on H-148's
branch; merges after it. Beta line. Verifier: Verifier 1.

**Everything down to the heading "Results" was written on 2026-10-07 at 13:02 WAT, before any
run.** Nothing in it was observed in a run. **[R]** = read by me in the code.

## The decision

User, 2026-10-07 (through the Senior Manager): the old code-based student sign-up is closed
outright. Senior Manager, same day: both of its routes answer 410 with one fixed sentence,
whatever is sent; the ordinary per-address request limit stays on them; no setting keeps the door
open; order on both services: the batch first, the conversion of leftover invitations the same
day.

## What the door was

`POST /auth/register/student`: a student holding a 6-digit code sent the code, a password and
THEIR OWN first, middle and last name; the names overwrote what was stored. **[R]** Its companion,
`POST /course/student/renew-student-token`, mailed a new code for an expired one. **[R]** Since
the temporary-password scheme nothing on this line mints such a code; what remains are
invitations sent before it. A student does not name themselves (founder, 2026-10-06), and this
door was the last place where one could.

## What changes

- `users/views.py`, `register_student`, and `classrooms/views.py`, `handle_expired_token`: each
  now returns 410 and "Invitations of this kind are no longer used. Ask your teacher to add you
  to the class again." (`OLD_INVITATION_CLOSED_MESSAGE` in `classrooms/services/enrollment.py`,
  exported by `classrooms/services/__init__.py`). Nothing is read from the request, nothing is
  written, nobody is mailed, and the shared budget for wrong codes is no longer touched: there is
  nothing left to guess. The routes keep their per-address rate limit (`RegisterThrottle`).
- `classrooms/management/commands/backfill_pending_student_invites.py`: its summary line, in the
  dry run and in the real run, also says how many of the accounts it converts have no name. An
  old-scheme account was created without one, so each must then be named by a teacher (H-153).
  Its behaviour is otherwise unchanged.
- No model, no migration.

## Left in place, on purpose

Code that no longer has a caller, NOT removed here: `renew_student_activation`,
`send_token_renewal_emails`, `send_course_invitation_email`, the two request serializers
(`StudentRegistrationCompletionSerializer`, `ExpiredTokenSerializer`), and the wrong-code budget
helpers in `users/throttling.py`. **[R]** Removing them is a tidy-up with its own tests to
remove; I would log it as a LOW row and not widen this one.

## For the frontend and for the people

- The activation page and the "renew my link" action now get 410 and the sentence above for every
  request. What the page shows then, I cannot read.
- A student who still holds an old code cannot use it. Their account and their pending place in
  the course are untouched. They get in when the founder's conversion mails them a temporary
  password, or when their teacher adds them again by email (which also names them, H-148).
- Runbook for the conversion, for the founder (nobody on the team runs it on live data):
  `~/Documents/Projects/GAP-planning/H-152-conversion-runbook.md`.

## Tests

`users/tests_old_activation_door_is_closed.py`, 11 tests. Ten were committed first, tests only,
at 50926f56. Changed with the fix, and said plainly:

- **One test was too weak as first committed.** `test_wrong_codes_spend_no_budget...` made forty
  knocks against a budget whose default is 100, so it could not have noticed a knock that still
  counted. It now runs with a budget of 5.
- **One test is added with the fix**, not before it: `test_a_pending_teachers_code_completes_nothing`
  (H-47's point, see below).

**Existing tests removed, each because the thing it tested no longer exists.** 27 tests; no
assertion of any remaining test is changed. For Verifier 1 to check one by one:

| File | Removed | What they tested |
|---|---|---|
| `users/tests_register_student_token_scope.py` (the whole module) | `RegisterStudentTokenScopeTests`, 5 tests | H-47: only a STUDENT's code may complete an account at the door; a teacher's or school admin's code must not; a student's still does |
| the same module | `RegisterStudentGlobalFailureBudgetTests`, 11 tests | the shared budget for wrong codes at the door and the renewal: when it is spent, what the 429 says, what is logged, that a success does not count |
| `users/tests_auth_endpoints.py` | `StudentRegistrationBranchTests`, 2 tests | branches of the door's completion |
| `users/tests_auth_input_validation.py` | `RegisterStudentExpiryTests`, 5 tests | the door's handling of an expired or missing expiry |
| `users/tests.py` | `StudentRegistrationTests`, 1 test | the door's refusal of a classmate's exact name |
| `classrooms/tests.py` | `RenewActivationTokenTest`, 3 tests | the renewal of a code |

**H-47's point is kept**, in the new module: a pending TEACHER's row holds a code in the same
column, and that code used to complete the teacher's account through this door with a password
the caller chose. A door that answers 410 to everything completes nobody; one test says so for
that case (the row is unchanged, and the caller's password does not log in).

Unused imports left by the removals are removed in those files; nothing else in them is touched.
`users/tests_throttle_client_identity.py` names the routes only in a comment and is unchanged.
The old evidence folder `docs/evidence/register-student-token-scope/` is left as history; its
runner names a module that no longer exists.

## Written before the runs

Gate script: `~/Documents/Projects/GAP-ed-scripts/run_h152_gate.sh` (sha256 starts
08cda346515e9099). Its base argument is the tip this branch is stacked on.

**0. Reproduce-first** (the new module on the production files as at the base): Ran 11, FAILED,
**10 red**: every test but `test_the_output_still_carries_ids_only`.

**1a.** `makemigrations --check`: no changes.

**1. Modules and guards at the tip**: OK. No count written. Rule 20 does not apply: this row
changes what two uncached POST routes answer and what a command prints, no serializer and no
cached route.

**2. Mutants**: 9, each KILLED with at least the tests `mutate.py` names (`--check` passes): the
door answering 200; the renewal answering 200; another sentence; the door giving the code back;
the renewal giving it back; a knock spending the budget; the command not counting the nameless,
counting everyone as nameless, and printing an address.

**3. Regression** (users, classrooms, serial; own grant): OK.

Rule 19, as it should stand after these runs: ten of the eleven tests red in step 0;
`test_the_output_still_carries_ids_only` under mutant C3. Two tests are red in step 0 for a
reason no mutant repeats, and I say so: `test_the_answer_names_no_course_and_no_classmate` and
`test_an_expired_code_is_not_renewed_and_nobody_is_mailed` (their distinct assertions, no
classmate's name and no mail, cannot be broken by a one-line change to a function that reads
nothing; mutants D1 and D2 fail them through their status check only).

## Not done

Nothing run. No frontend read. `origin/main` not read: there the old scheme is the live one, and
this row reaches it only with the next promotion.

## Results

### The gate at 8b396aa9 (0b's GRANT, 2026-10-07 14:56:18 WAT)

8b396aa9 is this branch's fade2ade and 0b's merge of the base-updated H-148 branch (356bdd34).
One run of `run_h152_gate.sh 8b396aa9 1 356bdd34`. Script sha256 starts ab717bc25e91447f: the
script named above plus, by the Senior Manager's ruling of the same day, a halt at part 0 when
the Ran count or the set of distinct red tests differs from the written list (the list is in
the script, test by test). 14:56:50 to 15:01:25. Load 4.38 at the start, 5.94 at the start of
part 1, 4.88 at its end, 7.74 at the end. Not stopped, not repeated.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0. Reproduce-first, on the base's production files | Ran 11; 10 red: every test but `test_the_output_still_carries_ids_only` | **Ran 11 tests in 1.393s, FAILED (failures=10)**: exactly those ten; the script's own comparison: "step 0 is as written" | `prefix_base_production_failing_8b396aa9.txt.gz` |
| 1a. makemigrations --check | no changes | no changes | `makemigrations_check_8b396aa9.txt` |
| 1. Modules and guards at the tip | OK | **Ran 506 tests in 141.666s, OK (skipped=3)** | `modules_and_guards_8b396aa9.txt.gz` |
| 2. Mutants | 9 KILLED with their named tests | **9 of 9 KILLED**: each exit 1, its own "Ran 11", every expected test among the failed (`expected-but-passed []` nine times); SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN empty. Source clean after. | `mutation_log_8b396aa9.txt`, `mutation_results_8b396aa9.json`, `mutant_logs_8b396aa9/` |

Nothing differed from what was written before the run. The run's console is
`gate_console_8b396aa9.txt.gz`.

The three skips are in `users.tests_google_auth`, in their own words: 'CI_REQUIRE_NETWORK not
set' (one) and 'live Google contract tests are opt-in: set CI_REQUIRE_NETWORK=1' (two). None is
for want of a browser.

Rule 19, counted from these records: of the 11 new tests, 10 were red in part 0 and the
eleventh (`test_the_output_still_carries_ids_only`) failed under mutant C3. None is left unseen
red.

Credential-pattern check before this commit (this folder only, inside the .gz too): no line
matches.

Written 15:02 WAT. **This gate does not close the row.** While it ran, the Senior Manager ordered
a delta on this branch (about 15:00): the conversion command must not convert an account whose
login email could not be queued. It follows as tests first, then the change, then its own short
gate; the regression (users and classrooms) runs on the final tip after that.

## The delta: the conversion needs a queued email

**Everything in this section down to "Results of the delta" was written on 2026-10-07 at 15:07
WAT, before any run of it.** **[R]** = read by me in the code.

### What was wrong

Found while writing the runbook, by reading. The conversion switches a leftover account on, sets
a new temporary password and queues the email that carries it. The sender
(`send_student_login_invitation_email`) hands the email to `safe_delay`, which swallows a queue
that cannot be reached and returns nothing; the sender returned nothing either. **[R]** So when
the queue was down at that moment the student came out switched on, with a password nobody
holds and no email; the command printed "convert: student <id>" for them like for anyone; the
only trace was a line in the service's log with no student id. And "forgot password" refuses an
account that was never verified ("Email not verified.", `POST /auth/otp`), which a converted
account never was. **[R]** The conversion is run once, by hand, and cannot be undone.

Senior Manager's ruling (2026-10-07, about 15:00): cure it now, on this row, before anyone runs
the conversion.

### What changes

- `classrooms/services/notifications.py`: `send_student_login_invitation_email` returns True
  when the email was handed to the queue and False when the queue could not be reached. It
  still never raises. Its two other callers, in `enroll_student_by_email` (a new student; a
  student who never signed in), ignore the answer as before: a teacher's add must not fail, or
  be undone, on an email. No line of `classrooms/services/enrollment.py` is changed.
- `classrooms/management/commands/backfill_pending_student_invites.py`: when the answer is
  False the command raises inside that one account's transaction, which undoes its conversion;
  it prints `NOT converted (email could not be queued): student <id>` in place of the "convert"
  line, counts it, and goes on. The summary gains `N NOT converted (email could not be queued;
  run again when the email queue is up).` The account still matches the selection, so a second
  run takes up exactly those. A dry run tries no queue and is unchanged but for that count (0).
- No model, no migration, no route, no serializer. Rule 20 does not apply: no serializer's
  output and no cached route's answer changes.

### Every caller, and every test that replaces the sender (grep, 2026-10-07)

Callers: the command; `enrollment.py` twice. Tests that replace the SENDER itself with a mock:
`classrooms/tests.py` (4), `classrooms/tests_backfill_pending_student_invites.py` (9),
`classrooms/tests_roster_ready_to_use.py` (the whole notifications module). A mock's answer is
truthy, so the command converts under them as it did. Tests that replace the QUEUE inside the
notifications module: three AutoGrader cache modules and `classrooms/tests_cache_course_roster_scope.py`
with a function returning None (the sender then answers False; they only add students, where
the answer is ignored), and `classrooms/tests_teacher_names_student_on_add.py` and this row's
`users/tests_old_activation_door_is_closed.py` with a mock (truthy). All of these modules are in
the delta's gate. I did not find a test that runs the command with a queue returning None.

### What it does NOT do

- It cannot know about an email that was queued and lost later (the mail provider, a spam
  folder). Such a student still cannot use "forgot password" until they have verified or signed
  in once; that is today's behaviour for every student added by email, and whether it should
  change is a product question the Senior Manager has taken to the user.
- The email is queued inside the account's transaction, before the commit, as it was. If the
  commit itself failed after a successful queueing, an email would go out for a password that
  was not saved. Not changed, not tested; I name it.
- The teacher's ordinary add is deliberately unchanged (two tests hold it).

### Tests

`classrooms/tests_conversion_needs_a_queued_email.py`, 11 tests, committed first, tests only
(87dc952a). The queue fails the way it really fails: the task's `.delay` raises a connection
error and the real `safe_delay` handles it; the tests also require the log line the runbook
tells the operator to look for.

### Written before the run

Gate script: `~/Documents/Projects/GAP-ed-scripts/run_h152_delta_gate.sh <tip> 87dc952a` (sha256
starts ef2e1943d442905f as this is written). The main script's list of production files gains
`classrooms/services/notifications.py` for the regression (sha256 now starts 4cde1f03112df21c).

**0. Reproduce-first** (the delta's module on the two production files as at 87dc952a): Ran 11,
FAILED, **7 red**: both tests of `TheSenderSaysWhetherTheEmailWasQueuedTest`, and five of
`TheConversionNeedsAQueuedEmailTest`: `test_the_account_is_left_exactly_as_it_was`,
`test_the_summary_counts_them`, `test_a_second_run_picks_up_exactly_those`,
`test_the_output_names_nobody_when_an_email_could_not_be_queued`,
`test_a_run_with_the_queue_up_says_none_was_left`. **Green there, 4**, each read against the old
code: `test_the_run_goes_on_and_converts_the_others` (the old code converts everyone),
`test_the_preview_queues_nothing_and_does_not_guess` (a dry run never queued), and the two of
`AnOrdinaryAddByEmailIsUnchangedTest` (they hold what must not change). The script compares the
set and halts on a difference.

**1a.** `makemigrations --check`: no changes.

**1. Modules and guards at the tip**: OK. No count written. In the list: the delta's module,
this row's own module, the related modules of the first gate (H-148's add by email and the
class-list import among them), the four modules that replace the queue, the guards.

**2. Mutants, 14**, each KILLED with at least the tests `mutate.py` names (`--check` passes, and
it refuses to load if one of the eleven delta tests is named by no mutant):

| Mutant | Must fail at least |
|---|---|
| Q01 the sender always says queued | sender-false; left as it was; summary; second run; names nobody |
| Q02 the sender never says queued | sender-true; run goes on; summary; second run; none was left |
| Q03 the command ignores the answer | left as it was; summary; second run; names nobody |
| Q04 one unqueued email stops the whole run | left as it was; run goes on; summary; second run; names nobody |
| Q05 the conversion is not undone (no transaction) | left as it was; summary; second run |
| Q06 the summary does not count them | summary |
| Q07 the line shows the address | left as it was; names nobody |
| Q08 the preview converts | preview |
| Q09 an unconverted account is listed as converted | left as it was; summary |
| Q10 a new student's add is undone when the queue is down | ordinary add, new student |
| Q11 a re-invited student's add is undone when the queue is down | ordinary add, never signed in |
| C1, C2, C3 (the first gate's three on the command file, which the delta changes) | as before: the nameless count twice; ids only |

Each inner run is of both modules: "Ran 22". The six mutants of the first gate on the two views
files are not run again: those files are untouched.

Rule 19, as it should stand after this run: of the 11 delta tests, 7 red in step 0; the other 4
each under a mutant (run goes on: Q02, Q04; preview: Q08; the two ordinary adds: Q10, Q11).

**3. Regression** (users, classrooms, serial; own grant; on the final tip): OK.

### Results of the delta

**The delta's gate at 51cd2773 (0b's GRANT, 2026-10-07 15:11 WAT).** One run of
`run_h152_delta_gate.sh 51cd2773 87dc952a` (script sha256 starts ef2e1943d442905f). 15:11:33 to
15:19:02. It ran beside the Hardening Engineer's H-144 chain; one-minute load 6.34 at the start,
7.16 at the start of part 1, 12.07 at its end, 11.02 at the end. Not stopped, not repeated.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0. Reproduce-first, on the two production files as at 87dc952a | Ran 11; 7 red, 4 green, named above | **Ran 11 tests in 1.438s, FAILED (failures=7)**: exactly the seven; the script's own comparison: "step 0 is as written" | `prefix_base_production_failing_delta_51cd2773.txt.gz` |
| 1a. makemigrations --check | no changes | no changes | `makemigrations_check_delta_51cd2773.txt` |
| 1. Modules and guards at the tip | OK | **Ran 553 tests in 241.860s, OK (skipped=3)** | `modules_and_guards_delta_51cd2773.txt.gz` |
| 2. Mutants, 14 | KILLED with their named tests | **14 of 14 KILLED**: each exit 1, its own "Ran 22", every expected test among the failed (`expected-but-passed []` fourteen times); SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN empty. Source clean after. | `mutation_log_delta_51cd2773.txt`, `mutation_results_delta_51cd2773.json`, `mutant_logs_delta_51cd2773/` |

Nothing differed from what was written before the run. The load was high in part 2; no kill is
a timeout: every inner log has its own Ran line of 22 tests. The run's console is
`gate_console_delta_51cd2773.txt.gz`.

The three skips are the opt-in live Google tests, as in the first gate. None is for want of a
browser.

Rule 19, counted from these records: of the 11 delta tests, 7 were red in part 0. The 4 that
were green there were each seen red by name under a mutant: the run goes on (Q02 and Q04), the
preview (Q08), a new student's ordinary add (Q10), a re-invited student's ordinary add (Q11).
None is left unseen red.

Credential-pattern check before this commit (the delta's files only, inside the .gz too): no
line matches.

Written 15:19 WAT. Still owed as this is committed: the regression (users and classrooms) on
the final tip, on its own grant.
