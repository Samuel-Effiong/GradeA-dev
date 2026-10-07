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

(none yet)
