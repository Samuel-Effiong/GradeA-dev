# H-181 (LOCK-1): the wallet lock first, in the six individual-plan functions and the licence rollover helper

Branch `task/h181-wallet-lock-first`, base beta 035e0a07. Written by ed (Security Engineer), 2026-10-08. Severity MEDIUM (Senior Manager).
The design and the reading are in DESIGN.md (same folder). **Nothing has been run on this branch yet** (the Release Engineer's quiet period; a test counts
only when seen red on the old code, rule 19). Marks: READ = read in the code; NOT RUN = reasoning, no run shows it.

## What this row changes (READ)

- New `billing/locks.py`: `lock_wallet_first(wallet)` takes the wallet row FOR UPDATE and returns the locked instance (inside an open transaction).
- billing/services.py: one call before the first bucket lock in `activate_subscription`, `apply_immediate_plan_change`, `process_mid_cycle_credit_grant`,
  `process_rollover_and_renewal`, `finalize_trial_conversion_via_stripe`, `finalize_trial_to_paid_conversion`.
- billing/license_service.py: one call at the top of `_rollover_and_grant_monthly_bucket`, the helper shared by `process_license_renewal`,
  `process_offline_renewal` and `_refresh_teacher_credits` (Senior Manager's ruling 2026-10-08: the licence side goes in the same row).
- No migration, no model, serializer, URL or answer change.

## The order each caller now takes (READ; NOT RUN)

The rule: LICENCE row, then WALLET rows, then BUCKET rows.

| Caller | Before the call | Now |
|---|---|---|
| `process_license_renewal` (:1853) | licence row FOR UPDATE (:1870); no bucket lock | licence, then per teacher (savepoint): wallet (helper), bucket (helper), allocation save, `wallet.save` |
| `process_offline_renewal` (:3874) | licence row FOR UPDATE; no bucket lock | the same |
| `_refresh_teacher_credits` (:3756), called by the monthly refresh task | allocation row FOR UPDATE (tasks.py), then the conditional UPDATE of the licence row (the consumption window); no bucket lock | allocation, licence, wallet (helper), bucket (helper) |
| six individual-plan functions | the user-subscription row (where taken); no bucket lock | subscription, wallet, bucket (no licence row involved) |

**No caller of the helper holds a bucket lock before calling it** (read to the end: the three callers above are the only ones; `grep` of `billing/*.py` finds no other).
`_enroll_teacher_internal` (:1342) is NOT among them and is NOT changed: it reads the teacher's monthly bucket without `select_for_update`
(license_service.py ~:1498) and writes buckets and `wallet.save` afterwards, so it holds no bucket lock for a charge to wait on; no cycle by this route
(READ). It does compute a rollover from that unlocked read; a charge landing between the read and the retirement is counted in the carry-over
and spent too. That is a separate, smaller fault and not part of this row (to be logged as its own LOW row if the Senior Manager wants it).

## What the wallet lock adds to H-183 (the licence renewal holds the licence row across every teacher) (READ; NOT RUN)

`process_license_renewal` and `process_offline_renewal` are one transaction holding the licence row for the whole loop. Locks taken in a per-teacher
savepoint are not released when the savepoint ends: they are held until the outer commit. Before this row each step already ended with
`wallet.save` (an UPDATE of the wallet row), so the teacher's wallet row was already locked until the end of the whole renewal; and the bucket lock
(`select_for_update` in the helper) too. So **for these two the wait moved earlier within each teacher's step (the wallet lock is taken at the step's
start, not at its end: milliseconds) and did not grow: it still ends at the outer commit.** For the monthly refresh (`_refresh_teacher_credits`)
there was no `wallet.save`: the wallet lock is NEW there, held from the helper's start to the commit of that one teacher's short transaction (the task commits
per allocation). So the only added wait is a charge by that one teacher during that one teacher's refresh. H-183 (shorten the licence hold) is unchanged
by this row. Whether a charge landing during a licence renewal still deadlocks with the renewal through the LICENCE row is H-182's question (LOCK-2), not this row's.

## Written expectations, before any run

- **Step 0 (reproduce-first)**, the base's `billing/services.py` and `billing/license_service.py` put under the new module (`billing/locks.py` is present but unused): **Ran 9, all nine tests red**:
  `WalletLockFirstTests` (six: activate_subscription, apply_immediate_plan_change, process_mid_cycle_credit_grant, process_rollover_and_renewal,
  finalize_trial_conversion_via_stripe, finalize_trial_to_paid_conversion) and `LicenceWalletLockFirstTests` (three: process_license_renewal,
  process_offline_renewal, test_refresh_teacher_credits). Each is red with a deadlock error (or the lock-timeout error) in the worker `errors`. On the old code
  the victim should be the CHARGE (it started waiting first, so its deadlock check fires first): "the charge and the function deadlocked". If any is green on the
  old code, the gate stops and I report which: that function does not deadlock under this fixture, or the fixture does not reach its lock.
  An unexpected red in a different form (an error before the hook, a fixture error) is also a stop.
- **Step 1**: `makemigrations --check` no changes; the new module (Ran 9, OK), the related modules named in the script (the modules that call these functions,
  and the two concurrent-credit modules) and the repo-wide guard modules (the current list in the script): OK, no FAIL or ERROR line. The Ran count is reported.
