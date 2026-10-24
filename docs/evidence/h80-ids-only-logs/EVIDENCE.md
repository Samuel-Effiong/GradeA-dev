# H-80 / H-86: licence and signal logs carry ids, never an address

**Author:** d5. **Branch:** `task/h80-ids-only-logs`, base-updated by 0b
onto `task/beta-batch-5` `83fe58ca` (clean merge, `b0f44443`).
**Scope:** `billing/license_service.py` and `users/signals.py`, plus the two
reason lines in `billing/services.py` that 1a's pre-review P1 needed. Other
modules are row H-91.

## The change (4 points)
1. **Before:**
   - H-80: about 30 logger calls in the two modules formatted a teacher's,
     admin's or super-admin's address. One logged whole `failed_results`
     dicts (address plus refusal text). Several logged an exception's text,
     which for the enrolment refusals carries the address.
   - H-86: a refusal raised inside `_enroll_teacher_internal` (the seat
     limit, a teacher of another school) reached the operator only as
     "Skipped enrolling ... ValueError".
2. **After:**
   - Every logger call in the two modules names users, licences, schools
     and requests by id, and an exception by its class.
   - Each refusal logs its own ids-only reason line before it raises: the
     seat limit and the other-school check in `_enroll_teacher_internal`,
     and (1a's P1) both refusals in
     `SubscriptionService.activate_automatic_free_trial`: a WARNING for a
     trial already used, an ERROR for a missing trial plan.
   - The two renewal-failure lines (Stripe and offline) keep their
     traceback (`exc_info=True`); they are unexpected-failure paths.
   - Nothing returned to a caller or a client changes, with one internal
     exception: the renewal paths' `failed_teachers` list, which is only
     logged, now holds user ids.
3. **Reach:** log output only. Anyone who searched the logs by address
   searches by user id now. No response body, model or migration changes.
4. **Tests:** `billing/tests/test_logs_carry_no_email.py`:
   - a source guard over both modules (no `.email`, no address-holding
     name, no `failed_results`, no exception text, and the message must be
     a plain literal: 1a's P2), with a self-test of every rejected shape;
   - behaviour tests on the paths 1a's probes found leaking (carry-forward,
     the failed-creation summary, invite-and-enrol, the seat-limit and
     other-school reason lines, user creation, the free-trial reasons).

   `test_license_renewal_partial_failure.py` now expects the failed
   teacher's id (it asserted the address) and a traceback on both
   renewal-failure lines.

| Commit | What |
|---|---|
| `21670117` | the tests (red) |
| `5565b246` | the fix in the two modules |
| `446b07a9` | the success-path test really runs its on-commit callback; the other-school reason test; `run_mutants.py` |
| `7cbd88be` | the renewal test expects the id (test only) |
| `f811d9bc` | tests for 1a's P1 and P2 |
| `af5cee04` | P1's fix (`billing/services.py`, the two renewal lines); mutants P1–P5, G6–G7 |

For ed's byte-identical Epic A copy, `billing/license_service.py` changes in
`5565b246` and `af5cee04` only.

## Known limit: tracebacks (row H-89)
13 logger calls in the two modules attach a traceback. Their message and
arguments are ids-only, but a traceback prints the exception's own text,
and that text can carry an address: for example an `IntegrityError`
("Key (email)=(…) already exists") or a `ValueError` built with an address.
The guard and the tests check messages and arguments only.

- `billing/license_service.py`: 790, 881, 1033, 1316, 1957, 3542, 3593,
  3639, 3854 (1957 and 3854 are the renewal-failure lines of P1).
- `users/signals.py`: 319, 332, 377, 408.

The SM ruled to keep the tracebacks (they are what makes these paths
diagnosable). The fix is H-89: one logging filter that scrubs addresses
from messages and formatted exception text on every handler (owner ed).

## Gates
The final chain (`chain.sh`) ran once on the frozen tip `af5cee04`, in one
slot granted by 0b.

| Gate | Result | Log |
|---|---|---|
| Repro: the tip's two test modules over `83fe58ca`'s `license_service.py`, `signals.py` and `services.py` (h78-repro worktree, own DB) | 15 tests, 10 failures, 1 error | `repro_af5cee04_tests_over_83fe58ca_code.log` |
| (a) 16 modules | 300 OK | `a_modules_af5cee04.log.gz` |
| (b) battery (`test_h80_mut`) | baseline green, 23/23 killed, every restore sha-verified | `b_mutation_battery_af5cee04.log`, `logs/`, `results.tsv` |
| (c) billing + users + the 9 guards | 2772 OK (skipped=6), wall 373 s | `c_apps_billing_users_guards_af5cee04.log.gz` |

The repro's error is the seat-limit test: with no reason line, `assertLogs`
captures nothing and raises. The four tests that pass on the old code are
the guard's self-test and three renewal tests the change does not touch.

(a)'s modules: `test_logs_carry_no_email`,
`test_other_school_before_subscription`,
`test_add_teachers_other_school_not_disclosed`, `test_license_service`,
`test_license_seat_400`, `test_license_seat_counting`,
`test_license_teacher_changes_400`, `test_license_renewal_partial_failure`,
`test_license_overage_offline`, `test_h38_teacher_removal`,
`users.tests_signals_and_edges`, `tests_free_trial`,
`test_trial_forfeiture_on_activation`, `test_free_plan_activation_security`,
and H-82's `test_annual_grant_anchor` and `test_next_monthly_grant` (the
fold edits `billing/services.py`).

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as
`systemd-inhibit --what=idle:sleep … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16). (c) ran with `--parallel 2 --verbosity 2` through a
timestamper.

**Rule 17.** The battery and its baseline ran with
`PYTHONDONTWRITEBYTECODE=1` (set for the runner and passed to every test
subprocess), and the runner deleted `billing/__pycache__` and
`users/__pycache__` in its worktree before the baseline, before each mutant
and after each restore. The runner ran inside the wrapper above, so the
whole battery shared one 6G scope and one 1800 s timeout.

## Earlier runs (superseded; the tip moved four times)
| Tip | What ran | Why it was superseded |
|---|---|---|
| `82b9c0a3` (base `d97b7e7c`) | repro red as expected (8 tests, 6 failures, 1 error); **(a) red: 152 tests, 1 failure** (`a_modules_82b9c0a3_red.log`); (b), (c) not run | `test_a_failed_teacher_is_reported_and_not_counted_as_renewed` asserted the failed teacher's address in the ERROR log, the behaviour H-80 removes. Re-coded in `7cbd88be`. A grep of the billing and users tests found no other test expecting an address in a log. |
| `7cbd88be` | repro red, (a) 152 OK, (b) 16/16 killed; (c) stopped by me after 3 minutes, not a result | H-60/H-57 merged into the batch and changed `license_service.py`; 0b base-updated onto `6b627cc4`. |
| `932acc22` (base `6b627cc4`) | repro red, (a) 152 OK, (b) 16/16 killed, (c) 2757 OK (skipped=6) | 1a's pre-review P1 and P2 were ruled into the branch, and H-82 merged into the batch (`billing/services.py`); 0b base-updated onto `83fe58ca`. |

Their status files are in `superseded/`. Only the red (a) log is committed;
the other superseded logs are in `~/Documents/Projects/GAP-d5-runs/h80/`.

## Mutants
B mutants run against the behaviour tests only (the guard is not loaded);
G mutants put a leak on a path no behaviour test drives and run against the
guard only; P mutants run against the tests of the lines they break.

| Id | Guards | Result |
|---|---|---|
| B1 | a carried-forward teacher's refusal names the teacher by id | killed |
| B2 | that line logs the refusal's class, not its text | killed |
| B3 | the failed-creation summary does not log `failed_results` | killed |
| B4 | a skipped enrolment logs the refusal's class, not its text | killed |
| B5 | the queued-invitation line names the teacher by id | killed |
| B6 | the enrolment line names the teacher by id | killed |
| B7 | H-86: the seat-limit refusal logs a WARNING reason line | killed |
| B8 | H-86: that line names the teacher by id | killed |
| B9 | H-86: the other-school refusal logs a WARNING reason line | killed |
| B10 | the new-user signal line names the user by id | killed |
| B11 | the trial-check signal line names the user by id | killed |
| P1 | a used trial's refusal logs a WARNING reason line | killed |
| P2 | a missing trial plan is logged at ERROR | killed |
| P3 | the missing-plan line names the user by id | killed |
| P4 | the Stripe renewal-failure line carries a traceback | killed |
| P5 | the offline renewal-failure line carries a traceback | killed |
| G1 | guard: an `.email` on an undriven path | killed |
| G2 | guard: an exception's text (license_service) | killed |
| G3 | guard: an exception's text (signals) | killed |
| G4 | guard: `failed_results` | killed |
| G5 | guard: a name holding an address | killed |
| G6 | guard (1a's P2): an address formatted into the message with `%` | killed |
| G7 | guard (1a's P2): a message built by an f-string, even from an id | killed |

B7 is killed by an error, not a failure: with the line at DEBUG the test's
one-element unpack of the reason lines raises.

## For the verifier
- 1a's P1 and P2 are in: the reason lines and tracebacks in `af5cee04`
  (tests in `f811d9bc`), the guard's message check in `f811d9bc`.
- The success-path test first called `captureOnCommitCallbacks` without
  `with`, so the queued-invitation line never ran. Since `446b07a9` it runs
  the callback and asserts the "Queued" and "Enrolled" lines fired.
- Out of scope, with rows: tracebacks (H-89), and address-bearing logs in
  other modules (H-91; `billing/services.py` alone has 19 such calls and
  one that logs an exception's text, counted by pointing the guard at it
  without a test run).
- The logs' addresses are test fixtures only.
