# Verification: H-65, one run at a time for each Celery Beat task @ 51fbb0e

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-01.
**Branch:** `task/h65-beat-locks` @ **51fbb0e** (the code tip is `adf8fe0`, base-updated by 0b onto bundle 4's final code tip `4e629d4` as `9887b25`). The evidence is `docs/evidence/h65-beat-locks/`. The SM assigned H-65 to me (beta lane); v2 handed over its checklist (`GAP-v2-handover/PREP_s4_s6b.md`, "H-65").

The runs were wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, from a detached scratch checkout at 51fbb0e with its own DB (`test_vf_h65`; mutants on `test_vf_h65_mut`, dropped after). Under rule 15, d5's regression (billing + dashboard + ai_processor + the 9 beta-line guards, 3115 OK at adf8fe0) is cited, not repeated.

**Verdict: VERIFIED-WITH-NOTES.** The lock is correct:
- token-owned SET NX PX;
- Lua compare-and-delete release and compare-and-extend heartbeat;
- release in `finally`;
- fail-closed skip with an ERROR, which the watchdog also reports;
- all 21 scheduled tasks are locked, and the 2 exemptions are reasoned;
- the reflective guard works;
- test isolation clears exact keys, without scanning.

**44dde21 is a faithful model** (below). Two notes need the SM:
- **N1 (small, real):** the worst-case lock lifetime is `max_hold + ⅔·ttl`, not `max_hold`. So on the every-5-minutes schedule, a hung or killed run **does** swallow the next scheduled run. That contradicts the module's stated guarantee, and the guard misses it.
- **N2 (the SM's cycle-end ruling):** my test shows the renewal does **not** cover a mid-cycle grant skipped by every run up to the cycle end. The grant is lost. It takes **31 consecutive skipped daily runs** (a month-long outage), and it isn't introduced by H-65. Whether that closes the ruling, or needs a fix or a detection, is the SM's call.

N3 is a separate, pre-existing over-grant found by the same test.

## The checklist (v2's, with the SM's rulings)
| Item | Finding |
|---|---|
| Token lock; Lua compare-and-delete and compare-and-extend | Correct: `_RELEASE` and `_EXTEND` compare `GET` to the run's token (`task id:uuid`), so a lapsed run can't delete or extend a newer run's lock. d5's L2 and L3 kill the non-compare versions. |
| Released on an exception | `finally:` stops the heartbeat and releases (compare-and-delete). `test_the_lock_is_released_when_the_run_raises` covers it; d5's L1 kills a skipped release. |
| Heartbeat vs max_hold < interval | **N1.** The heartbeat stops at `max_hold`, but its last extension (at ⅔·ttl before it) keeps the lock a full TTL longer. |
| Per-entity keys | N/A: every locked task is a Beat task that takes no arguments; one key per task name is right. |
| Fail closed only for catch-up tasks | The **10** money and state tasks each have a skip-then-catch-up test on real rows (`test_beat_lock_catch_up.py`). The other locked billing tasks are full-state scans that the next run catches up: `reconcile_subscription_prices`, `reconcile_stripe_prices`, and `recalculate_conversion_probabilities` (iterates every profile). The dashboard emails and the paid probes (`nightly_stripe_live_qa`, the two grading benchmarks) fail closed under the SM's explicit exception, listed in EVIDENCE's Deviations. The receipt sweep's 3-day window is kept by ruling. |
| The reflective guard catches missing and stale entries | `test_every_beat_task_is_locked_or_exempt` and `test_every_exemption_names_a_scheduled_task`. My **Z1** (a weekly email task loses its decorator) is **killed** by the guard and by `RealTaskSkipTests`. |
| Test isolation, not vacuous | Exact keys (`known_lock_names()`, recorded at decoration), under this process's prefix. `BeatLockIsolation1/2` prove that a lock left by one test doesn't make the next test's task skip. d5's L13 kills the clearing being removed. |
| Skips are logged | Held: a WARNING with the task, key and holder (asserted in every catch-up test). Redis error: an ERROR, and `check_beat_health` adds `lock_store_problem()`. |
| All 9 repo-wide guards on the merged base | In d5's regression at adf8fe0 (cited). |
| H-73's raw-client count | H-73 (`8bab946`) is stacked on `adf8fe0`, and `AutoGrader/beat_locks.py` is unchanged after it, so its `RAW_CLIENT_USERS` count still matches. |
| Exemptions | `record_concurrent_users`: one sample row per 60 s tick, where an overlap writes one extra sample. `check_beat_health`: the watchdog must never be skippable. Both are sound. |

## 44dde21: is the rewritten bundle-4 test still a faithful model? Yes
- **What changed.** `run_b_after_run_a` nests run A inside run B in one thread. With H-65 the nested run A skips on B's lock, so the helper no longer reached the per-row re-checks. It now deletes B's key before run A: B's lock has **lapsed**. Run A takes the lock itself, and run B must log "finished without its lock".
- **Why it's faithful.** After H-65, two runs of the same task overlap only when one's lock lapsed: a Redis blip, a run past max_hold, or N1's window. That is exactly the case the per-row re-checks now defend. Pre-H-65 the helper modelled "no lock at all", which no longer exists for task-vs-task overlaps. The stricter assertion (B's ERROR) also pins H-65's detection.
- **The threaded tests are unaffected.** `test_run_b_blocked_behind_run_a_does_not_grant_again` and `test_expiry_blocked_behind_a_conversion_leaves_it_paid` call the **service** directly in one thread, so no task lock gates them, and they still exercise the row-lock re-check.
- **Production-path tests are unaffected too.** My checkout-path tests (the conversion via `_handle_individual_checkout` landing after `expire_active_trials` read the row) involve a webhook, which no Beat lock covers. There the re-checks remain the only defence.
- d5's M1/M2 show the adapted tests still fail when the re-checks are removed.

## Notes
**N1 (small, real: fix it or correct the stated guarantee).**
- `single_instance` caps `ttl` at `max_hold`, the heartbeat extends every `ttl/3`, and it stops once `max_hold` has elapsed. So the last extension can land up to `ttl/3` before `max_hold`, and the lock then lives a further `ttl`. **Worst case: about `max_hold + ⅔·ttl`.**
- A hard-killed worker right after an extension gives the same bound.

| Schedule | max_hold | ttl | Worst-case lock | Interval | |
|---|---|---|---|---|---|
| **EVERY_5_MIN** | 240 s | 240 s | **≈ 400 s** | 300 s | **swallows the next run** |
| HOURLY | 3000 s | 300 s | ≈ 3200 s | 3600 s | ok |
| EVERY_6_HOURS, DAILY, WEEKLY, PRICE_SWEEP | | 300 s | well inside | | ok |

- **Probe L** (the real decorator, scaled 1:60 so the ratios are exact: max_hold 4 s, ttl capped at 4 s, interval 5 s): a hung run keeps the lock past 5 s, and the next run is **SKIPPED**. The control, with the same max_hold and `ttl=1`, **RAN**.
- `test_max_hold_is_below_the_schedule_interval` checks only `max_hold < gap`.
- **Impact:** only `escalate_stale_licence_stripe_intents` (every 5 min, a catch-up task): after a hung or killed run, one extra run skips. It's a delay, not a loss.
- **Suggested fix:**
  - give EVERY_5_MIN an explicit short TTL (for example `ttl=60`);
  - make the guard check the worst case, `max_hold + ttl < gap`;
  - adopt probe L's two cases as tests.

**N2 (the SM's ruling: "d5 proves renewal covers it, or fixes the selection, WITH A TEST"). My test answers it: the renewal does not cover it.**
- **Probe C** drives a real annual subscription through its whole year, day by day, with the real tasks: grants at 02:00, the cleanup at 05:00, and `process_rollover_and_renewal` an hour after the cycle ends. It then repeats the year with the grant runs finding the lock held.
  - **Start 15 January, 14:00:** no skip gives 11 mid-cycle grants, the last on 16 December. If **every** grant run from that one to the cycle end (**31 runs**) skips, there are **10 grants**: the month is lost, and carry-over is 55,000 against 60,000. If the last run before the end runs (30 skipped), the grant is caught up: 11 grants, the same carry-over.
  - **Why:** the grant query requires `billing_cycle_end > now + tolerance`, and the renewal (`activate_subscription`) starts a new chain. It rolls over the old month's bucket, which the rollover fix's cleanup guard kept, but it never grants the missed month.
- **Not introduced by H-65:** `billing/services.py` and the grant query are unchanged here. Before H-65, a month without Beat loses the grant the same way. H-65 doesn't make it more likely: a held lock lapses within `max_hold` (6 h for DAILY), and fail-closed applies only while Redis, which is also the Celery broker, is down, when Beat can't dispatch anyway.
- **d5's EVIDENCE says "about 18 consecutive skipped daily runs"; the test measures 31** (or 3 for month-end starts, where the "lost" grant is N3's extra one).
- **For the SM:** accept it with this test as the documentation (a month-long outage), or ask for a cheap **detection**: `process_rollover_and_renewal` logs an ERROR (ids only) when the old subscription's `next_credit_grant_at` is before its `billing_cycle_end − tolerance` (a grant owed at renewal). I'd suggest the detection as a backlog row, not a blocker.

**N3 (new, pre-existing, backlog): an annual subscription starting on the 29th–31st gets 13 monthly grants in a 12-month contract.**
- Probe C from **31 January, 01:00**: `relativedelta` clamps the first due time to 28 February, and the chain then stays on the 28th: 28 Feb, 28 Mar, …, 28 Jan.
- That's **12 mid-cycle grants plus the activation grant**. The last one lives 3 days (to the 31 Jan end, capped), and the renewal rolls its unused credits into carry-over.
- Not H-65's; the same code is on beta. **Suggested fix:** compute each due time from `billing_cycle_start + k months`, not `run start + 1 month`. That also removes the chain's drift.

## Evidence
My probes are in `h65_probe_test_vf1a_h65_probe.py` (not committed); my mutants are in `h65_mutants_vf.py`.

| Check | Result |
|---|---|
| **Baseline** @ 51fbb0e: my probes + `AutoGrader.tests_beat_locks`, `billing.tests.test_beat_lock_catch_up`, `test_overlapping_run_rechecks`, `test_monthly_rollover_cleanup_race` | **99 tests, 3 failures, exactly my findings:** L (EVERY_5_MIN skipped; the control ran) and C, both starts (the grant lost when all runs up to the end skip; caught up when the last one runs). Every d5 test passes. |
| **Mutants (mine), 2/2 KILLED** on `test_vf_h65_mut`, sha-checked restore | **Z1** (a weekly email task loses its lock): the coverage guard and `RealTaskSkipTests`. **Z3** (the heartbeat never stopped): `test_a_run_past_max_hold_lets_its_lock_lapse`. |
| d5's battery | 16/16 killed at adf8fe0, including M1/M2 for 44dde21 (cited). |
| Hooks | `pre-commit run --from-ref 67a0681 --to-ref 51fbb0e` passes, and each of the 8 non-merge commits passes. |
| Bundle 4 | `git merge-tree --write-tree 67a0681 51fbb0e` (bundle 4's final tip) is **clean**. |
| Prefix run | Not needed: N2 and N3 are in code H-65 doesn't touch (`billing/services.py` unchanged), and N1's code is new with H-65. |

Logs: `runs/h65_baseline_51fbb0e.log`, `runs/h65_mutant_Z1.log`, `runs/h65_mutant_Z3.log`.

---

# Delta re-check: N1 @ 6cfebed

**Date:** 2026-10-01. By the SM's ruling, this covers **only the N1 delta**.
- **Commits over 51fbb0e:**
  - `cd03cda`: this record
  - `1e9a27e`: the N1 tests
  - `593d249`: the fix
  - `8360684`: a direct test of the TTL cap
  - `c5790bb`, `411f130`, `6cfebed`: evidence
- **Setup:** my scratch checkout at 6cfebed, `test_vf_h65`, in 0b's slot, with rule 16's `systemd-inhibit`, 6G and `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`.

**Verdict: VERIFIED.** N1 is closed.

## The fix
- **`593d249`:**
  - `EVERY_5_MIN` goes from 4 min to **3 min**.
  - It adds **`EVERY_5_MIN_TTL = 1 min`**.
  - `escalate_stale_licence_stripe_intents` now declares `@single_instance(max_hold=beat_locks.EVERY_5_MIN, ttl=beat_locks.EVERY_5_MIN_TTL)`.
  - The docstring now states the real bound.
  - **Worst-case lock life:** about `max_hold + ⅔·ttl` ≈ **220 s**, against the 300 s interval. The guard's conservative bound, `max_hold + ttl` = 240 s, is also inside it.
  - It's the only task on `EVERY_5_MIN` (`git grep` at 6cfebed). No other schedule's values changed.
- **`1e9a27e`:**
  - `test_a_hung_runs_lock_is_gone_before_the_next_scheduled_run` checks `max_hold + ttl < gap` for **every** declared Beat lock, replacing the old guard's reliance on `max_hold < gap` alone. That old test is kept.
  - `test_a_hung_runs_lock_is_gone_within_max_hold_plus_ttl`.
  - `EveryFiveMinuteScaledTests`: my probe L, reading the task's **declared** lock and scaling it 1:60.
- **`8360684`:** a direct test of the `ttl` cap at `max_hold`. It is needed because, after N1, no scheduled task passes `ttl > max_hold`, so mutant L9 survived at 593d249. d5 recorded that and fixed it.

## Evidence
| Check | Result |
|---|---|
| **Delta run** @ 6cfebed: probe L's tip case and its control + `AutoGrader.tests_beat_locks` | **29 tests OK** (23.4 s). Probe L at the tip's declared values (`max_hold=180s ttl=60s`, scaled to 3 s / 1 s, interval 5 s): the next run **RAN**. At 51fbb0e the same probe at that tip's values was **SKIPPED**. |
| **My mutant Z5** (on `test_vf_h65_mut`, dropped): the heartbeat's compare-and-extend sets the lock's life to `max_hold` instead of `ttl`. The production file's sha matched the commit blob after the restore. | **KILLED** by both new N1 tests (`EveryFiveMinuteScaledTests`, `test_a_hung_runs_lock_is_gone_within_max_hold_plus_ttl`): 2 failures in 27. This is a different undo from d5's N1a (the guard) and N1b (the decorator's TTL): it lengthens the lock inside the heartbeat. |
| d5's gates (cited) | reproduce-first at 1e9a27e: exactly the 2 new tests fail; the red battery at 593d249: 17/18 (L9 survived, recorded); the green battery at c5790bb: 18/18 killed, modules 220 OK; the regression at 411f130: billing + dashboard + ai_processor + 9 guards, 3115 OK. |
| Hooks | `pre-commit run --from-ref 51fbb0e --to-ref 6cfebed` passes, and each of the 7 commits passes. |
| Merges | The branch is still based on `4e629d4`. `git merge-tree --write-tree` against 67a0681 (bundle 4's final tip, two docs-only commits ahead) is **clean**. It is also clean against H-78 @ b9e4ccb and H-76 @ 91eadbd. |

The old hard-coded probe-L case (`max_hold=4, ttl=300`) is unchanged in the probe file. It documents the pre-fix values and wasn't run here.

Logs: `runs/h65_n1_6cfebed.log`, `runs/h65_n1_mutant_Z5.log`. Probe: `h65_probe_test_vf1a_h65_probe.py` (it adds `test_every_5_min_as_declared_at_the_tip`).
