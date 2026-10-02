# H-88, H-93, H-81: licence refreshes on the anchor day, the consumption window, owed grants

**Author:** d5. **Branch:** `task/h88-licence-grant-anchor`, base-updated by
0b onto beta `74bfc8d3` (clean merge, `d178f100`). Bundle 6.
Three changes on one branch, each in its own commits so each can be verified
and reverted separately.

**Two gates.** The first gate ran on `37344615` and was all green. 1a's
static pre-review then found F1 and O1 (below), which the SM ruled into the
branch, so the branch was re-gated in full on `8ea91e21`. The first gate is
superseded; its logs are kept in `first_gate_37344615/`.

| Commit | Row | What |
|---|---|---|
| `ec7e1caa` | H-88 | the e2e tests (red); they include H-93's first red test |
| `db287b54` | H-88 | the fix, migration `billing/0073`, the anchor's property tests |
| `b03f5d7c`, `4d92609e` | H-93 | the first rule ("a month less 7 days"), replaced by `a26c8b5e` |
| `f800fa80` | H-81 | the tests (red) |
| `5e533159` | H-81 | the detection |
| `a175c2a9` | H-88 | three more tests; `run_mutants.py` |
| `37344615` | | base update onto `058a9507` (0b); first gate; evidence `84ec0ddc` |
| `fc4fa05a` | H-88 | 1a's F1: tests (red) |
| `cc22f6e2` | H-88 | 1a's F1: the fix |
| `117e4afb` | H-93 | 1a's O1: tests of the licence-point rule (red) |
| `a26c8b5e` | H-93 | 1a's O1: the licence-point rule |
| `d178f100` | | base update onto beta `74bfc8d3` (0b) |
| `8ea91e21` | H-81 | 1a's late-renewal test, mutants O9 and O10; the first gate's logs moved |

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
   - **A stored anchor holds only while the due time is on its chain (1a's
     F1).** If something moves `next_credit_grant_at` and not the anchor,
     the same 7-day test fails and the due time becomes the anchor, and is
     stored. 1a's example: anchor 5 January, due time moved to 28 March:
     the next refresh is 28 April, not 5 April. Two things move the due
     time alone: the QA time-travel tool (it now clears the anchor with the
     due time) and old code after a rollback.
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

**After a code-only rollback and a roll-forward** (the column kept): the old
code renews and re-enrols without touching the anchor, so some rows come
back with a stale one. No manual NULLing is needed. At each such row's next
refresh its due time is off the stale anchor's chain, so the due time
becomes the anchor and is stored: the row keeps the rhythm the old code
gave it, with no extra refresh (`MovedDueTimeTests`).

## H-93: the consumption window reopens on the licence's monthly points

1. **Before:** the window reopened only when it had been open a full
   calendar month. A refresh on a clamped date (31 January, then 28
   February; 31 March, then 30 April) comes less than a calendar month
   after the last one, so the window stayed shut and two months of usage
   counted against one month's budget. A teacher enrolled then got a
   reduced or empty first bucket. Before H-88 this happened once per
   29th–31st start; an anchored chain would repeat it after every 31-day
   month.
