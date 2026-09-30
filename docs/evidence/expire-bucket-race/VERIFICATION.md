# Verification: expire_bucket race @ 5ee8bff

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Security (ed).
**Base:** beta 755aa27 (the bundle after bundle 3). **Date:** 2026-09-30.

**Verdict: VERIFIED.** The fix is minimal and correct. Nothing is required before merge.

## What I checked
My own detached checkout with its own test DB, under `systemd-run` MemoryMax=6G, nice and timeout.

| Check | Result |
|---|---|
| Scope | `billing/services.py` (+6: after `select_for_update().get()`, `if bucket.is_processed: return 0`), one new test file and evidence. 2ba72a6 is the unrun WIP, completed by 5ee8bff. No migration. The tests use no mocks (rule 14). |
| The race | `cleanup_expired_credit_buckets` selects `is_processed=False` buckets **without** a lock and passes each copy to `expire_bucket`. Two overlapping runs can both hold the same stale copy. Before the fix, the second one to get the row lock wrote a second EXPIRE row. Now it sees `is_processed` under the lock and returns 0. |
| Other writers of `is_processed` | The monthly rollovers in `billing/services.py` (the `old_monthly` / `old_monthly_bucket` paths) and in `billing/license_service.py` (`current_bucket`) each select with `select_for_update()` **inside** a query filtering on `is_processed=False`. Under READ COMMITTED, Postgres re-checks that filter on the locked row, so a bucket that cleanup has just expired is skipped. Both orders of the race are therefore safe: cleanup then rollover, and rollover then cleanup. |
| `billing.tests.test_expire_bucket_race` | **3 OK**: a normal expiry (control), a stale copy replayed, and a real two-thread race (TransactionTestCase with a barrier, exactly one EXPIRE row) |
| Author regression (rule 15) | Relied on: billing 1668 OK (`docs/evidence/expire-bucket-race/regression_billing.txt`); reproduce-first on beta, 2 of 3 fail. |

## My mutant
| Mutant | Result |
|---|---|
| The re-check under the lock removed | **killed** by `test_a_stale_copy_expires_nothing_the_second_time` and `test_two_racing_expiries_write_one_expire_row` |

The restore was sha-checked.

## Notes (not blocking)
**N1.** The spendable balance comes from live buckets, so beta never double-charged. The defect was only a duplicate EXPIRE ledger row, and any duplicates already written in production would need their own data query (a production action needing the founder). That's out of scope here.
