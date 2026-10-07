# H-174: a plan bought during the free trial is not "scheduled to cancel"

Author: Security Engineer (ed). Branch `task/h174-converted-trial-reads-cancelling`, from the pushed beta
tip d7143538 (the Release Engineer's choice: no open row touches the three billing files). Beta line.
HIGH (Senior Manager, 2026-10-07: money). Verifier: Verifier 1.

**Everything down to the heading "Results" was written on 2026-10-07 at 18:00 WAT, before any run.**
Nothing in it was observed in a run, on a service, in a database or at Stripe. **[R]** = read by me
in the code. The longer report written for the user, in plain words, is outside the repository:
`~/Documents/Projects/GAP-planning/trial-converted-reads-cancelling-investigation.md`.

## What the user saw

On the trial service a new subscriber on the Standard plan is shown "Subscription scheduled to
cancel". The API answer: `is_trial` false, `auto_renew` false, `cancelled_at` null,
`cancellation.has_pending_cancellation` true (created 16:23, updated 16:26). Pressing "Keep
subscription" leaves the banner; that request's own answer still reads as a pending cancellation.

## The two faults [R]

1. **Buying a plan during the trial leaves the trial's "does not renew" mark on the paid record.**
   Every trial is created with `auto_renew=False` (`billing/services.py`, `activate_free_trial` and
   the automatic trial at sign-up). A purchase during the trial turns the SAME record into the paid
   one: `finalize_trial_to_paid_conversion`, called by `_handle_individual_checkout` when the
   Checkout-completed message names a running trial. Neither it nor
   `finalize_trial_conversion_via_stripe` (a Stripe-side trial's first charge) assigned
   `auto_renew`. The page's rule (`billing/serializers.py`, `_has_pending_cancellation`) is
   "active, not a trial, `auto_renew` false, cycle not ended". A teacher who buys with NO running
   trial gets a new record with the mark true; so only purchases during the trial are affected.
2. **"Keep subscription" corrected our record only when Stripe was cancelling.**
   `SubscriptionReactivationService.reactivate_if_cancelling` asked Stripe (one read) and set
   `auto_renew` back only inside `if stripe_sub.get("cancel_at_period_end")`. For these records
   Stripe never was, so nothing was written; the request answered 200, status "already_active",
   "already active and set to renew, nothing to resume", around a subscription that still read as
   a pending cancellation.

## What it costs [R]

- **Not the plan, the credits or the renewal.** For an individual subscription nothing reads the
  mark but cancel, "keep" and the page's rule: confirmed by a search of every tracked file outside
  docs/ on both lines' commits (`H-174-search_d7143538.txt`, `H-174-search_9c21bee8.txt`, beside the
  report). A renewal replaces the record with a new one whose mark is true, so the banner goes at
  the first renewal: a month, or a YEAR on an annual plan.
- **Money and trust.** Stripe was never told to cancel and charges at renewal, while the page says
  "is scheduled to end ... won't renew". A customer who wants to leave may think it is arranged.
  Whether the page still offers a Cancel button in that state I cannot read (no frontend code).
- A school licence is not reached (no licence trial; a licence's own mark is set false only by its
  own cancel paths). The live line (origin/main 9c21bee8) has the same faults in the same functions.

## What changes (Senior Manager's ruling: cures 1 and 2 in this row)

1. `billing/services.py`: both conversions set `auto_renew=True` and clear `cancelled_at`, assigned
   AND named in the same save's `update_fields`.
2. `billing/stripe_service.py`, `reactivate_if_cancelling`: a second branch. When Stripe's answer
   says `cancel_at_period_end` **is False** and the local record is active, not a trial, its cycle
   not ended: `auto_renew` is set true if false, `cancelled_at` cleared if set, under a row lock,
   with no call that changes Stripe. `local_changed` reports it. Never for a trial (its mark is
   false by design). Never when the answer omits the flag (`is False`, not falsy: the caution the
   webhook's sync already takes).
3. `billing/stripe_service.py`, `select_plan`: the sentence "We've undone the scheduled
   cancellation" is added only when a cancellation was undone AT STRIPE (`stripe_changed`), not
   when a local record was corrected.
4. `billing/views.py`, `resume`: for a corrected record the answer is status "resumed" with "Your
   subscription was never scheduled to cancel with our payment provider. Our record showed
   otherwise and has been corrected ..."; "already active" is said only when nothing needed doing,
   and the enclosed subscription then reads the same.
5. `billing/views.py`, `cancel`: a paid record with `auto_renew` false and NO recorded date was
   never cancelled through this request (it is a converted trial; Stripe was never told). For it
   the request now answers "Subscription will not renew at the end of the current billing cycle"
   and records the date, in place of "already set to not renew" and no date. A record with a
   recorded date, and a trial, answer as before.

No model, no migration, no serializer change. What a request ANSWERS changes in items 4 and 5 only.

**Three older tests are reversed, in a tests-only commit of their own (b60007c2), and I say so:**
`test_noop_when_not_cancelling` held the old rule of fault 2 as correct (now
`test_local_record_is_corrected_when_stripe_is_not_cancelling`; the noop is kept for a record that
agrees with Stripe); the past-due test's "nothing changed" becomes "nothing changed at Stripe";
`test_already_not_renewing_reports_so` had a fixture with the mark false and no date, which is
exactly the state item 5 now treats as never cancelled, so the fixture carries a date.

## Not built, on the Senior Manager's word

- **A correction of records already converted.** They are corrected when the customer presses "Keep
  subscription" (item 2), when they choose another plan, or at their first renewal. A count for the
  user is written (`~/Documents/Projects/GAP-planning/H-174-converted-trials-count.sql`: counts only,
  never tried against a database, nobody on the team runs it). A correction command is NOT built
  until the user says so, and must then ask Stripe about each record first: a record really
  cancelled in Stripe's dashboard before the date column existed looks the same in our database.
- **Requiring a recorded date for "pending cancellation"** in the serializer: not done.

## Limits, stated

- **An affected customer who does nothing still reads as cancelling until the first renewal.** This
  row corrects new purchases and gives the button a real effect; it does not reach into existing
  records by itself.
- Item 5 also applies to a record cancelled before the date column existed (mark false, no date,
  really cancelled): a repeat cancel now answers "will not renew" and records TODAY as the date.
  The sentence is true; the date is the day of the repeat, as the webhook's own backfill does.
- Item 2 trusts Stripe's answer. If Stripe's answer were wrong or stale, a really cancelled record
  would be set back to renewing locally; Stripe would still cancel it, and the next "updated"
  message would set the mark false again.
- The web page's code is not read. The order of Stripe's messages on a purchase is not read.
- Stripe is a stand-in in every test. No Stripe call was or will be made by the team.

## For the live line (origin/main 9c21bee8) [R, and one dry run on copies]

Cures 1 and 2 apply to main's files **as they are**: the same functions, the same text, other line
numbers. Shown by a dry run of this row's production patch (`git diff b60007c2 f29bc45f`, the three
files) against copies of main's three files in a scratch folder outside the repositories: every
hunk applied, with offsets only (services.py four hunks, stripe_service.py two, views.py three).
Nothing was changed anywhere by that; it is not a test run of main.
**The smallest change that fixes main** is therefore that one patch of three files (85 lines added,
1 removed). Smaller still, if the founder wants the least possible: the four hunks of
`billing/services.py` alone (cure 1, 16 lines) stop every NEW purchase from being affected; without
cure 2 the customers already affected keep the banner until their first renewal.
Not checked for main: whether this row's test module runs there unchanged (it needs the routes'
names and the sign-up signal as they are on beta), and nothing of main was run.

