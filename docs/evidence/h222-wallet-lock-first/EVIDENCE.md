# H-222: a licence clawback and a monthly grant no longer deadlock

Author: Hardening Engineer (d5). Base: phase2/epic-a `d2ad0405`. Gated tip: `3b5f9fb5`. Merged by the Release Engineer into the merge-down as `f46a3097` (clean). Written 2026-10-09 20:4x from the files in `gate_files/`; clock times are read from the logs.

## What was wrong
`audit.tests_background_attribution.ClawbackRaceTests`, run alone, deadlocked 3 of 3 (Senior Manager, 20:01): OperationalError "deadlock detected". Lock order everywhere else (billing/locks.py, H-181): licence, wallet, bucket.
- The monthly grant (`LicenseSubscriptionService._rollover_and_grant_monthly_bucket`) takes the WALLET row FOR UPDATE, then the teacher's current MONTHLY bucket FOR UPDATE.
- The licence clawback (`remove_teacher_from_license`, via `billing/license_views.py` `_remove_one_teacher`) took the allocation row, then every live bucket FOR UPDATE and no wallet lock, then `SubscriptionService.expire_bucket` per bucket (bucket lock, ledger row, second save of the same bucket).

## The diagnostic (granted 20:09, run 20:09:29-20:10:11; logs `diag_d2ad0405/`)
One clean red run of that test from the base, with a read-only poller (pg_stat_activity / pg_locks). Process A (the grant) held the wallet and waited in `SELECT ... FROM billing_creditbucket ... FOR UPDATE`; process B (the clawback) held the bucket and waited AT COMMIT (statement `COMMIT`, ShareLock on A's transaction, with a tuple lock on `billing_creditwallet` being taken). Postgres: "Process A waits for ShareLock on transaction ...; blocked by process B. Process B waits ...; blocked by process A. CONTEXT: while locking tuple (0,1) in relation billing_creditbucket".
Said exactly: THE RUN SHOWS THE COMMIT-TIME WAIT; THE REASON IS MY READING: a deferred foreign-key recheck on `CreditBucket.wallet` (billing/models.py 1156; the ledger row has no wallet key), queued because the clawback saves the same bucket row twice in one transaction (`expires_at`, then `is_processed`). Runs 2 and 3 of the diagnostic failed through my poller (its open session blocked the test database drop): script fault, not the code.

## What changed
- `SubscriptionService.expire_bucket`: `lock_wallet_first(bucket.wallet)` before its bucket lock.
- `LicenseSubscriptionService.remove_teacher_from_license`: `wallet = lock_wallet_first(teacher.credit_wallet)` after the allocation lock and before the bucket query.
- Callers of `expire_bucket` on the merged tree (read): (1) `billing/tasks.py` `cleanup_expired_credit_buckets` (Beat): holds no database lock at the call; (2) `remove_teacher_from_license`: allocation and now the wallet; its inner call re-locks a wallet it holds. No caller holds a bucket or subscription row at the call. The allocation-then-wallet order matches the Beat refresh; no path locks a wallet then an allocation (consume's licence roll-up runs after commit, H-182).
- Tests: `billing/tests/test_wallet_lock_first_expiry.py` (X1, X2, H-181's order mode: the worker stops at the entry of the wallet-lock helper). `ClawbackRaceTests` now call the REAL clawback (a hand-written copy of the old lock order would deadlock against the fixed `expire_bucket`), with a non-vacuous assertion added (Verifier 1).

## Gates
Tip `3b5f9fb5`. Scripts in `gate_files/scripts/` (`.txt`), logs gzipped.
| Step | Result |
|---|---|
| Small slot 20:21:00-20:21:39 | Ran 56, OK |
| Chain, first run 20:21:50-20:22:31 | STOPPED at (r): my reason fragment ("the hook was not reached once") was not what the harness prints ("worker 1 never started its charge", asserted before the hook message). Red set was the expected {X1, X2}. Logs in `stops/chain1_3b5f9fb5/`. |
| Chain, the one delegated re-run 20:30:36-20:36:34 | (r) red {X1, X2} with the corrected fragment; (a) 673 tests OK; (b) 4 of 4 killed, exact required sets |
| (c) regression | NOT a separate run: the Senior Manager ruled that the promotion's one full run (on the merge-down with this tip merged, `f46a3097`) is H-222's regression. `c_h222.sh` was written and is kept in `gate_files/scripts/`, never run. |
NOTE (rule 22): the corrected fragment was written AFTER seeing the failure; Verifier 2 judges X1/X2 for their real reason.

## Notes on the files
- The diagnostic poller prints its own times in UTC (19:09:48 is 20:09:48 WAT).
- `gate_files/battery_3b5f9fb5.tar.gz` holds the battery's logs and `results.tsv` (raw failure bodies under `logs/raw/`).

## Limits
- A1 and A2 (the ClawbackRaceTests) are timing races with no hook: A2 was green at the base in the chain's run (red once in the diagnostic) and A1/A2 stayed green under W2-W4. They are informative only; X1 and X2 carry the proof.
- Trial expiry (`services.py` ~1379) locks the subscription row, then the TRIAL bucket, saves it once and writes no wallet: left alone on purpose (a wallet lock after the subscription row would be a new order). The Beat cleanup alone saves a bucket once and could not meet this cycle; the lock inside `expire_bucket` is for one order everywhere and for the clawback's inner call (Senior Manager's ruling).
- Possible follow-up, not done: save the clawback's bucket once (both fields in one save), which would remove the recheck trigger.
- The new module's docstring still says "the hook was not reached once"; the real text is the harness's.
- Worktrees (rule 21): `Grade-Automator-Plus-h222-wallet-lock-first`, `Grade-Automator-Plus-h222-diag`.
