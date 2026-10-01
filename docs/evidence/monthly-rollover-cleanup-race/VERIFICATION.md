# Verification: the monthly rollover lost to the 05:00 cleanup (F6 item 4) @ fc08945

**Verifier:** 1a. **Author:** d5. **Date:** 2026-09-30.
**Branch:** `task/monthly-rollover-cleanup-race` @ **fc08945**, off beta `abeda10`, stacked on the verified mid-cycle re-check (`2bfa2e8`, merged as `3f5fa73`). The review range is `3f5fa73..fc08945`: the code is `billing/{refresh_timing,services,tasks,license_service}.py`, the tests are `billing/tests/test_monthly_rollover_cleanup_race.py` plus one updated assertion in `test_trial_to_annual_conversion.py`, and the evidence and F6 query are in `docs/evidence/monthly-rollover-cleanup-race/`.

The runs were wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, from my detached scratch checkout at fc08945. The baseline, guards and prefix used DB `test_vf_mcg`; the mutants used their own DB, `test_vf_mcg_mut` (dropped after), as 0b asked. Under rule 15, d5's billing regression (1755, with the one expected B2 failure, then the 34 OK confirmation) is cited, not repeated.

**Verdict: REJECTED.** The three defences close the reported race, and the SM's five points hold (below). **But the due tolerance introduces two new billing defects.** Neither is in the pre-fix tree, both reproduce through the real tasks, and both have small fixes:
- **F1 (substantive):** in about half of all months, the licence's monthly consumption window is **not reset**. Last month's usage then caps this month's new-teacher enrolments.
- **F2 (low frequency):** a month's credits are granted for the **last few minutes of a contract**.

The F6 query also reports customers on no-carry-over plans (the SM's point 4); see Q4. I can re-check the round quickly: my probe module holds the tests for F1 and F2.

## Findings

### F1: the licence consumption window misses its monthly reset when the run starts early (introduced by B1)
`_refresh_teacher_credits` opens the licence's monthly consumption window with a conditional UPDATE:
- **The condition:** `consumption_window_start <= now - 1 month`, where `now` is now the run's start.
- **The due check:** now `next_credit_grant_at <= now + 5 min`. The two used to agree exactly: pre-fix, the teacher who opened the window was due at `window_start + 1 month` and was only refreshed when `now >= that`.
- **When the run starts a few seconds earlier than last month's run** (d5's own jitter case, `test_a_run_starting_a_little_earlier_than_last_months_still_refreshes`):
  - the teacher **is** refreshed through the tolerance;
  - but the window's UPDATE matches nothing;
  - so `total_credits_consumed` keeps last month's usage for another month.

**Probe F1** (`F1LicenceWindowOnAnEarlyRun`, through the real `process_license_monthly_credit_refreshes`), with month 1's consumption at 10,000 raw and 2 seats:

| This month's run vs last month's | Refreshed | `total_credits_consumed` | Window moved | Budget left for new teachers |
|---|---|---|---|---|
| **2 s earlier** | 1 | **10000** (not reset) | **no** | **10000** of 20000 |
| 2 s later (control) | 1 | 0 | yes | 20000 |

**Impact.**
- `_enroll_teacher_internal` (`license_service.py:1433`) caps a newly added teacher's first grant at `max_seats × monthly_credits − total_credits_consumed`. For the rest of that month, teachers added through `add_teachers`/`add_teachers_batch` get a reduced or **0-credit** first month, which logs the "no monthly budget left" warning.
- The run's start time jitters either way day to day, so this hits roughly one month in two for the teacher who opened the window.
- When every teacher on a licence shares a due day (enrolled together), nobody else resets the window that month.
- It corrects itself the following month.
- Nothing customer-visible reads the field besides enrolment (no serializer exposes it).

**Suggested fix (one line) plus a test:** apply the same tolerance to the window's condition:
`Q(consumption_window_start__lte=refresh_due_by(now) - relativedelta(months=1))`.
This matches the teacher's due check exactly. My F1 probe (both offsets) can be adopted as the test.

### F2: the tolerance grants a month for a contract's last minutes (introduced by B1)
d5 excluded a due time **capped at** the cycle end. A due time **just before** the cycle end is not capped (`run start + 1 month < billing_cycle_end`), and the tolerance makes it due on the anniversary run:
- **Annual:** `F2AnnualGrantMinutesBeforeTheCycleEnds`. Due 2 minutes before the cycle ends; the run starts 4 minutes before. Result: **1 granted**, with a full month's MONTHLY bucket that **lives 3 min 59 s**. The renewal then rolls its unused credits into CARRY_OVER (capped by the plan's rules).
- **Licence:** `F2LicenceRefreshMinutesBeforeTheContractEnds`. Same shape through `process_license_monthly_credit_refreshes`: **1 refreshed**.
- **On the pre-fix tree (3f5fa73) both probes pass:** nothing is granted, because the due time was after the run's start.