## Tests

`billing/tests/test_converted_trial_is_not_cancelling.py`, 16 tests, committed first (eb5719c3).
Records are made by production code: a teacher is created, the sign-up signal starts the automatic
trial (asserted in setUp: a trial, mark false), and the purchase is the Checkout-completed message
given to `StripeWebhookHandler.handle_checkout_completed`; each purchase asserts it is the same
record, no longer a trial, with Stripe's id. "A record converted before the fix" is such a record
with the mark put back to false and no date, asserted to read as pending before the request.

- **The purchase (4):** set to renew; the page is told nothing is pending; an annual plan; a stray
  date on the trial is cleared.
- **A trial that Stripe converts (2).**
- **Keep subscription (6):** right after the purchase the answer does not contradict itself (what
  the user did); a record converted before the fix is corrected, Stripe asked once and told
  nothing; a stale date goes with it; no guess when Stripe's answer omits the flag; a real
  cancellation is still undone at Stripe; a trial is never corrected into a renewing plan.
- **The shared core (2):** the result reports a local correction and no change at Stripe; choosing
  another plan corrects the record and claims no undone cancellation.
- **Cancel on such a record (2):** says what it did and records when; a second cancel still says
  "already" and keeps the date.

## Written before the runs

Gate script `~/Documents/Projects/GAP-ed-scripts/run_h174_gate.sh`; its base argument is d7143538.

