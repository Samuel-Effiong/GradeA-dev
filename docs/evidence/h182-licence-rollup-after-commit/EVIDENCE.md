# H-182 (LOCK-2): the school licence's consumption rollup is applied after the charge commits

Branch `task/h182-licence-rollup-after-commit`, stacked on `task/h181-wallet-lock-first` (41a92162; beta 035e0a07 underneath). Written by ed (Security Engineer),
2026-10-08. Severity MEDIUM (Senior Manager). Design: ~/Documents/Projects/GAP-ed-scripts/lock2/DESIGN_NOTE_lock2.md (ruling: on_commit, **no outbox, no migration**).
**Nothing has been run on this branch yet.** Marks: READ = read in the code; NOT RUN = reasoning, no run shows it.

## The fault and the change (READ; NOT RUN)

`CreditWallet.consume_credits` locked the wallet and buckets and then UPDATEd the school's `LicenseSubscription` row (`_record_license_consumption`); `refund_credits`
did the same at its end. The licence paths take the licence row first and then write wallets, so a charge and `_grant_overage_blocks` (or a licence renewal) could deadlock.
Now `billing/licence_rollup.py` `roll_up_after_commit(licence_id, delta)` registers the same F() update (clamped at zero for a refund) with `transaction.on_commit`; a
callback that fails is logged at ERROR with the licence id, the amount and the error's CLASS NAME only (no name, no address, no exception text, no traceback: a database error can quote what it was given) and never raised. A test pins the exact message and that an address in the error's text and the teacher's own address are absent (Senior Manager's condition, 2026-10-08). The allocation lookup stays inside the charge (as before); only the write moves.
The rule is now: licence row, then wallets, then buckets, on the charge side as well. The only reader of the figure caps a newly enrolled teacher's first-month grant
(`_enroll_teacher_internal`, license_service.py:1475): it never refuses or bills.

## The counter the ruling asked for (a deviation, stated)

The ruling said a failed callback is "logged and counted (a metric)". **Beta has no metric counter** (the `audit_metrics` counter exists only on the next-stage line;
grep of beta finds none). So the failure is an ERROR log line (`billing.licence_rollup`) with a stable text; the project's logging reaches Sentry as an error event
(`LoggingIntegration(event_level="ERROR")`, main and beta), so Sentry's event count is the count. Nothing is counted in a dashboard. A real metric is a later row.

## Limit, said plainly

A process that dies between the commit and the callback loses that one increment, silently. The figure is the licence's per-window total; a lost increment makes the school
look like it consumed slightly less, so a teacher enrolled later in that window may be granted slightly more than the budget (by at most part of one allocation). The exact-once
version is the Next-stage Builder's proposal for Epic B (a separate small table). Also: the callback is a separate statement after the commit; it waits for the licence row if a licence
renewal holds it (it holds nothing else meanwhile), so a charge's request can be slower during a renewal; the wait is not new (the charge waited for the licence row before too),
but it is now after the charge's own commit.

## Callers that wrap the charge, and the timing (READ; NOT RUN)

`consume_credits` has ONE production caller: `ai_processor/services.py` `execute_graded_task` (:4766 on 035e0a07), inside its own `with transaction.atomic():` (:4763), which is the
outermost transaction for the grading pipeline (the pipeline deliberately does not wrap a run in one transaction; billing/refunds.py). The callback fires right after that block commits.
`refund_credits` has one caller, `billing/refunds.py:94`, and opens its own `transaction.atomic()`; the callback fires after it commits. I searched every call of the form
`ai_processor.<method>(` outside ai_processor (14, in assignments, classrooms, dashboard, students and billing): exactly ONE is inside an outer `atomic` block, `billing/views.py`
(the super-admin custom AI prompt, ~:2692-2715). There the callback fires at that outer block's commit (later, never earlier), and an exception rolls the block back, registering
nothing. That caller is a SUPER_ADMIN, who has no school-licence seat, so no rollup is registered at all. So the timing is right for each. Not read: calls made through names other
than the `ai_processor` singleton (a direct `AIProcessor(...)`); none found by grep of `AIProcessor(` outside ai_processor is claimed here.

## Older tests changed (named; assertions unchanged)

