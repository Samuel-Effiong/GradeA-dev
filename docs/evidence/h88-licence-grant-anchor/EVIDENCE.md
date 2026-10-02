# H-88, H-93, H-81: licence refreshes on the anchor day, the consumption window, owed grants

**Author:** d5. **Branch:** `task/h88-licence-grant-anchor`, base-updated by
0b onto `task/beta-batch-5` `058a9507` (clean merge, `37344615`). Bundle 6.
Three changes on one branch, each in its own commits so each can be verified
and reverted separately.

| Commit | Row | What |
|---|---|---|
| `ec7e1caa` | H-88 | the e2e tests (red); they include H-93's red test |
| `db287b54` | H-88 | the fix, migration `billing/0073`, the anchor's property tests |
| `b03f5d7c` | H-93 | `refresh_timing.consumption_window_is_over` (the rule only) |
| `4d92609e` | H-93 | the refresh uses the rule; its property tests |
| `f800fa80` | H-81 | the tests (red) |
| `5e533159` | H-81 | the detection |
| `a175c2a9` | H-88 | three more tests; `run_mutants.py` |
| `37344615` | | base update onto `058a9507` (0b) |

`b03f5d7c` is half a change by my mistake: a script step failed and the
commit went through with only `refresh_timing.py` staged. `4d92609e`
completes it. The two together are the whole of H-93.

## H-88: licence refreshes are anchored to the allocation's own month

1. **Before:** `_refresh_teacher_credits` set the next due time to `now + 1
   month`. A 31 January enrolment clamps to 28 February and stays on the
   28th, which is 12 refreshes plus the enrolment grant in a 12-month
   contract: 13 months of credits for 12 paid. Every due time also drifted
   by the run's lateness. (H-82 fixed the same chain for individual annual
   plans.)
2. **After:**
   - `SchoolCreditAllocation.grant_anchor_at` (nullable, migration 0073) is
     the moment a teacher's months are counted from. It is written at
     enrolment, re-enrolment and reactivation, for the admin allocation
     (create and reactivate), and at both renewals (Stripe and offline),
     which restart every teacher's month.
   - The refresh takes the next due time from H-82's
     `next_monthly_grant(anchor, served due, licence cycle end)`, unchanged.
   - An outage is caught up one refresh per daily run, each with a WARNING
     (ids only). A caught-up bucket lives a month from its grant and never
     past the contract's end.
3. **Rows older than the field** (`refresh_timing.allocation_anchor`):
   - the fallback is the later of the row's `created_at` and the licence's
     `billing_cycle_start`, used when the row's due time lies within 7 days
     of one of its points;
   - otherwise (a teacher re-enrolled since the last renewal, at a time
     nobody stored) the due time itself is the anchor, so the teacher keeps
     their rhythm with no irregular interval;
   - the refresh stores the anchor it resolved (SM-approved for both
     cases). **The column fills in lazily: after the deploy a NULL means
     "not refreshed yet". Enrol, re-enrol, reactivate and renewal overwrite
     it.** No data migration.
   - An already-drifted row (31 January licence, due on 28 March) moves to
     30 April, not 31 March: no second refresh three days later.
4. **Tests:** `test_licence_grant_anchor.py` drives the real task with a set
   clock; `test_allocation_anchor.py` is property-style (every start day,
   drifted rows, re-enrolled rows, weekly runs).

