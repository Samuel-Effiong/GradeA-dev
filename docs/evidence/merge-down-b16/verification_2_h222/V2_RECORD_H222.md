# Verifier 2 record: H-222 (wallet lock first at expire_bucket and the licence clawback)

Judged: d5's task/h222-wallet-lock-first-expire 3b5f9fb5 (over d2ad0405), as merged by 0b into the merge-down tip **f46a3097** (070d3c09 + 3b5f9fb5, no conflict).
Slot: 2026-10-09 20:48:27 to 20:50:01 WAT (`date`), GRANT 20:44 by 0b, load 1.2-1.8 during the run, one systemd-inhibit around the script.
Sets and failure fragments were written in vf_h222_mutants.py (docstring) before any run; sha256 of script 1746121095dd99ac..., mutants file 52e02f12b6c3bd09... .

## Read (git objects only)
- Production delta: billing/services.py `expire_bucket` gains `lock_wallet_first(bucket.wallet)` before the bucket get; billing/license_service.py `remove_teacher_from_license` uses `wallet = lock_wallet_first(teacher.credit_wallet)` before its bucket query. Nothing else in production.
- Lock order: clawback = allocation, wallet, buckets; Beat cleanup (tasks.py) holds no lock when it calls expire_bucket. Every SchoolCreditAllocation select_for_update site (license_service 3171, 3494, 4331; stripe_service 3322; tasks 1098) takes the allocation before any wallet; no wallet-then-allocation path found.

## Results at f46a3097
| Run | Result |
|---|---|
| baseline (X module + ClawbackRaceTests) | Ran 4 OK |
| ClawbackRaceTests alone, run 1 / 2 / 3 | Ran 2 OK / Ran 2 OK / Ran 2 OK (3 of 3) |
| B0 base production code (d2ad0405) + tip tests | FAILED (failures=2): X1, X2; each block holds "worker 1 never started its charge" |
| M1 clawback wallet lock removed | EXACT {X2}, fragment "never started" |
| M2 expire_bucket wallet lock removed | EXACT {X1}, fragment "never started" |
| M3 expire_bucket lock after the bucket get | EXACT {X1}; real `deadlock detected` on billing_creditbucket and "the charge and the function deadlocked" |
| M4 clawback lock after the bucket query | EXACT {X2}; same real deadlock |
Restores: 5/5 sha256 equal to the commit blob; scratch tree clean at f46a3097 afterwards.

## Judgement of X1 and X2 (rule 22)
At the base both are red only because the helper is never called: worker 0 never reaches the hook, worker 1 waits 10 s, race() asserts `a_started` and says "worker 1 never started its charge". That is d5's corrected fragment and it is the real cause, found in each test's own FAIL block. It is a structural failure (the lock is missing), not a shown deadlock; the deadlock is shown only when the helper is called TOO LATE (M3, M4), which the tests also catch.

## Limits
- ClawbackRaceTests A1/A2 (timing races) were GREEN in B0 and under M1-M4: they prove nothing about the fix; they are informative only.
- Two of my mutants (M1/M2) fail for the "helper not called" reason, the other two for the real deadlock; no mutant of the tests themselves (e.g. the `assertNotEqual(rows, [])` guard) was made.
- Only the two functions of the delta were judged; the full run is 0b's.

VERDICT: VERIFIED at f46a3097.