The roll-up callback never runs inside an ordinary TestCase, so each charge or refund followed by a read of `total_credits_consumed` is wrapped in
`self.captureOnCommitCallbacks(execute=True)`:
- `billing/tests/test_license_consumption_accounting.py`: all six `consume_credits` calls and the two `refund_credits` calls (tests: consumption increments the counter; refund reverses it; refund
  clamps at zero; admin analytics allocation excluded; individual teacher untouched). Without the wrapper the last two would pass vacuously (they assert the figure stays 0).
- `billing/tests/test_license_multi_month_budget.py`: the two `consume_credits` calls (`_consume_whole_monthly_pool`, and the one in the idempotent-refresh test).
Commit 4b407bbd; tests only.

## Written expectations, before any run

- **Step 0 (reproduce-first)**: `models.py` and `services.py` as at 41a92162 (before the change; `licence_rollup.py` present and unused) under the new module: **Ran 7, 4 red**: `test_the_figure_is_not_touched_inside_the_charge`,
  `test_a_refund_takes_it_back_after_commit`, `test_a_failed_roll_up_is_logged_with_its_licence_and_amount_not_raised` (the inline update raises out of the charge), and the thread test
  `test_a_charge_racing_a_licence_overage_grant_does_not_deadlock` (the charge is the deadlock victim: it started waiting first). **Green on the old code, by design (they hold results, not timing):**
  `test_a_charge_rolls_up_what_it_charged_once_it_commits`, `test_a_charge_that_rolls_back_rolls_up_nothing`, `test_a_refund_never_takes_the_figure_below_zero`. These three are NOT counted as seen red;
  the first and third are seen red by mutants (N2 and N3/N8 below); **the rolled-back-charge test is isolated by no mutant** (the inline update rolled back with the charge too), so it is a guard, not a proof.
  A difference from this list (a count, a name) stops the gate.
- **Step 1**: `makemigrations --check` no changes; the new module + `test_license_consumption_accounting`, `test_license_multi_month_budget`, `test_credit_refund`, `test_concurrent_credit_operations`,
  `test_execute_graded_task`, `ai_processor.tests_grading_pipeline` and the repo-wide guard modules: OK, no FAIL or ERROR line. The Ran count is reported.
- **Step 2, mutants (12)**, each fails the tests named, exactly them: N1 (charge writes the licence inline): the not-touched test, the failed-roll-up test, the thread test; N2 (charge registers no roll-up): the charge result test, the not-touched test, the refund test,
  the failed-roll-up test, the thread test; N3 (refund registers none): the refund test and the clamp test; N4 (refund writes inline): the refund test; N5 (roll-up runs at once, not after commit): the not-touched test, the refund test, the thread test;
  N6 (a failed roll-up is raised): the failed-roll-up test; N7 (not logged as an error): the failed-roll-up test; N8 (no clamp): the clamp test; N9 (adds nothing): the charge result, not-touched, refund and thread tests; N10 (the log line carries the error's text): the failed-roll-up test; **N11 (the charge registers its roll-up TWICE)**: the charge result test (the figure moves by twice the charge), the not-touched test (two roll-ups, not one), the refund test (its setup charge), the failed-roll-up test (two log lines, not one) and the thread test (final figure); **N12 (the refund registers its roll-up twice)**: the refund test (two roll-ups, not one). The clamp test and the older refund tests cannot see a doubled refund (the clamp at zero hides it); N12 is seen by the count of registered roll-ups only (Release Engineer's condition, 2026-10-08).
- **Step 3**: the billing app, one serial run: OK. Rule 20 (the cache payload test) is not needed: no answer or serializer changes.
- Nothing is re-run without the Release Engineer's word; a difference is reported, not repaired in place.

## Not shown by any run so far

Everything: nothing has been run. And, even when run: behaviour under pgbouncer in transaction mode, the role's lock timeout, a real process death between commit and callback, a real licence renewal of a large school.

## RESULTS OF THE FIRST GATE (step 1 at 9c131ea1; run_h182_gate.sh 91738c86219b352d; started 12:57:43, ended 13:00:15 WAT, 2026-10-08): STOPPED, my tests were wrong

