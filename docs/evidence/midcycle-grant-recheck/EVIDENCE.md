# Overlapping nightly runs: re-check under the row lock

**Branch:** `task/midcycle-grant-recheck`, off beta `abeda10`. **Author:** Hardening (d5). **Verifier:** 1a. It's beta-line, for bundle 5 (or bundle 4 if the founder folds fixes in; it's F6 item 3).

The SM split this out of H-65 (the beat-task lock) and ordered it first, because it's a live money bug and smaller than H-65. H-65's lock stops overlapping runs; these re-checks are the defence in depth for when the lock expires or fails, and they also close a race that no lock can: the Stripe webhook.

## The defects

Each nightly task reads its due rows **without** a lock, then works through them. The per-row service takes a row lock, but a lock alone doesn't help: a second run waits on it while the first commits, then carries on with the copy it read before.

1. **`process_mid_cycle_credit_grant` (annual plans) granted the same month twice.** It locked the subscription but didn't re-check `next_credit_grant_at` afterwards. With two overlapping `process_annual_plan_credit_grants` runs, the second retired the bucket the first had just granted, rolled part of it over as an **unearned CARRY_OVER bucket**, and granted the month again. The customer gains that carry-over (up to `carry_over_percent` of a month, capped by `carry_over_max`/`max_bank`; 0 on a plan with no carry-over), and the ledger shows an extra month's GRANT row. The license renewal and monthly refresh paths already re-check after their locks (`billing/tasks.py` ~275 and ~1013).
2. **`expire_trial` acted on the caller's stale copy, with no lock.**
   - Two overlapping `expire_active_trials` runs wrote a **second EXPIRE row**.
   - **Worse: a paying customer switched off.** When Stripe's trial-end payment (`finalize_trial_conversion_via_stripe`) or a mid-trial checkout (`finalize_trial_to_paid_conversion`) converted the trial to paid on the same row after the task had read it, the task expired its stale copy and saved `is_active=False` on the subscription the customer had just paid for. One run racing the webhook is enough; no overlap is needed. Trial end is exactly when both happen.
3. **`expire_trial` took an already-processed trial bucket.** The trial bucket expires at `trial_end`, so `cleanup_expired_credit_buckets` (05:00) often expires it first and writes an EXPIRE row; `expire_trial` then wrote a **second EXPIRE row** for the same remainder. That's ledger only (balances unaffected), with no overlap needed, so it's probably common in production.