2. **After:** `refresh_timing.latest_monthly_point(licence cycle start, run
   time)`. The window reopens when it was opened before the licence's
   latest monthly point (`billing_cycle_start` + k months, with the
   refresh's 5-minute due tolerance). So the first teacher refresh after
   each licence point reopens it, and every other refresh in that licence
   month leaves it alone.
   - 12 windows a year whatever days the teachers are anchored on.
   - A clamped date is a point like any other.
   - A catch-up reopens once, and the licence's next point reopens on
     time.
   - A renewal sets the window's start to the new cycle start, so the
     points move with it.
3. **Reach:** when the licence's `total_credits_consumed` is reset. It
   affects only the cap on a new enrolment's first bucket.
4. **Tests:** `ConsumptionWindowOnLicencePointsTests` (teachers on the 1st
   and the 25th share 12 windows; a teacher off the licence's day still
   gives 12; after an outage the next point reopens on time; a renewal
   mid-year; a point within the due tolerance),
   `test_the_consumption_window_reopens_at_every_months_refresh`,
   `test_a_catch_up_never_grants_more_than_the_months_paid_for`, and
   `LatestMonthlyPointTests`.

**The first rule and why it was replaced.** `b03f5d7c` + `4d92609e` made the
window count as over after "a month less 7 days". 1a's O1: with teachers
anchored on different days (the 1st and the 25th) that reopened the window
when it was about 24 days old, some 15 times a year. The SM ruled the
licence-point rule in; `consumption_window_is_over` is gone. The six-week
window after an outage, which the first rule had and the SM had accepted as
an edge, no longer exists.

`b03f5d7c` was half a change by my mistake (a script step failed and the
commit went through with only `refresh_timing.py` staged); `4d92609e`
completed it. Both are now history under `a26c8b5e`.

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
   renewal, a renewal 20 days late of a chain served to the end (1a's
   mutant: the count runs to the cycle's end, not to the renewal's
   moment), and that the renewal still goes on) and `GrantsOwedTests`.

## Gates
The re-gate ran on the frozen tip `8ea91e21` (beta `74bfc8d3` as base), in
slots granted by 0b. Status: `chain.status`.

| Gate | Result | Log |
|---|---|---|
| Repro: the tip's two e2e modules over `74bfc8d3`'s `license_service.py`, `services.py`, `refresh_timing.py`, `models.py` and `qa_time_travel.py`, without migration 0073 (h66-repro worktree, own DB) | 37 tests, 15 failures, 9 errors | `repro_8ea91e21_tests_over_74bfc8d3_code.log` |
| (a) 14 modules | 207 OK | `a_modules_8ea91e21.log.gz` |
| (b) battery (`test_h88_mut`), two parts | both baselines green; M 16/16, W + O 15/15 killed; every restore sha-verified | `b_mutation_battery_M_8ea91e21.log`, `b_mutation_battery_WO_8ea91e21.log`, `logs/` (the larger ones gzipped), `results.tsv` |
| (c) billing + users + classrooms + dashboard + the 9 guards | 3469 OK (skipped=8), wall 384 s | `c_apps_guards_8ea91e21.log.gz` |

The repro's 9 errors are the tests that touch the new column. The 13 tests
that pass on the old code are controls and "nothing owed" cases.

(a)'s modules: `test_licence_grant_anchor`, `test_allocation_anchor`,
`test_owed_grant_detection`, `test_license_multi_month_budget`,
`test_monthly_rollover_cleanup_race`, `test_beat_lock_catch_up`,
`test_license_renewal_partial_failure`, `test_annual_grant_anchor`,
`test_next_monthly_grant`, `test_annual_mid_cycle_grants`,
`test_license_service`, H-80's guard `test_logs_carry_no_email`, and
`test_qa_time_travel_clock` and `test_qa_time_travel_guardrail` (the folds
edit `billing/qa_time_travel.py`).

**The hand-built fixtures under the new window rule.**
`test_license_multi_month_budget` and `test_monthly_rollover_cleanup_race`
build licences and windows by hand and were written against the
full-calendar-month rule. Both pass unchanged under the licence-point rule:
no fixture date had to move.

(c)'s scope follows rule 15's addendum for a model change: every app that
uses `SchoolCreditAllocation`.

**The pause.** 0b asked for the slot between steps, for two short Epic A
runs. The chain script checks for a pause file before each step; it stopped
cleanly before (c) at 15:50 and (c) ran under a new grant on the same frozen
tip (`c_only.sh`). `chain.status` has the times.