**Reachability.**
- An annual subscription whose period starts between 02:00 and 02:05 UTC: its first due time (02:0x) falls within the tolerance of the 02:00 run, so its grant chain lands on 02:00 of the same day-of-month, just before the anniversary's 02:0x end. That is about 5 minutes of every 24 hours of signups (~0.35% of annual subscribers), once a year.
- The licence analogue is a contract ending 03:00–03:05 UTC.

**Suggested fix:**
- In both task filters, treat a contract ending within the tolerance as ended: `billing_cycle_end__gt=refresh_due_by(now)` and `license_subscription__billing_cycle_end__gt=refresh_due_by(now)`.
- Make the same change in the annual re-check (`billing_cycle_end <= refresh_due_by(now)`).
- Nothing legitimate is lost: any grant it skips would be a bucket for under 5 minutes of contract.
- My F2 probes (both paths) can be adopted as the tests.

## The SM's five points
**1. Does the grace make credits spendable twice? No.**
- Every grant site that can run inside the grace retires the old bucket before granting:
  - the mid-cycle grant, the licence helper and the renewal take the newest unprocessed MONTHLY bucket, whatever its expiry;
  - activation and the plan change take the live one.
- A bucket kept by the cleanup is expired, so the wallet's spendable total (`expires_at > now`) excludes it.
- **Probes `Q1SkippedRunAnnual` and `Q1SkippedRunLicence`** (the scheduled run skipped, the next day's run inside the grace): exactly one live MONTHLY bucket before and after, and after the run the spendable total is the new month plus the rollover. The old remainder is not counted.
- `Q2…test_a_kept_bucket_is_not_spendable`: a kept bucket gives a balance of 0.
- *(My first Q1 draft measured the balance an hour before the run's retirement moment; that was a probe bug, fixed and re-run. `baseline` is the corrected run.)*

**2. Does the cleanup guard keep buckets alive forever? No, for every edge I probed (`Q2EntitledEdges`, all pass):**
- a **deactivated licence**'s teacher is written off;
- an **inactive allocation** is written off;
- a teacher **removed through the real `remove_teacher_from_license`** has both the live bucket (expired by the removal) and the old one written off;
- a subscriber who **lapses** is written off on the next cleanup;
- **the kept bucket isn't spendable** (see 1).

Other edges, by reading:
- **Licence enrolment refuses** a teacher with an active individual subscription, so a removed teacher's school bucket can't be kept by a personal plan.
- **Two edges keep a bucket, by design, and the ERROR surfaces them:**
  - an individual subscription left `is_active` while Stripe is past_due, until `customer.subscription.deleted`;
  - an **OFFLINE** licence that is never renewed and never deactivated. `process_license_renewals` excludes OFFLINE licences, so their teachers' buckets are kept, with a daily ERROR per teacher, until a super admin deactivates the licence.

**3. Was the B2 test weakened? No.**
- The assertion still pins the bucket's expiry, now to `start + 1 month + 2 days` within 120 s.
- Mutant **B2a** (the Stripe annual conversion's bucket loses the grace) is **killed** by it (off by 172,800 s).
- Mutant **B2b** (the bucket lives the whole year, the regression the test exists for) is **killed** by it.

**4. The lost-months query: read-only with ids and amounts only, yes. No false positives on no-carry-over plans: NO.**
- `detect_monthly_rollovers_lost_to_cleanup.sql` is a single `SELECT` (d5's test asserts it writes nothing), and its output is ids, counts, raw amounts and timestamps.
- **Q4** (documenting): on a plan with `carry_over_percent = 0`, a cleanup write-off followed by the renewal's grant is **reported** (`rows=1`, 6,000,000 raw written off), although no carry-over was lost. The header says such rows mean "0 lost", but the output doesn't tell them apart.
- **Suggested:** add the owner's current plan's `carry_over_percent`, and an estimated `carry_over_lost_raw = LEAST(written_off × pct / 100, carry_over_max)`, taken from the active subscription's plan or the active allocation's licence plan. Then filter out, or flag, the rows where it's 0. The re-subscribe-within-3-days caveat stays.

**5. Are the monthly-plan renewal after 05:00 and `apply_immediate_plan_change` covered? Yes.**
- **The renewal:** `test_the_renewal_that_arrives_after_the_cleanup_still_rolls_it_over` passes at fc08945 and **fails on 3f5fa73** (my prefix run): the renewal's rollover was lost there. `process_rollover_and_renewal` selects the newest unprocessed MONTHLY bucket whatever its expiry, so it rolls over the kept bucket.
- **The plan change:** `FirstMonthGraceTests.test_an_annual_immediate_plan_change_gets_the_grace` covers the grace, and mutant R19 is killed.
- **A plan change landing between the due time and the grant**, which the tolerance makes minutes-wide, now gets a bucket that lives until the grant. Pre-fix, that bucket was already expired on creation. So the grace isn't worse there.
- The old bucket being left unprocessed is H-76 (the SM's separate row).

## Evidence
My probes are in `rollover_probe_test_vf1a_rollover_probe.py` (not committed). They reuse d5's fixtures, and the inherited d5 tests are switched off in each subclass. The harness is `rollover_harness_vf_mcg_run.py`. The module imports d5's classes, so d5's tests also run once more inside it.

| Check | Result |
|---|---|
| **Baseline** @ fc08945: my probes + d5's module + `test_trial_to_annual_conversion` | **46 tests, 3 failures, exactly F1 (2 s early), F2 annual, F2 licence.** Everything else OK: my Q1/Q2 probes, the F1 control and every d5 test. The Q4 print shows `rows=1` |
| **Prefix** @ 3f5fa73's `services.py`, `tasks.py`, `license_service.py` (my module, with d5's tests inside it) | **F2 annual and F2 licence pass** (not granted pre-fix), so F2 was introduced by the fix. F1's early run fails on "not refreshed" (the original race), and its control passes. d5's race and cleanup-guard tests fail as d5 reported, including the monthly-plan renewal's lost rollover. The 1 error is `process_mid_cycle_credit_grant(now=…)`, which doesn't exist pre-fix |
| **Guards (addendum 2), the 6 beta-line modules 0b listed**, @ fc08945: `AutoGrader.tests_redis_test_isolation`, `AutoGrader.tests_beat_health`, `classrooms.tests_teacher_access_sweep`, `classrooms.tests_course_roster_scope_sweep`, `assignments.tests_schema_extension`, `users.tests_schema_extension` | **49 OK.** d5's 64b7c8b run covered only the other 3 (`no_wildcard_invalidation`, `cache_invalidation_coverage`, `migration_rollback_defaults`). No production file changed between 64b7c8b and fc08945 (`git diff --stat 64b7c8b fc08945` outside docs and tests is empty), so together all 9 beta-line guards are covered |
| **Mutants (mine), 4/4 KILLED**, own DB, sha-checked restore | B2a and B2b (see Q3), killed by the updated B2 assertion. **Y1** (a deactivated licence still entitles) is killed **only by my** `test_a_deactivated_licences_teacher_is_written_off`. **Y2** (an inactive or removed allocation still entitles) is killed **only by my** `test_an_inactive_allocation_is_written_off` and `test_a_teacher_removed_through_the_real_path_is_written_off`. d5's battery has no test that a no-longer-entitled *licence* owner is written off |
| d5's battery | Round 2: 19/19 killed at 64b7c8b (cited) |
| Hooks | `pre-commit run --from-ref 3f5fa73 --to-ref fc08945` passes, and each of the 6 commits passes |
| Bundle 4 | `git merge-tree --write-tree e190f06 fc08945` is **clean** (exit 0) |

## For the rework round
1. **F1:** tolerance in the window condition, with my F1 test (both offsets).
2. **F2:** a contract ending within the tolerance counts as ended, in both task filters and the annual re-check, with my F2 tests (both paths).
3. **Q4:** the query outputs, or filters on, the plan's carry-over.
4. **N1 (tests):** adopt `Q2EntitledEdges`' licence cases. They are the only tests that kill Y1 and Y2.

The re-check will cover: my probe module and d5's module on the new tip, F1/F2 turned into assertions, the mutants again, the hooks, and merge-tree against bundle 4's tip. No regression, if the delta stays in the tested functions (rule 15).

Logs: `runs/rollover_{baseline_fc08945,guards_fc08945,prefix_3f5fa73,mutant_B2a,mutant_B2b,mutant_Y1,mutant_Y2}.log`.

---

## Re-check (round 2, narrow) @ **4da21c3** (code 4a80558), 2026-09-30: **VERIFIED**
The delta `fc08945..4da21c3` touches `billing/{license_service,services,tasks}.py`, d5's test module, and docs (the query and the evidence). **No new module**, so addendum 2 stays satisfied by round 1's 9 guards.
- **F1:** the window's reset uses `consumption_window_start__lte=refresh_due_by(now) - 1 month`, which matches the teacher's due check exactly.
- **F2:**
  - both task filters use `billing_cycle_end__gt=refresh_due_by(now)` (the licence's through `license_subscription__`);
  - the annual re-check uses `billing_cycle_end <= refresh_due_by(now)`.
- **Q4:** the query outputs `plan_id`, `carry_over_percent`, `max_bank` and `estimated_carry_over_lost_raw`, and leaves out rows where nothing was lost. It keeps rows whose plan can't be found (NULL) for review.
- **N1:** my F1, F2 and licence-edge probes are ported into d5's module.

The runs were in 0b's slot at 6G, everything on `test_vf_mcg_mut` (dropped after). d5's billing regression (1739 OK) is cited, not repeated.

| Check | Result |
|---|---|
| **Baseline:** my probes + d5's module @ 4da21c3 | **52 OK.** F1 at 2 s early: consumed reset to 0, window moved, budget 20000 of 20000. F2 annual: 0 granted. F2 licence: 0 refreshed. Q4 (0% plan): **rows=0**. New **Q4L** (licence path): reported against the **licence plan**, with the estimate equal to the rollover the refresh would have made (4000 written off, 2000 estimated at 50%), and no `@` |
| **Mutants (mine), 8/8 KILLED by d5's module ALONE** (without my probe), sha-checked restore, the query file included | **F1** (the reset without the tolerance): `test_a_run_2s_early_reopens_the_consumption_window`. **F2a** (annual filter): killed through the re-check ("1 already granted"). **F2b** (licence filter): `test_no_refresh_for_the_last_minutes_of_the_contract`. **F2c** (annual re-check): `test_the_service_refuses_the_last_minutes_of_the_contract`. **Y1**: `test_a_deactivated_licences_teacher_is_written_off`. **Y2**: the inactive-allocation and real-removal tests. **S1** (the query keeps nothing-lost rows): the 0% plan test. **S2** (the estimate ignores the percent): off by 4000 vs 2000 |
| d5's battery | 23/25 at 4a80558 (cited); R5/R6 are equivalent (below) |
| Hooks | `pre-commit run --from-ref fc08945 --to-ref 4da21c3` passes, and each of the 2 commits passes |
| Bundle 4 | `git merge-tree --write-tree e190f06 4da21c3` is **clean** (exit 0) |

### Rulings asked for
- **R5/R6 (the cycle-end CAP exclusion) are equivalent: keep the code.**
  - The annual path selects `next_credit_grant_at <= now + 5 min` together with `billing_cycle_end > now + 5 min`. So `next_credit_grant_at < billing_cycle_end` always holds for a selected row: a capped due time (`= billing_cycle_end`) would need `billing_cycle_end <= now + 5 min`, which F2 excludes.
  - The re-check has the same `now` and the same pair of conditions, so it holds there too.
  - Keep the exclusion anyway: it states the cap's intent where a reader looks for it, it costs nothing, and it still stands if the tolerance and F2's comparison are ever changed separately. Record R5/R6 as equivalent mutants, not test gaps.
- **Deviation (a) (the licence re-check keeps `billing_cycle_end <= now`) is acceptable, with no interleaving that matters.**
  - The filter and the re-check use the **same run-start `now`**.
  - A row the filter excludes never reaches the re-check.
  - For the re-check to let through a row that a tolerant re-check would refuse, the licence's end would have to move **into** `(now, now + 5 min]` between the filter and the lock. Renewals only extend the end, and deactivation is re-checked (`is_active`). That leaves only a super admin hand-editing a contract to end within minutes of the run.
  - Parity with the annual path is optional, not required.
- **Deviation (b) (no `carry_over_max` in the estimate) is correct, and my round-1 record was wrong** to say the rollover caps by it. `compute_capped_rollover` is `int(unused × pct / 100)`, limited only by `max_bank` room (which depends on the wallet's live carry-over at the time), and nothing in the rollover reads `carry_over_max`. The estimate is an upper bound, and the query shows `max_bank` beside it.

Logs: `runs/rollover_r2_{baseline_4da21c3,mutant_F1,F2a,F2b,F2c,Y1,Y2,S1,S2}.log`. Probe: `rollover_probe_r2_test_vf1a_rollover_probe.py`. Harness: `rollover_harness_r2_vf_mcg_run.py`.