- **Step 2, mutants (10)**, each fails the tests named, **exactly** them: M1-M6 remove the call in one of the six functions (its own test); M7 removes it in
  the licence helper (the three licence tests); M8 makes the helper lock nothing (all nine); M9 moves the call below the bucket lock in the licence helper
  (the three licence tests); M10 moves it below the bucket lock in `process_rollover_and_renewal` (its test). SURVIVED, BROKEN, KILLED_NOT_AS_EXPECTED each empty.
- **Step 3**: the billing app, one serial run: OK. Rule 20 (the cache payload test) is not needed: no answer or serializer changes; the cache module is not run.
- Nothing is re-run without the Release Engineer's word; a difference is reported, not repaired in place.

## Limits, said plainly

- The foreign-key wait at commit is Postgres behaviour; the tests show it or they do not. A passing run shows the order holds for these nine paths under
  this forced interleaving, not that no other path deadlocks.
- The licence-row inversion with a charge (a charge holds the wallet and waits for the licence row that a renewal holds) is H-182's, not this row's.
- The bucket-first order may exist in code I did not read (paths outside billing/services.py and billing/license_service.py): the grep for
  `buckets.select_for_update` found those two files only (with `billing/disputes.py`, `payment_refunds.py` and `credit_reversal.py`, which already take the wallet first).

## RESULTS OF THE FIRST GATE (step 1 at 41a92162; run_h181_gate.sh 62bc8b8c2fdb35c8; started 12:44:30, ended 12:49:52 WAT, 2026-10-08)

Console `gate_console_step1_41a92162.txt.gz` (raw sha256 starts 1fab63bbd1d13dc7; rc=0). Whole script under ONE `systemd-inhibit`. Load at start 3.57 5.25 5.14 (waited from 4.45 to 3.62 before starting).
Raw files kept as they are, with the tip in their names: `modules_and_guards_41a92162.txt.gz` (raw sha 77954fac835749e6), `prefix_base_production_failing_41a92162.txt.gz` (raw sha 6acc9093d046cca4),
`mutation_log_41a92162.txt`, `mutation_results_41a92162.json`, `mutant_logs_41a92162/`. Credential patterns on all of them: 0 lines.

- **Step 0 (reproduce-first) as written**: Ran 9, FAILED (failures=9), the nine distinct red = the written nine. **Every one is a real Postgres deadlock**: the worker `errors` hold
  `OperationalError('deadlock detected ...')` nine times (counted in the raw log), not a lock timeout. So the fault is shown, not only reasoned: on the old code a charge racing each of the nine
  functions deadlocks, and the charge is the one Postgres aborts (it started waiting first), as written.
- **makemigrations --check**: no changes.
- **Step 1 modules and guards**: Ran 589, OK, 0 FAIL or ERROR lines.
- **Step 2 mutants: KILLED 8 of 10, and TWO SURVIVED** (the rule: a survivor is a place the tests do not guard). M1-M8 each failed exactly the written test(s) (M7 the three licence tests; M8 all nine); each inner run Ran 9.
  **M9 (the licence rollover helper takes the wallet lock AFTER its bucket query) and M10 (the same in `process_rollover_and_renewal`) SURVIVED**: Ran 9, failed 0.
  **Why (read from the harness, confirmed by the survival):** worker 0 stops at its first ledger row, which each function writes AFTER both locks are taken. With the lock moved below the bucket query,
  the wallet is still locked before that pause point, so the charge (which only starts at the pause) finds the wallet held and waits: no cycle. The window between the bucket lock and the late wallet lock
  (a few statements long) was never raced. That window is real in production (a charge landing inside it deadlocks) but narrow; the tests did not guard it.
- Nothing else was run. The step-3 grant was not given for this tip.

## THE CORRECTION (written before any run of it, 2026-10-08)

New tip adds two ORDER tests, and the harness a second pause point: worker 0 stops at the ENTRY of the wallet-lock helper instead of at the first ledger row (`race(..., before_wallet_lock_in=...)`).
With the call in its right place the pause holds no bucket lock, the charge runs to the end, and nothing deadlocks; with the call below the bucket query worker 0 holds the bucket at the pause,
the charge takes the wallet and waits for that bucket, and the helper's own wallet lock then deadlocks (the charge, which waited first, is the victim).
- `WalletLockFirstTests.test_process_rollover_and_renewal_locks_the_wallet_before_the_bucket`, and
- `LicenceWalletLockFirstTests.test_the_licence_rollover_helper_locks_the_wallet_before_the_bucket` (the helper is called DIRECTLY: its three callers hold or write the licence row, which a charge also writes, and that other cycle is H-182's; through them this test would deadlock for the wrong reason).

**Written expectations for the new run, before it:**
- **Step 0**: Ran 11, ELEVEN red: the first nine as before (deadlock), and the two new tests red because the base code never calls the wallet-lock helper (the pause point is never reached; each fails on `the hook was not reached once` / the other worker's `never reached its hook`).
- **Step 1**: the new module Ran 11, OK; everything else as before (589 plus the two).
- **Mutants (10, same list)**: M1-M3, M5, M6 as before (their own test only). **M4** fails `test_process_rollover_and_renewal` and the new rollover order test (the helper is never reached). **M7** fails the three licence tests and the new helper order test. **M8** (the helper locks nothing)
  fails the NINE as before, NOT the two order tests (a pause before a no-op lock holds nothing, the charge completes first): said so that it is not read as a gap. **M9** fails exactly `test_the_licence_rollover_helper_locks_the_wallet_before_the_bucket`; **M10** fails exactly `test_process_rollover_and_renewal_locks_the_wallet_before_the_bucket`.
  If M9 or M10 survives again I stop and report.
