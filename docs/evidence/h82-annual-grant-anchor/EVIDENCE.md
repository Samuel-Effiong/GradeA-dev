# H-82: an annual subscription gets 12 monthly grants a year, on its anchor day

**Author:** d5. **Branch:** `task/h82-annual-grant-anchor`, base-updated by
0b onto `task/beta-batch-5` `d97b7e7c` (clean merge, `892aa913`).
**Scope:** Path A only, individual annual subscriptions
(`process_mid_cycle_credit_grant`). Licence allocations (Path B) are H-88.

## The change (4 points)
1. **Before:** `SubscriptionService.process_mid_cycle_credit_grant` set the
   next due time to `now + 1 month`, from the run that granted. A 31 January
   start clamps to 28 February and stays on the 28th: 28 Feb, 28 Mar, ...
   28 Jan, which is 12 mid-cycle grants plus the activation grant, 13 in a
   12-month contract (1a's probe C, H-65 verification N3). Every due time
   also drifted by the run's lateness.
2. **After:** `refresh_timing.next_monthly_grant(anchor, served_due,
   contract_end)` returns the next due time as `billing_cycle_start + k
   months`, computed from the anchor each time, for the first k more than
   `ANCHOR_SNAP` (7 days) past the due time just served, capped at the
   contract end. It never uses "now".
   - A row already drifted (a 28 Mar due on a 31 Jan anchor) moves to
     30 April, not 31 March (which would be a second grant three days
     later). No data migration is needed: each row converges at its next
     grant.
   - After an outage the owed grants are caught up, one per daily run, each
     with a WARNING (ids only).
   - A caught-up bucket expires at `min(now + 1 month, billing_cycle_end)`,
     so it is not born expired and does not outlive the contract.
3. **Reach:** individual annual subscriptions only, through the daily
   `process_annual_plan_credit_grants` task. A subscription that started on
   the 1st to the 28th gets the same dates as before, minus the lateness
   drift. Monthly plans and licence allocations are untouched.
4. **Tests:** `billing/tests/test_annual_grant_anchor.py` drives the real
   task with a patched clock (a 31st start, a 15th control, a leap
   February, a drifted row, a three-month outage, no bucket born expired,
   and 1a's V3: a catch-up at the cycle end ends with the contract).
   `billing/tests/test_next_monthly_grant.py` is property-style over the
   helper (every start day, late and skipped runs, drifted rows, the cap).

| Commit | What |
|---|---|
| `7556aa64` | the e2e tests (red) |
| `ca5d9352` | the fix, the property tests, `run_mutants.py` |
| `9d027366` | the outage tests' driver: catch-up runs a day apart |
| `31d769cb` | 1a's V3 as a test; mutant H8 (1a's Y2) |
| `892aa913` | base update onto `d97b7e7c` (0b) |

## Gates
One chain (`chain.sh`) on the frozen tip `892aa913`, in one slot granted by
0b, stopping on red.

| Gate | Result | Log |
|---|---|---|
| Repro: the tip's `test_annual_grant_anchor` over `d97b7e7c`'s `services.py` and `refresh_timing.py` (h66-repro worktree, own DB) | 7 tests, 5 failures | `repro_892aa913_tests_over_d97b7e7c_code.log` |
| (a) 7 modules | 84 OK | `a_modules_892aa913.log` |
| (b) battery (`test_h82_mut`) | baseline green, H1–H8 8/8 killed, every restore sha-verified | `b_mutation_battery_892aa913.log`, `logs/`, `results.tsv` |
| (c) billing + the 9 guards | 2083 OK (skipped=2), wall 489 s | `c_app_billing_guards_892aa913.log.gz` |

The two repro tests that pass on the old code are the 15th control (it is a
control) and 1a's V3: the old code never caught up, so it had no bucket to
outlive the contract. V3 guards the new catch-up path, and H8 shows it
bites there.

(a)'s modules: `test_annual_grant_anchor`, `test_next_monthly_grant`,
`test_annual_mid_cycle_grants`, `test_subscription_cycle_integrity`,
`test_monthly_rollover_cleanup_race`, `test_trial_to_annual_conversion`,
`test_beat_lock_catch_up`.

**An earlier red run.** Gate (a) at `ca5d9352` failed in the two outage
tests. The fault was in the tests' driver, not the fix: it made two
catch-up runs at the same instant and the first one a day late. `9d027366`
makes the runs a day apart, as Beat's are. That run's log was in a session
scratch folder that a restart wiped, so it is not committed; no gate had
passed on that commit.

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as
`systemd-inhibit --what=idle:sleep … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16). (c) ran with `--parallel 2 --verbosity 2` through a
timestamper, beside 0b's Gate 10 full suite.

**Rule 17.** The battery and its baseline ran with
`PYTHONDONTWRITEBYTECODE=1` (set for the runner and passed to every test
subprocess), and the runner deleted `billing/__pycache__` in its worktree
before the baseline, before each mutant and after each restore. The runner
ran inside the wrapper above, so the whole battery shared one 6G scope and
one 1800 s timeout.

## Mutants
| Id | Guards | Result |
|---|---|---|
| H1 | the snap past the served due (`ANCHOR_SNAP` → 0) | killed |
| H2 | the chain is computed from the anchor, not the served due | killed |
| H3 | the helper is fed the served due, not now | killed |
| H4 | the last due is capped at the contract end | killed |
| H5 | a caught-up bucket lives from its grant time | killed |
| H6 | a caught-up grant logs a WARNING | killed |
| H7 | the row's next due is the anchored due, not the bucket's expiry | killed |
| H8 | a caught-up bucket ends by the contract end (1a's Y2) | killed |

## For the verifier
- The transition hazard is the point most worth a probe: rows in production
  are already drifted, and the fix must not double-grant them
  (`test_a_drifted_row_moves_to_the_next_anchor_period_not_back_to_this_one`,
  mutants H1 and H3).
- The helper's docstring states its one assumption: a served due lies within
  `ANCHOR_SNAP` before its own anchor point. The old chain's drift is at
  most 3 days. `test_a_due_time_far_off_its_anchor_still_gives_one_per_period`
  covers due times further off.
- The logs' addresses are test fixtures only.