Audited, no gap: `reconcile_subscription_prices` is detection only and writes nothing per row (a duplicate run duplicates only its log lines, which H-65's lock covers).

## The fix

- `process_mid_cycle_credit_grant`: under the row lock, re-check everything the task selected on (`is_active`, not `is_trial`, `next_credit_grant_at` set and due, `billing_cycle_end` not passed). If it's no longer due, log the ids and return `None`; nothing is written.
- `expire_trial`: after the existing guards on the caller's object (unchanged), lock and re-read the row. Unless it's still an active trial, log the ids and return `False`. Otherwise act on the **locked** row (so the has-it-ended guard reads fresh `trial_end` too) and return `True`. Take only an **unprocessed** trial bucket.
- The two tasks count the skips separately in their summaries: "N already granted by another run" and "N already expired or converted".

## Behaviour changes (4-point records)

**B1. `process_mid_cycle_credit_grant` on a subscription that's no longer due.**
1. Previous: it granted regardless (the only check was the plan interval), and returned the subscription.
2. New: it returns `None` and writes nothing, when under the lock the subscription is inactive, a trial, has no next grant, isn't due yet, or its cycle has ended.
3. Why it's correct: these are exactly the conditions `process_annual_plan_credit_grants` selected on; the only caller outside tests is that task. A due subscription is granted as before (the direct-call tests in `test_annual_mid_cycle_grants` and `test_subscription_cycle_integrity` call it at the due moment and are unchanged).
4. Tests: `MidCycleGrantOverlapTests` (all four), `MidCycleGrantRealLockTests`.

**B2. `expire_trial` on a row that's no longer an active trial by the time it's locked.**
1. Previous: it expired the stale copy: a second EXPIRE row, and after a conversion `is_active=False` on the paid subscription. It returned `None`.
2. New: it returns `False` and writes nothing. When it does expire the trial, it returns `True`. The guards on the caller's object (not a trial: `ValueError`; not ended: `ValueError`) are unchanged, and the not-ended guard now also applies to the locked row's `trial_end`.
3. Why it's correct: the only thing to expire is an active trial. A converted row belongs to the paying customer, and an already-expired row has already been written off. Callers ignored the old `None`; the task now uses the bool to count skips.
4. Tests: `TrialExpiryOverlapTests` (all six), `TrialConversionRealLockTests`.

**B3. `expire_trial` when the trial bucket is already processed.**
1. Previous: it wrote the remainder off again (a second EXPIRE row) and re-saved the bucket.
2. New: it leaves the bucket alone (already expired, and its EXPIRE row already written) and still deactivates the trial.
3. Why it's correct: `expire_bucket` (the 05:00 cleanup) already wrote the EXPIRE row for this same remainder, so a second one double-counts it. It matches `process_mid_cycle_credit_grant`'s bucket selector (`is_processed=False`).
4. Tests: `test_cleanup_first_does_not_write_a_second_expire_row`, `test_a_single_expiry_is_not_reported`.

## F6: read-only detection for the founder

Three queries: ids, counts, credit totals and timestamps only, no emails. Each one is run by the tests against the damage exactly as the pre-fix code wrote it (and, on the reproduce-first run, against the real pre-fix outcome), and a test checks that none of them contains a writing verb.

| File | Finds | The cost it reports |
|---|---|---|
| `detect_paid_subscriptions_switched_off.sql` (**most important**) | Subscriptions inactive now, with a PAID trial conversion charge at or before their last update, switched off inside the period they paid for, not cancelled at Stripe | Ids and timestamps, plus corroboration: the "Free trial expired" EXPIRE row after the conversion, a later subscription (they paid twice), a scheduled cancellation. **Review each row by hand before acting.** |
| `detect_double_midcycle_grants.sql` | Annual subscriptions with two mid-cycle grants less than 14 days apart (genuine grants are a month apart) | `extra_carry_over_credits_raw` (what the customer gained) and `extra_monthly_grant_credits_raw` (the ledger overstatement) |
| `detect_duplicate_trial_expiries.sql` | Trial buckets with more than one EXPIRE row | `overstated_expired_credits_raw` (ledger only) |

## Tests (`billing/tests/test_overlapping_run_rechecks.py`)

The overlap is driven deterministically: run B (the real task) reads its rows, then run A (the same task, start to finish) runs, then run B acts. Two threaded tests prove the same outcome against real Postgres row locks, and each asserts the blocking actually happened (seen in `pg_stat_activity`), so the race can't pass vacuously:

- `MidCycleGrantRealLockTests`: run A grants and holds its transaction until run B is seen blocked on the row lock.
- `TrialConversionRealLockTests`: the conversion goes through the real `invoice.payment_succeeded` handler (`_handle_individual_invoice_succeeded`) and holds its transaction until the expiry is seen blocked.

## Runs (every run under `systemd-run` MemoryMax=6G, MemorySwapMax=0, `nice -n 10`, `timeout`, with `EXEMPT_EMAIL_DOMAINS` empty)

| Run | Tree | Result | Log |
|---|---|---|---|
| **Reproduce-first, run 1:** the new module on the unchanged base (a disposable worktree with only the test and SQL files added, its own DB, dropped after) | `abeda10` | **21 tests, 18 failures.** The real defects, each caught by an F6 query placed as the test's first assertion: the grant overlap (a double grant found), the conversion race (a switched-off paid subscription found), the overlapping trial expiry and cleanup-first (duplicate EXPIRE rows found), and the threaded conversion race (the switched-off subscription found). The rest are the missing re-checks and the new return contract (`None`/`False`/`True`, the summaries). **Two were test bugs:** the read-only check matched "update" in an SQL comment, and the threaded grant test's lock-waiter poll read `pg_stat_activity` from inside its own transaction, where Postgres keeps one snapshot per transaction, so it never saw run B waiting. Both fixed (comments stripped; `pg_stat_clear_snapshot()` before each look). | `repro_abeda10_run1.log` |
| **Reproduce-first, run 2:** only the two corrected tests, same worktree | `abeda10` | **The threaded grant test fails on the real double grant:** 1 extra grant, 10,000 raw monthly and **5,000 raw unearned carry-over** (50% of the month, the fixture plan's carry-over). The read-only check passes. | `repro_abeda10_run2_corrected_tests.log` |
| The fix: the new module | `ae944e9` | **21 OK**, both threaded tests included | `fix_module.log` |
| **Mutation battery** (`run_mutants.py`): 17 mutants, one per guard, each running the new module in one disposable worktree with a sha256-checked restore (DB dropped after) | `ae944e9` | **17 of 17 killed.** The lock mutants G7 (grant) and T5 (expiry) are killed by the threaded real-lock tests; T4 (acting on the stale copy) by the extended-trial test; each condition by its own subtest | `mutation_battery_ae944e9.log`, `results.tsv`, `logs/` |
| **Owning-app regression (rule 15): `billing`**, plus the repo-wide guards present on beta (`AutoGrader.tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`) | `ae944e9` | **1,725 tests, OK** (139 s) | `app_billing_guards_ae944e9.log.gz` (gzipped: 2.5 MB raw, over the 500 KB hook limit) |

No model or migration change, so no other app reads changed fields. `audit.tests_history_guard` and `audit.tests_route_coverage` (addendum 2) exist only on the Phase 2 line; beta doesn't have the audit app.

## Noted, not changed

- On this beta base `expire_bucket` doesn't re-check `is_processed` after its lock either. That's the expire-bucket race, already fixed in bundle 4.
- The logger calls in the touched functions still pass `user.email` (pre-existing; the repo hook checks direct `.email` arguments and passed on these files). My new log lines carry ids only.

## Addendum: the production checkout path (1a's N2 and N3), test only, 6f8c536

1a found that the production conversion path is checkout during a trial (`checkout.session.completed` → `_handle_individual_checkout`, which locks the trial row and converts that same row). Only 1a's own probe killed a mutant that skips the re-check when `force=True` (the credits-exhausted branch). The SM asked for the fix's own suite to pin it.

- `test_a_checkout_conversion_in_between_is_not_undone` (ended-trial branch) and `test_a_checkout_conversion_is_not_undone_on_the_credits_path` (credits branch, `force=True`) drive the interleaving through the real checkout handler. They assert the subscription stays active and paid, and the F6 switched-off query stays empty.
- Mutant **T9** (1a's X1 shape: `if not force and not (...)`) joins the battery.
- N3: the lock-waiter check now matches a waiter querying `billing_usersubscription`, not any lock waiter in the test database.

| Run | Tree | Result | Log |
|---|---|---|---|
| The module | `6f8c536` | **23 OK** | `n2_module_6f8c536.log` |
| Mutant T9 alone (a disposable worktree, DB dropped after) | `6f8c536` | **KILLED** by `test_a_checkout_conversion_is_not_undone_on_the_credits_path`, restore verified | `n2_mutant_T9_6f8c536.log`, `logs/T9.log`, `results.tsv` |

No production code changed, so no regression (rule 15).
