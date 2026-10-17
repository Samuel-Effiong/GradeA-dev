# expire_bucket expires a bucket once (beta fix)

Branch `task/expire-bucket-race` off beta `755aa27`. Beta-bound, for the bundle after bundle 3; the verifier is 1a. Found while building Epic A S3; the SM asked for the beta check and this small fix ahead of S3.

## Defect
`SubscriptionService.expire_bucket` (`billing/services.py`) locks the bucket row (`select_for_update`, inside `@transaction.atomic`) but never re-checked `is_processed` under that lock. A caller holding a stale copy of a bucket another caller has just expired waits for the lock, then writes a **second** EXPIRE ledger row for the same unused credits. Even two sequential calls with a stale object do it.

**Reachable on beta:** only through `billing.tasks.cleanup_expired_credit_buckets` (Beat daily at 05:00, no single-instance lock, `max_retries=0`), so it takes two overlapping cleanup runs:
- Beat scaled above 1 replica (`docs/ops/railway-services.md` says it must stay at 1, with no leader election, and Railway doesn't prevent scaling);
- old and new Beat containers overlapping during a redeploy near 05:00;
- a manual run during the nightly one;
- broker redelivery of a run that takes more than an hour.

On beta the licence clawback doesn't call `expire_bucket`. Epic A S3 will make it, adding a second path, and carries the same guard.

**Impact:** spendable balance is summed from live buckets (`CreditWallet.total_remaining_credits`), so no credits are lost twice. The harm is a duplicate EXPIRE row, which overstates the expired credits in the ledger history and anything reported from it.

## Fix
After taking the lock, `if bucket.is_processed: return 0`.

## Tests (`billing/tests/test_expire_bucket_race.py`)
- A normal expiry writes one EXPIRE row (the control).
- A stale copy expires nothing the second time (deterministic: the overlapping run's view).
- **Gate 3:** two real threads (TransactionTestCase, real commits) holding the same unprocessed bucket, released together at a barrier, write exactly one EXPIRE row, and the second returns 0.

## Gates (rule 15)
Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout and `--noinput`.

| Gate | Result |
|---|---|
| Reproduce-first | beta 755aa27's `services.py` (`prefix_755aa27_failing.txt`): **2 of 3 fail** (the stale copy and the race); the control passes |
| On the fix | **3 OK** (`changed_modules.txt`) |
| 2 Mutation | The fix is one guard. Its mutant, removing it, is exactly the reproduce-first run: killed by both. |
| 1 Regression (owning app) | `billing`: **1668 OK** (`regression_billing.txt`, trimmed; the full log is outside the repo) |
