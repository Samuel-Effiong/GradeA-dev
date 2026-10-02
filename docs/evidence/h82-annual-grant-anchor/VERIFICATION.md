# Verification: H-82 Path A, an annual subscription gets 12 monthly grants on its anchor day @ 45fb442c

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-02.
**Branch:** `task/h82-annual-grant-anchor` @ **45fb442c** (code tip `892aa913`):
- `7556aa64`: the e2e tests
- `ca5d9352`: the fix, the property tests, `run_mutants.py`
- `9d027366`: the outage tests' driver runs catch-ups a day apart
- `31d769cb`: my V3 end-of-cycle case adopted as a test, plus mutant H8 (= my Y2)
- `892aa913`: 0b's base update onto `task/beta-batch-5` `d97b7e7c`
- `45fb442c`: the evidence (docs only)

The evidence is in `docs/evidence/h82-annual-grant-anchor/`. Scope is Path A only (individual annual subscriptions, `process_mid_cycle_credit_grant`); licence allocations are H-88.

I ran in 0b's slot from my detached scratch checkout at 45fb442c, with its own test DBs (`test_vf_h82`, mutant `test_vf_h82_mut`). The wrapper was rule 16's `systemd-inhibit`, the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`. Under rule 15 I cite d5's regression (billing + 9 guards, 2083 OK at 892aa913) and don't repeat it.

**Verdict: VERIFIED.**
- All four points the SM asked me to probe hold: (a) drifted rows don't double-grant at deploy, (b) no catch-up bucket is born expired, (c) every start day gives exactly 12 grants a cycle on the anchor day, (d) nothing is granted at or after the cycle end.
- The one gap my pre-review found (a caught-up bucket could outlive the contract) is closed by `31d769cb` and proven by my mutant Y2.
- Two observations below (O1, O2) are behaviours at the edges, not defects in this change. O2 has not been ruled on; it does not block the merge.

## Static review
**The fix** (`billing/refresh_timing.py`, `billing/services.py`):
- `next_monthly_grant(anchor, served_due, contract_end)` computes each due time as `billing_cycle_start + k months`, for the first k more than `ANCHOR_SNAP` (7 days) past the due time just served, capped at the contract end. It never uses "now".
- `process_mid_cycle_credit_grant` feeds it the stored `next_credit_grant_at`, writes the anchored due back, and logs a WARNING with ids only when the served due is more than a day old.
- A caught-up bucket expires at `min(now + 1 month, billing_cycle_end)`.
- `billing_cycle_start` is NOT NULL, and the lock re-check still refuses a grant at or after the cycle end.

**The base update** `892aa913`: H-82's four billing files are identical to `31d769cb`. Batch-5's only billing change since the old base is H-66 (`stripe_service.py` and its tests), so nothing overlaps. Production code is unchanged after `ca5d9352`.

**Rule 14:** there is no MagicMock in the new tests.

**The evidence:** the committed logs match the stated gates. The addresses in them are test fixtures (every one is a literal in the test code, or built by a test's own loop).

## Evidence
| Check | Result |
|---|---|
| **Run** @ 45fb442c: my probes V1–V4 + `billing.tests.test_annual_grant_anchor` + `billing.tests.test_next_monthly_grant` | **17 tests OK** (394 s). |
| **V1 (c, d):** the real task run daily for a year, for every start day 1–31 at two times of day, the 28th–31st at 02:03, leap-year starts and three month-end starts | **73 starts, 0 failing.** Each gives exactly 11 mid-cycle grants (12 with the activation grant), each on the first run after its anchor, none at or after the end, the chain ending at the cycle end, and no bucket born expired. |
| **V2 (a):** rows drifted by the old rule, then the fix deployed at 5 points in the year, for 6 start dates | **30 cases, 0 failing.** Every case has 11 mid-cycle grants, the smallest gap between two grants is 28 days, and every grant after the first post-deploy one is on the anchor. Example: a 31 Jan start deployed on 1 Mar grants 02-28, 03-28, then 04-30 (not 03-31). |
| **V3 (b, d):** outages (numbers below) | **8 cases, 0 failing.** No bucket is born expired or outlives the cycle end, nothing is granted at or after the end, and every catch-up line is an ids-only WARNING. |
| **V4 (observation):** a delayed-webhook activation | See O1. |
| **My mutant Y2** (drops the cycle-end cap on a caught-up bucket's expiry; on `test_vf_h82_mut`) | **KILLED by exactly two tests:** d5's adopted `test_a_catch_up_at_the_cycle_end_never_outlives_the_contract` and my V3 probe (the two "last two anchors" cases: a bucket granted on 29 Jan would expire on 29 Feb, a month past the contract end). The other 15 tests pass under Y2. This agrees with d5's H8. |
| d5's gates (cited) | at 892aa913: repro 7 tests / 5 failures; (a) 7 modules 84 OK; (b) H1–H8 8/8 killed with sha-verified restores; (c) billing + 9 guards 2083 OK (skipped=2). |
| Hooks | `pre-commit run --from-ref d97b7e7c --to-ref 45fb442c` passes, and each of the 5 non-merge commits passes. |
| Merges | `git merge-tree --write-tree` against `task/beta-batch-5` `d97b7e7c` is **clean**; batch-5 is an ancestor of the tip. |

**Rule 17.** Both runs had `PYTHONDONTWRITEBYTECODE=1`, and the mutant was applied with `python -B`. `billing/**/__pycache__` was deleted before the baseline, before the mutant and after the restore (the logs show 0 directories each time). The restored `billing/services.py` matched the commit blob's sha256, and no tracked file was changed afterwards.

## V3's numbers (the SM asked for them in the record)
The user never spends, so "carry-over" is the sum of the CARRY_OVER buckets at the end of the year.

| Start | Outage | Mid-cycle grants | Shortest bucket life | Carry-over total | WARNINGs | First grants after the outage |
|---|---|---|---|---|---|---|
| 31 Jan 01:00 | over one anchor | 11 | 29 d 23 h | 55000 | 1 | 05-03, 05-31 |
| 31 Jan 01:00 | over two anchors | 11 | 27 d 23 h | 55000 | 2 | 06-03, 06-04 |
| 31 Jan 01:00 | the last two anchors, to end − 2 d | 11 | 23 h | 55000 | 2 | 01-29, 01-30 |
| 31 Jan 01:00 | the last anchor, to end − 1 h | 10 | 31 d 23 h | 50000 | 0 | none |
| 15 Jan 14:00 | over one anchor | 11 | 28 d 12 h | 55000 | 1 | 04-19, 05-16 |
| 15 Jan 14:00 | over two anchors | 11 | 28 d 12 h | 55000 | 2 | 05-19, 05-20 |
| 15 Jan 14:00 | the last two anchors, to end − 2 d | 11 | 12 h | 55000 | 2 | 01-14, 01-15 |
| 15 Jan 14:00 | the last anchor, to end − 1 h | 10 | 29 d 12 h | 50000 | 0 | none |

- **Catch-up carry-over (accepted as design by the SM):** after an outage over two anchors the two owed grants arrive on consecutive days, and both end up in carry-over. The total is 5000 per mid-cycle grant in every case, so an outage loses no credits as long as a run happens before the contract ends.
- The 23 h and 12 h lives are the last caught-up bucket, granted the day before the contract ends and capped at the end. It is not born expired.

## Observations
**O1: a delayed-webhook activation (V4).** A subscription with a 1 Jan anchor activated on 10 Feb gets 10 mid-cycle grants: 03-11, then 04-01 and the 1st of each month after. The first two are 21 days apart, because the activation path sets the first due a month after activation and the fix then returns to the anchor. That is one period shortened once, not a double grant. The grant count is the same as before the fix. I reported this in the pre-review.

**O2: an outage that runs to the contract end loses the last grant.** In the two "last anchor, to end − 1 h" cases the final monthly grant is never made (10 mid-cycle grants, no WARNING), because the first run after the outage is at or after the cycle end and is refused. That refusal is point (d) and is unchanged by this fix; the old code behaved the same. It needs a Beat outage over the last month of a contract, right up to its end. For the SM: a backlog row if a renewal should make that grant good; nothing to do in H-82.

Logs: `runs/h82_45fb442c.log`, `runs/h82_mutant_Y2_45fb442c.log`. Probe: `h82_probe_test_vf1a_h82_probe.py`. Mutant: `h82_mutant_Y2.py`.
