# H-181 (LOCK-1): take the wallet row lock first in the six functions that lock buckets before they write the wallet

Design, written 2026-10-08 before any test or code. Base: beta 035e0a07. Reading only so far (git objects); nothing run.
Owner: ed. Severity: MEDIUM (Senior Manager). Marks: READ = read in the code; NOT RUN = reasoning, no run shows it.

## The fault (READ, NOT RUN)

`CreditWallet.consume_credits` (billing/models.py) takes the wallet row FOR UPDATE and then every consumable bucket FOR UPDATE.
Six functions in billing/services.py take bucket locks (`wallet.buckets.select_for_update()`) and only afterwards write the
wallet row (`wallet.save(...)`) or insert a bucket (the insert's foreign-key check takes a share lock on the wallet row, which
conflicts with FOR UPDATE; Django's keys are deferred to commit, so the wait lands at commit with the bucket locks still held):

| Function | Line (035e0a07) | Bucket lock, then |
|---|---|---|
| `activate_subscription` | 154 | trial/monthly bucket locks, `wallet.save(update_fields=["overage_blocks_used"])`, bucket insert |
| `apply_immediate_plan_change` | 454 | monthly bucket lock, carry-over and new bucket inserts |
| `process_mid_cycle_credit_grant` | 659 | monthly bucket lock, bucket inserts, `wallet.save` |
| `process_rollover_and_renewal` | 862 | monthly bucket lock, carry-over and new bucket inserts |
| `finalize_trial_conversion_via_stripe` | 1613 | trial bucket lock, `wallet.save`, bucket insert |
| `finalize_trial_to_paid_conversion` | 1750 | trial bucket lock, `wallet.save`, bucket insert |

A charge and one of these for the same user can deadlock: Postgres aborts one. Not in the list (read): `expire_trial` (bucket lock, ledger row,
no wallet write), `grant_overage_bucket` and `top_up_credits` (no bucket lock first). The refund side was fixed the same way before
(`billing/credit_reversal.py`: "WALLETS FIRST, THEN BUCKETS"; test `ConcurrentRefundTests`).

The rule (Senior Manager, 2026-10-07): LICENCE row, then WALLET rows (by id), then BUCKET rows. These six never touch a licence row.

## The cure

One helper, `billing/locks.py`: `lock_wallet_first(wallet)` -> takes `CreditWallet.objects.select_for_update().get(pk=wallet.pk)` and returns the locked
instance; called at the top of each function BEFORE its first bucket lock, inside the function's `@transaction.atomic`. The functions
use the returned instance afterwards (so a stale in-memory wallet is replaced by the locked read). Proposed name and place, to be told
to the Next-stage Builder when fixed. Nothing else changes: no migration, no serializer, no answer.

## The test (real threads; one per function; seen red on the old code)

`TransactionTestCase` (a plain TestCase would hide the lock contention), `AutoGrader.testing.concurrency.run_concurrently` for the group deadline
and connection hygiene, two workers released together by a barrier:
- worker B runs the function under test; a hook patched on `CreditLedger.record` (called after the first bucket lock in each of the six) sets
  `b_holds_bucket` and waits for `a_started` plus a short pause; the hook must fire (asserted, so a fixture that never reaches it fails instead of passing);
- worker A starts a charge (`wallet.consume_credits`) after `b_holds_bucket`: on the old code A takes the wallet lock and waits for B's bucket lock while
  B waits for the wallet: a cycle; Postgres raises a deadlock error in one of them (about 1 s) and the other waits at most the per-test
  `lock_timeout` (set on each worker's connection) so the test FAILS instead of hanging; on the new code B already holds the wallet, A waits, B finishes, A charges;
- assertions: no error in either worker; the function's own result is present (e.g. the new bucket exists); the charge reduced exactly one bucket by exactly the amount.
Fixture sources: `billing/tests/test_monthly_rollover_cleanup_race.py` (rollover, mid-cycle, immediate change), `test_converted_trial_is_not_cancelling.py`
and `test_trial_to_annual_conversion.py` (both conversions), `tests_free_trial.py` (activate_subscription).

## Mutants (one per function)

Remove the `lock_wallet_first(...)` call in function N (six), plus: call it AFTER the first bucket lock in one function (order, not presence);
make the helper a no-op. Each must fail exactly its own thread test (and the helper-no-op all six).

## Limits (NOT RUN)

- The foreign-key wait at commit is Postgres behaviour I have read about, not seen here; the thread test shows or does not show it.
- A plain TestCase (inside one rolled-back transaction) never reaches it.
- Behaviour in production (pgbouncer in transaction mode, the role's lock timeout) is not tested.
- Nothing about licence rows (that is H-182, LOCK-2).

## Order

Tests first as their own commit; then the change; then the gate (reproduce-first: the six tests red on the base's three files, a billing regression,
the mutants, tests_cache_bespoke_1114 not needed: no answer changes); Verifier 1 verifies. Starts file work now; no run until the Release Engineer grants.