Console `gate_console_step1_9c131ea1.txt.gz` (raw sha256 starts d2d2de0828836571; rc=2). Whole script under ONE `systemd-inhibit`. Load at start 3.55 4.11 4.23 (the pre-start check, alone, read 4.63; I waited to 3.83 and started in a separate command).
Raw files kept as they are, named by the tip: `modules_and_guards_9c131ea1.txt.gz` (raw sha f9c6779e088bbea4), `prefix_base_production_failing_9c131ea1.txt.gz` (raw sha 71ee614823b13e18), `makemigrations_check_9c131ea1.txt`. Credential patterns: 0 lines.
- **Step 0 as written**: Ran 7, the four written red (not-touched-inside-the-charge, refund-after-commit, failed-roll-up-logged, the thread test), the other three green by design.
  The thread test fails on the old code with a real Postgres deadlock (the charge is the victim), as for H-181.
- **makemigrations**: no changes.
- **Step 1: RED. Ran 415, FAILED (failures=2)**: `test_the_figure_is_not_touched_inside_the_charge` and `test_a_refund_takes_it_back_after_commit`, both `AssertionError: 2 != 1 : exactly one callback was registered`. The script stopped (`STOP: not green`) and did NOT run the mutants.
  The other 413 passed, including the thread test, the failed-roll-up test with its exact fields, and the two older modules whose charges were wrapped.
- **Cause (from the raw log): my tests, not the change.** `captureOnCommitCallbacks` records EVERY after-commit callback of the charge or the refund, and the code under it registers another, unrelated one (a cache or analytics hook); I asserted the list held exactly one.
  The change registers exactly one roll-up per charge and per refund (not counted by the tests until now).
- **Fix** (tests only; the production code is unchanged): the two tests count only the licence roll-up callbacks (by the function `roll_up_after_commit` registers) and still require exactly one. The rolled-back-charge test keeps asserting that NO callback at all remains.
- **Written expectations for the next run are the ones above, unchanged** (step 0 Ran 7 and the same four red; the same ten mutants N1-N10 with the same sets; N5 and N2 now fail the not-touched test by `0 != 1` roll-ups instead of by the figure).

## RESULTS OF THE FRESH GATE (step 1 at 5523aa55; run_h182_gate.sh eb3dd754065e0dae; started 13:24:12, ended 13:28:04 WAT, 2026-10-08): GREEN AS WRITTEN

Console `gate_console_step1_5523aa55.txt.gz` (raw sha256 starts 063f833d3fe5cc7f; rc=0). Whole script under ONE `systemd-inhibit`. Load at start 3.40 5.08 4.75 (the pre-start check, alone, read 6.07 with another project's tests on the machine; I waited to 3.83 and started in a separate command).
Raw files named by the tip: `modules_and_guards_5523aa55.txt.gz` (raw sha d7eb5f81434cd0a7), `prefix_base_production_failing_5523aa55.txt.gz` (raw sha 33081a127528130b), `mutation_log_5523aa55.txt`, `mutation_results_5523aa55.json`, `mutant_logs_5523aa55/`, `makemigrations_check_5523aa55.txt`. The first gate's files (…_9c131ea1) are kept beside them. Credential patterns: 0 lines.
- **Step 0 as written**: Ran 7, the four written red (the not-touched test, the refund-after-commit test, the failed-roll-up test, the thread test), the three green by design.
- **makemigrations --check**: no changes.
- **Step 1 modules and guards**: Ran 415, OK, 0 FAIL or ERROR. (The two tests that failed at 9c131ea1 now pass: they count only the roll-up callbacks.)
- **Step 2 mutants: KILLED 12 of 12**; SURVIVED [], KILLED_NOT_AS_EXPECTED [], BROKEN []; every inner run Ran 24; source clean after; database dropped. **The two mutants the Release Engineer asked for are killed:** N11 (the charge registers its roll-up twice) fails the five written tests and five older tests; N12 (the refund registers twice) fails exactly the refund test.
  **Every written test failed under its mutant.** Where a mutant failed MORE than was written, the extra tests are the older ones that read the figure: N2, N9, N11 five older tests each (consumption increments, contract renewal resets, monthly refresh resets, refresh idempotent, and the like); N3 two (refund reverses, refund clamps); N8 one (refund clamps). N1, N4, N5, N6, N7, N10 and N12 failed exactly the written sets. So the older tests whose charges were wrapped really guard the figure.
- Not yet run: step 3 (the billing app). **The rolled-back-charge test is still isolated by no mutant** (a guard, not a proof). Not shown: a real process death between commit and callback; behaviour under pgbouncer; any caller outside the ones named above.