**0. Reproduce-first** (the new module and the class `SubscriptionReactivationServiceTestCase` of
the older module, on the three production files as at the base): **Ran 24, FAILED, 14 distinct
tests red**, by name in the script: the four purchase tests; the two Stripe-conversion tests; of
"keep": right after the purchase, converted before the fix, a stale date; both shared-core tests;
"cancel says what it did"; and the two reversed older tests (local record corrected; past due).
Green there, as they must be: no guess when Stripe does not say; a real cancellation still undone;
a trial never corrected; a second cancel says already (the four that hold what must NOT change),
and the other six tests of the older class. The script halts if the count or the set differs.

**1a.** `makemigrations --check`: no changes.

**1. Modules and guards at the tip:** OK. No count written. Thirteen billing modules that touch
trials, conversions, cancel, resume, renewal and the Checkout handler, and the repo-wide guards.
Rule 20: no serializer's code changes; `AutoGrader.tests_cache_bespoke_1114` is in the list.

**2. Mutants: 20**, each KILLED with at least the tests `mutate.py` names (`--check` passes). Inner
runs are the new module and three older modules (reactivation, cancel, cancellation visibility).
Every one of the 16 new tests is named by at least one mutant. Because the three older modules
hold many tests I have read only in part, a mutant may fail MORE tests than named; the results
will say for each whether the set is exact.

| Mutant | Must fail |
|---|---|
| A1 the purchase does not set auto_renew; A2 sets it but does not save it | the three purchase tests, the stray date, "right after the purchase" |
| A3 the purchase keeps a stray date; A4 clears it but does not save it | the stray date |
| B1, B2 the same two for the Stripe conversion's auto_renew | Stripe conversion: set to renew |
| B3, B4 the same two for its date | Stripe conversion: stray date |
| R1 "keep" never corrects; R4 clears the date but leaves auto_renew | converted before the fix; stale date; both shared-core tests; the two reversed older tests |
| R2 "keep" corrects when Stripe does not say | no guess |
| R3 "keep" corrects a trial | a trial is never corrected |
| R5 "keep" leaves the stale date | stale date |
| R6 choosing a plan claims an undone cancellation | choosing another plan |
| V1 "keep" says "resumed normally" for a corrected record | converted before the fix |
| V2 "keep" says "corrected" for a real resume | a real cancellation still undone; the older "successful resume" |
| V3 cancel treats an unrecorded cancellation as already done; V4 does not record when | cancel says what it did |
| V5 cancel forgets a recorded cancellation | second cancel; three older cancel tests (already; twice in a row; the date does not slide) |
| V6 cancel fabricates a date for a trial | the older "cancelling a trial does not fabricate a date" |