**Can a catch-up exceed what the school paid for (the SM's check)?** No.
- Each refresh grants exactly `allocation.monthly_allocation`, once per owed
  month, and the chain stops when it is current.
- The carry-over comes out of the previous bucket's unused credits (the
  plan's percentage, capped by `max_bank`), so it adds nothing beyond what
  was granted.
- The consumption window reopens once: the conditional UPDATE fires on the
  first catch-up refresh and is a no-op on the following days.
- `total_credits_consumed` only caps a new teacher's first bucket at
  enrolment; it does not gate refreshes.
- Test: `test_a_catch_up_never_grants_more_than_the_months_paid_for` (12
  monthly grants, 12 × the allocation, one reopening over three days).

**Migration and rollback.** `billing/0073` is one `AddField`, nullable, no
default: rule 11 needs no DB default. Old code ignores the column, so a
rollback is a plain column drop with no `SET DEFAULT`. Epic A has no billing
migration beyond 0072; note 0073 for the next merge-down.

## H-93: the consumption window reopens after a month less the snap

1. **Before:** the window reopened only when it had been open a full
   calendar month. A refresh on a clamped date (31 January, then 28
   February; 31 March, then 30 April) comes less than a calendar month
   after the last one, so the window stayed shut and two months of usage
   counted against one month's budget. A teacher enrolled then got a
   reduced or empty first bucket. Before H-88 this happened once per
   29th–31st start; an anchored chain would repeat it after every 31-day
   month.
2. **After:** `consumption_window_is_over(now)`: a month, less
   `ANCHOR_SNAP` (7 days).
3. **Reach:** when the licence's `total_credits_consumed` is reset. It
   affects only the cap on a new enrolment's first bucket.
4. **Tests:** `test_the_consumption_window_reopens_at_every_months_refresh`
   (e2e, in `ec7e1caa`) and `ConsumptionWindowTests` (over at every refresh
   of any anchored chain; not over for three weeks after it opens).

**Known edge, accepted by the SM.** After an outage the window reopens on
the first catch-up day. The next regular refresh, about two weeks later,
does not reopen it, so that one window runs about six weeks. It errs
against over-granting, touches only a new enrolment's first bucket, and
corrects itself the month after.

## H-81: a renewal reports the monthly grants never made

1. **Before:** the grant tasks stop serving a due time once the cycle has
   ended. If Beat was down from a grant's due time to the cycle's end, that
   grant was never made and nothing said so (1a's H-65 N2; 1a's H-82 O2 for
   a contract's last grant).
2. **After:** when a renewal replaces the due time, a due time more than 7
   days before the old cycle's end (or before now, for an early renewal) is
   reported at ERROR with ids only: the subscription or allocation, the
   user, the licence, the unserved due time, the cycle end and the number
   owed (`refresh_timing.grants_owed`).
   - Sites: `SubscriptionService.process_rollover_and_renewal`, and
     `process_license_renewal` and `process_offline_renewal` through
     `_report_owed_refreshes`.
   - **It only detects. Nothing is granted** (SM ruling); support credits
     the customer by hand.
3. **Not covered:** a subscription or licence that ends without renewing
   never reaches a renewal, so it is not reported (SM-accepted).
4. **Tests:** `test_owed_grant_detection.py` (the last grant, three grants,
   a chain served to the end, a row drifted to the 28th, an early offline
   renewal, and that the renewal still goes on) and `GrantsOwedTests`.

## Gates
One chain (`chain.sh 37344615 058a9507`) on the frozen tip, in one slot
granted by 0b, as the team's only run on the machine. Status: `chain.status`.

| Gate | Result | Log |
|---|---|---|
| Repro: the tip's two e2e modules over `058a9507`'s `license_service.py`, `services.py`, `refresh_timing.py` and `models.py`, without migration 0073 (h66-repro worktree, own DB) | 27 tests, 13 failures, 7 errors | `repro_37344615_tests_over_058a9507_code.log` |
| (a) 12 modules | 165 OK | `a_modules_37344615.log.gz` |
| (b) battery (`test_h88_mut`), two parts | both baselines green; M 14/14, W + O 11/11 killed; every restore sha-verified | `b_mutation_battery_M_37344615.log`, `b_mutation_battery_WO_37344615.log`, `logs/` (the larger ones gzipped), `results.tsv` |
| (c) billing + users + classrooms + dashboard + the 9 guards | 3458 OK (skipped=8), wall 478 s | `c_apps_guards_37344615.log.gz` |

The repro's 7 errors are the tests that touch the new column. The 7 tests
that pass on the old code are the two H-88 controls (an enrolment on the
15th, and the mid-cycle enrolment) and H-81's five "nothing owed" cases.

(a)'s modules: `test_licence_grant_anchor`, `test_allocation_anchor`,
`test_owed_grant_detection`, `test_license_multi_month_budget`,
`test_monthly_rollover_cleanup_race`, `test_beat_lock_catch_up`,
`test_license_renewal_partial_failure`, `test_annual_grant_anchor`,
`test_next_monthly_grant`, `test_annual_mid_cycle_grants`,
`test_license_service`, and H-80's guard `test_logs_carry_no_email` (0b's
request: the branch adds logger calls to a file it guards).

(c)'s scope follows rule 15's addendum for a model change: every app that
uses `SchoolCreditAllocation`.

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as `systemd-inhibit
--what=idle:sleep … --mode=block systemd-run --user --scope -p MemoryMax=6G
-p MemorySwapMax=0 nice -n 10 timeout -k 60 1800` (rules 12, 13, 16). (c)
ran with `--parallel 2 --verbosity 2` through a timestamper.

**Rule 16's prefix.** The rule changed to `idle:sleep:handle-lid-switch` at
13:15, after every step of this chain had started ((c) began at 13:10), so
the whole chain ran with `idle:sleep` only. `journalctl` shows no "Lid
closed" since 13:00, and each step's wall time matches its test time.

**Rule 17.** Both battery parts and their baselines ran with
`PYTHONDONTWRITEBYTECODE=1` (set for the runner and passed to every test
subprocess), and the runner deleted `billing/__pycache__` in its worktree
before each baseline, before each mutant and after each restore. Each part
ran inside its own wrapper: one 6G scope and one 1800 s timeout per part
(237 s and 236 s used).

## Mutants
| Id | Guards | Result |
|---|---|---|
| M1 | an enrolment stores its anchor | killed |
| M2 | the renewal task restarts the teacher's month | killed |
| M3 | the offline renewal restarts the teacher's month | killed |
| M4 | the chain is computed from the anchor, not the served due | killed |
| M5 | the helper is fed the served due, not now | killed |
| M6 | the fallback is the later of creation and the cycle start | killed |
| M7 | a due time off the fallback's chain is its own anchor | killed |
| M8 | the fallback's snap window covers a due time before its point | killed |
| M9 | the refresh stores the anchor it resolved | killed |
| M10 | a caught-up refresh logs a WARNING | killed |
| M11 | a caught-up bucket lives from its grant time | killed |
| M12 | a caught-up bucket ends by the contract end | killed |
| M13 | a new admin allocation stores its anchor | killed |
| M14 | a reactivated admin allocation restarts its month | killed |
| W1 | H-93: over after a month less the snap, not a full month | killed |
| W2 | H-93: not over within three weeks | killed |
| W3 | H-93: the refresh uses the rule | killed |
| O1 | H-81: the licence renewals log the owed refreshes | killed |
| O2 | H-81: an early renewal's future refreshes are not owed | killed |
| O3 | H-81: a due time within the snap of the end is not owed | killed |
| O4 | H-81: every owed grant is counted | killed |
| O5 | H-81: the renewal task reports | killed |
| O6 | H-81: the offline renewal reports | killed |
| O7 | H-81: the individual renewal logs the owed grants | killed |
| O8 | H-81: the licence line names the allocation's user by id | killed |

## For the verifier
- **The stored anchor.** The refresh writes `grant_anchor_at` on a row that
  had none. Worth a probe: rows with a NULL anchor in each state (on the
  fallback's chain, drifted, re-enrolled on another day), and that a
  renewal or re-enrolment overwrites a stored anchor.
- **The window commits** (`b03f5d7c` + `4d92609e`) are separable from H-88:
  reverting them restores the full-calendar-month rule and turns only
  `test_the_consumption_window_reopens_at_every_months_refresh` and
  `ConsumptionWindowTests` red.
- **Mixed anchors.** Teachers under one licence have different anchor days;
  the window is reopened by whichever refresh first finds it a month (less
  7 days) old.
- **H-81 on rows with no anchor** uses the same fallback as the refresh.
- The existing refresh tests (`test_monthly_rollover_cleanup_race`,
  `test_license_multi_month_budget`, `test_beat_lock_catch_up`) create
  allocations with no anchor and passed unchanged.
- The logs' addresses are test fixtures only.