**How the runs were made.** Every run used `--settings=settings_worktree`
and an empty `EXEMPT_EMAIL_DOMAINS`, wrapped as `systemd-inhibit
--what=idle:sleep:handle-lid-switch … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16). (c) ran with `--parallel 2 --verbosity 2` through a
timestamper, under `flock ~/.machine-fullsuite.lock`.

**Rule 17.** Both battery parts and their baselines ran with
`PYTHONDONTWRITEBYTECODE=1` (set for the runner and passed to every test
subprocess), and the runner deleted `billing/__pycache__` in its worktree
before each baseline, before each mutant and after each restore. Each part
ran inside its own wrapper: one 6G scope and one 1800 s timeout per part
(276 s and 247 s used).

**The first gate (superseded), on `37344615`:** repro 27 tests, 13 failures,
7 errors; (a) 12 modules 165 OK; (b) 25/25 killed; (c) the same four apps
and guards, 3458 OK (skipped=8). It ran under the old rule 16 prefix
(`idle:sleep` only; the rule changed while it was running, and the journal
showed no lid close). Logs: `first_gate_37344615/`.

## Mutants
| Id | Guards | Result |
|---|---|---|
| M1 | an enrolment stores its anchor | killed |
| M2 | the renewal task restarts the teacher's month | killed |
| M3 | the offline renewal restarts the teacher's month | killed |
| M4 | the chain is computed from the anchor, not the served due | killed |
| M5 | the helper is fed the served due, not now | killed |
| M6 | the fallback is the later of creation and the cycle start | killed |
| M7 | a due time off the anchor's chain is its own anchor | killed |
| M8 | the snap window covers a due time before its point | killed |
| M9 | the refresh stores the anchor it resolved | killed |
| M10 | a caught-up refresh logs a WARNING | killed |
| M11 | a caught-up bucket lives from its grant time | killed |
| M12 | a caught-up bucket ends by the contract end | killed |
| M13 | a new admin allocation stores its anchor | killed |
| M14 | a reactivated admin allocation restarts its month | killed |
| M15 | 1a's F1: a stored anchor is used only while the due time is on its chain | killed |
| M16 | 1a's F1: the QA time-travel tool clears the anchor with the due time | killed |
| W1 | H-93: a run at a licence point counts that point | killed |
| W2 | H-93: a point within the due tolerance of the run reopens | killed |
| W3 | H-93: the points are the licence's, not the teacher's | killed |
| W4 | H-93: the window reopens on the points, not after a calendar month | killed |
| W5 | H-93: a refresh inside a licence month leaves the window alone | killed |
| O1 | H-81: the licence renewals log the owed refreshes | killed |
| O2 | H-81: an early renewal's future refreshes are not owed | killed |
| O3 | H-81: a due time within the snap of the end is not owed | killed |
| O4 | H-81: every owed grant is counted | killed |
| O5 | H-81: the renewal task reports | killed |
| O6 | H-81: the offline renewal reports | killed |
| O7 | H-81: the individual renewal logs the owed grants | killed |
| O8 | H-81: the licence line names the allocation's user by id | killed |
| O9 | H-81 (1a's mutant): a late licence renewal counts to the cycle end | killed |
| O10 | H-81: a late individual renewal counts to the cycle end | killed |

## For the verifier
- **F1, the stored anchor.** `allocation_anchor` now tests the stored anchor
  as it tests the fallback: within 7 days of one of its points, or the due
  time is the anchor. Worth a probe: a due time moved by exactly the edge
  amounts, a renewal or re-enrolment after a moved due time, and the QA
  tool (`rewind_license_subscription`, mode `mid_cycle_grant`).
- **O1, the window.** The rule reads the licence's `billing_cycle_start`,
  not any teacher's anchor. Reverting `a26c8b5e` (and `117e4afb`) restores
  "a month less 7 days". Worth a probe: licences whose teachers all enrolled
  after the licence began, and a renewal (both kinds) between two points.
- **H-81 on rows with no anchor** uses the same resolver as the refresh.
- **A late renewal** of a fully served chain reports nothing (your mutant
  Y5 is my O9; O10 is the same at the individual site).
- The logs' addresses are test fixtures only.