Rule 19 as it should stand after these runs: 12 of the 16 new tests red in step 0; the other four
red under R2, V2, R3 and V5. One older test added in b60007c2 (`noop when Stripe and the local
record agree`) is named by no mutant and will not have been seen red; I do not count it.

**3. Regression** (the billing app, serial; own grant): OK.

## Not done

Nothing run. The frontend not read. No record, database, log or Stripe account looked at.

## Results

### The gate at e92c7ea1 (0b's GRANT, 2026-10-07 18:13:06 WAT)

e92c7ea1 is eb5719c3 (tests first), b60007c2 (tests only: three older tests reversed), f29bc45f (the
change) and the docs commit. One run of `run_h174_gate.sh e92c7ea1 1 d7143538` (script sha256 starts
6a05dcd880251264; `mutate.py` starts 0f053bf17007bd5b), 18:13:36 to 18:18:40, script exit 0.
One-minute load 3.08 at the start, 4.75 at the start of part 1, 2.58 at its end, 2.38 at the end.
Not stopped, not repeated.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0. Reproduce-first, on the base's three production files | Ran 24, FAILED, 14 distinct tests red by name | **Ran 24 tests in 1.720s, FAILED (failures=14)**: 14 lines, 14 distinct tests; the script's own comparison with the 14 written names: "step 0 is as written" | `prefix_base_production_failing_e92c7ea1.txt.gz` |
| 1a. makemigrations --check | no changes | "No changes detected" | `makemigrations_check_e92c7ea1.txt` |
| 1. Modules and guards at the tip | OK | **Ran 581 tests in 101.885s, OK** (no skip) | `modules_and_guards_e92c7ea1.txt.gz` |
| 2. Mutants | 20 KILLED with at least their named tests | **20 of 20 KILLED**: each exit 1, its own "Ran 113", `expected-but-passed []` twenty times; SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN empty. Source clean after. | `mutation_log_e92c7ea1.txt`, `mutation_results_e92c7ea1.json`, `mutant_logs_e92c7ea1/` |

Nothing differed from what was written before the run. The console is `gate_console_e92c7ea1.txt.gz`.

**Every failing set is EXACTLY the written one**, compared by program from the results file: for all
20 mutants the sorted expected list equals the list of failing tests (sizes 5, 5, 1, 1, 1, 1, 1, 1,
6, 1, 1, 6, 1, 1, 1, 2, 1, 1, 4, 1). I had said a mutant might fail more older tests than I named;
none did.

**What the user saw is now shown by a run, on the old code:** in part 0,
`test_right_after_the_purchase_the_answer_does_not_contradict_itself` is red: a teacher's automatic
trial, the Checkout-completed message, then "keep" with Stripe saying "not cancelling" answers
"already_active" around a subscription that reads as a pending cancellation. And the purchase
tests are red there: the paid record's mark is false and the page is told a cancellation is pending.

**Rule 19, counted from these records:** 12 of the 16 new tests red in part 0, with the two reversed
older tests. Under the mutants 23 distinct tests failed, all 16 new ones among them; so the four
that are green on the old code were seen red (no guess under R2; a real cancellation under V2; a
trial under R3; a second cancel under V5). **Never seen red:** the older-module test added in
b60007c2, `test_noop_when_stripe_and_the_local_record_agree`; not counted.

