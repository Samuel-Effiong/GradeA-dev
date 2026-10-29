# Verification: H-25 cost cap on batch-2a @ fa2d351

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Hardening (d5).
**Base:** batch-2a b438cf5 (it merges cleanly into 2a's d510786, per 0b). **Commits:** d322165 (the port of bd95ccb), bc6533a (evidence), fa2d351 (default pin). **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** The change is test-only and correct:
- the default single-transaction scale is 600;
- each cost test has a wall-clock budget;
- the default is pinned.

Nothing is required before merge.

## What I checked
My own detached checkout, with its own test DB, **no `RACE_COST_*` variables set**, and every run under `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800` and `/usr/bin/time -v`.

| Check | Result |
|---|---|
| Scope | Only `AutoGrader/tests_cache_commit_race_cost.py` and one evidence log. It adds no mocks (rule 14). The file at fa2d351 is byte-identical to the batch-2b candidate dd8a7fc, and bc6533a's is identical to bff770a's. So 2a and 2b won't diverge. |
| The conflict with 877c900 | Both sides were kept: the PENDING first-import test is present and passes. The budget also covers it, because it is armed in `RosterScaleCostBase.setUp`. |
| Budget design | `wall_clock_budget` arms SIGALRM before `super().setUp()`. Because `enterContext` registers its cleanup first, the budget also covers tearDown and the other cleanups. The `finally` always cancels the alarm and restores the previous handler. The cost tests run in the main thread, including under `--parallel` workers. |
| Both H-25 modules plus my probe at the defaults, bc6533a | **Ran 20, OK.** Peak RSS **230,228 KB**, 2:41 wall. Slowest tests: per-row import 93.3 s, single transaction 35.0 s, PENDING 4.3 s. The **900 s budget leaves about 10× headroom** over the slowest test. |
| Cost module plus probe at the defaults, fa2d351 | **Ran 7, OK.** Peak RSS **209,396 KB**, 2:46 wall. |
| The budget is armed in the real class (my probe `VfBudgetArmedInRealCostClass(RosterScaleCostBase)`) | Inside a real cost test: `alarm` has 900 s pending, and the handler is `wall_clock_budget.<locals>.over_budget`. |
| The opt-in still works | Unset: (600, 2000, 900). `RACE_COST_ENROLLMENTS=6000 RACE_COST_TIMEOUT=5400`: (6000, 2000, 5400). `RACE_COST_ROWS=200`: (600, 200, 900). The values are read from the imported module. |
| CI | `tests.yml` runs `coverage run manage.py test --parallel 4` with a 30-minute job limit. At the old 6000 default the single-transaction class held one worker for about 20 minutes (my 877c900 run: both modules 1467 s test time). At 600 it takes 35 s. The cap therefore also removes a CI timeout risk. |

## My mutants (7)
| Mutant | Result |
|---|---|
| C1: budget never armed (the `enterContext` line removed) | **killed only by my probe**; d5's tests pass. See N1. |
| C2: alarm not cancelled in `finally` | killed: `test_a_test_within_budget_is_untouched` |
| C3: previous handler not restored | killed: `test_a_test_over_budget_fails_instead_of_running_on` |
| C4: `alarm(seconds)` becomes `alarm(0)`, so it never fires | killed: the over-budget test and my probe |
| C5 @bc6533a: default back to 6000 | survived at bc6533a. **fa2d351 was written to close exactly this.** |
| D1 @fa2d351: `DEFAULT_SINGLE_TRANSACTION_STUDENTS = 6000` | killed: `test_the_single_transaction_scale_defaults_to_600_at_most`, in milliseconds |
| D2 @fa2d351: the env fallback hard-coded to 6000, bypassing the constant | killed by the same test |

All restores were sha256-checked. My first run was cut off by the 12:29 session restart (SIGTERM 143); I re-ran everything cleanly.

## Notes (not blocking)
**N1.** `WallClockBudgetTests` tests the context manager on its own. Nothing of d5's checks that `RosterScaleCostBase` actually arms it, so C1 survives. My probe does check that: a subclass test asserts the pending alarm and the handler. Adopting it would pin the setUp wiring. It's 10 lines at `~/Documents/Projects/GAP-1a-records/h25_tests_vf_costcap_probe.py`.

**N2 (context for the record).** d5's journalctl evidence points the 12:03 and 11:42 OOMs at an unbounded MagicMock in the licence seat-counting WIP (rule 14), not at H-25. Both H-25 modules peak at about 230 MB at 600 and were at 244 MB during an interrupted 6000 pass. The cap is still right: it removes CI time risk and a 6000 default that nothing stopped.