**Beside the run, as told to me by the Hardening Engineer:** one test process of its own (one module
of assignments, its own test database, 6G cap) ran from 18:16:05 to 18:16:27, against the Release
Engineer's hold, and was stopped. By this run's console that is inside the mutants step (begun
18:16:03), two seconds after the modules step had ended. Every mutant is killed as written.
Verifier 2's one-minute run, granted beside this gate by the Release Engineer, ran 18:13:42 to
18:14:24 (the Release Engineer's times): during part 0 and the first seconds of part 1.

**What these runs do not show:** anything at Stripe, on a service, in a database or on the web page;
the order of Stripe's messages; that the production patch works on main (only that it applies to
copies of main's files).

Credential-pattern check before this commit (every new file, before gzip, the wide URL pattern and
the name-and-value pattern, masked): no line matches in any of them.

### The three older tests reversed in b60007c2, one by one (Senior Manager's word; written after the gate was requested, describing a commit made before any run)

So that each reversal can be judged on its own. All three are in the tests-only commit b60007c2; the
lines quoted are that commit's own removed and added lines.

**1. `billing/tests/test_subscription_reactivation.py`, `SubscriptionReactivationServiceTestCase`:
`test_noop_when_not_cancelling` became `test_local_record_is_corrected_when_stripe_is_not_cancelling`.**
- Fixture (unchanged): a paid record with `auto_renew=False`, a Stripe id; the stand-in for Stripe
  answers `status="active", cancel_at_period_end=False`.
- Old assertions: `assertFalse(result.changed)` and, after a re-read, `assertFalse(self.sub.auto_renew)`.
- New assertions: `assertTrue(result.changed)`, `assertTrue(result.local_changed)`,
  `assertFalse(result.stripe_changed)`, and after a re-read `assertTrue(self.sub.auto_renew)`. Kept
  as they were: no warning, and Stripe's `modify` is not called. Added first: the record's mark IS
  false before the call (the deciding value).
- Why: the old test wrote down fault 2 as the intended behaviour: a record that says "not renewing"
  while Stripe says it is not scheduled to cancel was to be left alone. The ruling is the opposite:
  Stripe is the source of truth and the record is corrected.
- What the old test also protected, and where that went: "nothing happens when there is nothing to
  do". A new test beside it, `test_noop_when_stripe_and_the_local_record_agree` (the same fixture
  with the mark true), holds that: `assertFalse(result.changed)`, no warning, no `modify`. It is
  green on the old code and named by no mutant, so it has not been seen red; I do not count it.

**2. The same class, `test_surfaces_past_due_warning` (name unchanged).**
- Fixture (unchanged): the same record, mark false; Stripe answers `status="past_due",
  cancel_at_period_end=False`.
- Old assertion: `assertFalse(result.changed)`. New: `assertFalse(result.stripe_changed)` and
  `assertTrue(result.local_changed)`. Unchanged: exactly one warning, about the outstanding payment.
- Why: what the test is about, the past-due warning, is untouched. Its side assertion "nothing
  changed" was true only because of fault 2; with the same fixture the record is now corrected,
  and nothing changes at Stripe.

**3. `billing/tests/test_subscription_cancel.py`, `CancelIdempotencyTests`,
`test_already_not_renewing_reports_so` (name and assertions unchanged; the FIXTURE changed).**
- Old fixture: `self._make_sub(auto_renew=False)`: a paid record with the mark false and NO date of
  cancellation. New: `self._make_sub(auto_renew=False, cancelled_at=timezone.now())`.
- Assertions, unchanged: 200, status "cancelled", and "already set to not renew" in the message.
- Why: the old fixture is, field for field, a trial converted before this row: mark false, no
  date, never cancelled by anyone. Item 5 of "What changes" answers such a record "will not renew"
  and records the date, because for it the request IS the cancellation. A record that really was
  cancelled through this request carries a date, so the test's meaning ("a second cancel says
  already") is kept by giving the fixture the date a real cancellation leaves.
- What this reversal costs, said plainly: a record that was really cancelled BEFORE the date column
  existed has the old fixture's shape too. For it a repeat cancel now answers "will not renew" and
  records today's date. That is the side effect the Senior Manager accepted; the same class's
  `test_already_not_renewing_still_reasserts_on_stripe` (fixture unchanged, mark false and no date)
  still passes: Stripe is told to cancel in both cases.

Written 18:19 WAT. Still owed as this is committed: the regression (the billing app), on its own
grant.
