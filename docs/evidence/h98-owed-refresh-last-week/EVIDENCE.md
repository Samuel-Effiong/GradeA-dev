# H-98: an unserved refresh in a cycle's last week is reported when it is on the stored anchor

**Author:** d5. **Branch:** `task/h98-owed-refresh-last-week`, off
`task/beta-batch-7`; base updated onto its tip `27b0d2e0` (`8513ae0d`).
For the bundle after bundle 7. **Verifier:** 1a. Detection only: one more
ERROR log line at a licence's renewal. No migration, no settings change,
no change to what is granted.

Source: 1a's N1 on H-88's verification; design approved by the SM on
2026-10-05 with the one-full-day margin.

## The change (4 points)
1. **Before:** at a licence's renewal, H-81's count (`grants_owed` in
   `billing/refresh_timing.py`) ignored every due time within 7 days
   (`ANCHOR_SNAP`) of the cycle's end, so that a row drifted by the old
   chain is not reported. A real monthly point in that week, left unserved
   by an outage that ran to the end, was not reported either.
2. **After:** a due time in that week counts as one owed grant when all
   three hold:
   - the caller says the anchor is the allocation's STORED anchor
     (`on_stored_anchor=True`; the default is False);
   - the due time is exactly a point of that anchor (anchor plus whole
     months: `is_anchor_point`), not merely near one;
   - it came due at least one full day (`OWED_MARGIN`) before the end.
3. **The caller** (`_report_owed_refreshes` in
   `billing/license_service.py`) makes the claim only when the allocation
   has a stored anchor and `allocation_anchor` returned that same anchor.
   A fallback anchor, or the due time itself, is not a stored anchor.
4. **Unchanged:** everything outside the last week; a row with no stored
   anchor; the individual-plan caller (it passes no claim). The log line
   is H-81's own, ids only.

## What stays unreported, on purpose
**A point due in the last 24 hours before the cycle's end.** The refresh
runs once a day and serves a due time only on a run between that time and
the cycle's end. A point due later than one day before the end may have
had no run at all with Beat healthy, so reporting it would say Beat was
down when it was not. An outage that swallowed such a point is therefore
not reported by H-98.

That case was logged as its own row, **H-117**, and read: with Beat
healthy such an allocation never gets that cycle's final monthly refresh.
The SM closed it on 2026-10-05 as by design, no entitlement lost. H-98
does not change it.

Also not covered: a drifted row (near a point, not on it) is still
ignored, as H-81 intended; and H-98, like H-81, reports and does not make
the grant up.

| Commit | What |
|---|---|
| `ff2edee7` | tests (red): `GrantsOwedInTheLastWeekTests`, `TheRenewalReportsTests` in `billing/tests/test_allocation_anchor.py` |
| `19a15de7` | the change; the mutation runner |
| `8513ae0d` | base update: merge of `task/beta-batch-7` `27b0d2e0` (no conflict) |
| `9e211ca4` | the mutation runner only: each run straight to a file; a kill needs the run's "Ran" line and named failing tests |

## Gates
On the frozen tip `8513ae0d`, 2026-10-05, under 0b's grants; the battery again on `9e211ca4` (the runner only differs). Times are
the shell's clock; in `chain.status` each time is the step's END. Nothing
of H-98 had been run under Django before these runs.

| Gate | Start – end | Result | Log |
|---|---|---|---|
| Repro: `billing.tests.test_allocation_anchor` at `ff2edee7` (h78-repro worktree) | 15:22:14 – 15:22:24 | RED as expected: 28 tests, failures=1, errors=480, exit 1 | `repro_ff2edee7.log.gz` |
| (a) 34 labels, serial | 15:22:25 – 15:26:32 | GREEN: 470 tests, OK | `a_modules_8513ae0d.log.gz` |
| (b) battery (`test_h98_mut`), 11 mutants, **in rule 18 form, on `9e211ca4`** | 15:51:56 – 15:54:11 | 11/11 killed, restore verified, none BROKEN; every mutant's failing tests are the expected set; each log has its "Ran 28 tests" line | `b_mutation_battery_9e211ca4.log`, `battery_9e211ca4.tar.gz` (raw output of each run in `logs/raw/`), `expected_kills.py.txt`, `expected_kills_9e211ca4.txt`, `battery2.status` |
| (b) the first battery, on `8513ae0d`, **in pipe form: kept, not the gate** | 15:26:32 – 15:28:38 | the same result: 11/11, the same sets | `b_mutation_battery_8513ae0d.log`, `battery_8513ae0d.tar.gz`, `expected_kills_8513ae0d.txt` |
| (c) the owning app, billing, `--parallel 2` | 15:29:40 – 15:35:02 | GREEN: 2109 tests, OK, no stall | `c_billing_p2_8513ae0d.log.gz`, `iso.status` |

**The repro is weaker than its numbers look, and I say so.** The 480
errors are one error, `TypeError: grants_owed() got an unexpected keyword
argument 'on_stored_anchor'`, across nine tests and their subtests: for
the pure function the repro shows that the argument does not exist yet,
not the old behaviour. The one behavioural failure is
`test_a_point_of_the_stored_anchor_in_the_last_week_is_reported`
(`0 != 1`): the renewal, through the real caller, reporting nothing for
the case H-98 is about. The old behaviour of the function itself is
pinned at the tip instead, by
`test_without_a_stored_anchor_it_is_ignored_as_before`.

**(a)'s labels.** The modules around the change: `billing.tests`
`test_allocation_anchor`, `test_owed_grant_detection` (H-81's own tests),
`test_licence_grant_anchor`, `test_next_monthly_grant`,
`test_annual_grant_anchor`, `test_license_service`,
`test_license_renewal_partial_failure`, `test_renewal_guards`,
`test_license_multi_month_budget`, `test_monthly_rollover_cleanup_race`,
`test_beat_lock_catch_up`, `test_license_cancellation`,
`test_trial_to_annual_conversion`. The repo-wide guard list named in
0b's grant: `AutoGrader` `tests_no_wildcard_invalidation`,
`tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`,
`tests_redis_test_isolation`, `tests_beat_health`, `tests_beat_locks`,
`tests_management_commands_are_commands`, `tests_error_messages`,
`tests_no_pii_in_logs`, `tests_log_scrubbing`, `tests_sentry_scrubbing`,
`tests_redis_hygiene_databases`, `tests_redis_hygiene`,
`tests_patient_test_stream`, `tests_pool_worker_sigterm`;
`billing.tests.test_logs_carry_no_email`,
`billing.tests.test_log_scrubbing_end_to_end`;
`classrooms.tests_teacher_access_sweep`,
`classrooms.tests_course_roster_scope_sweep`;
`assignments.tests_schema_extension`, `users.tests_schema_extension`.
The guard labels were added to the gate script after the request, on
0b's condition, before the run started; the tree was not touched.

**H-81's existing tests** (`test_owed_grant_detection`) were read before
the run and found consistent with the change, including the row drifted
to the 28th: it has a stored anchor but is near a point, not on it, and
stays unreported. They are green in (a).

**(c)'s status line** says `ok=2108` against 2109 tests run. That figure
counts result lines ending in "ok"; one test's "ok" is on a line of its
own because a log line was written between its name and its result
(`test_none_assignment_denied`). Nothing failed, errored or was skipped.

**Every mutant's failing tests are the expected set** (0b's standing
check). The sets were written down before any run
(`expected_kills.py.txt`, 14:56) and all eleven matched. The "failures="
figures in the battery log count subtests; the table below counts tests.

**How the runs were made.** `--settings=settings_worktree`, an empty
`EXEMPT_EMAIL_DOMAINS`, `systemd-inhibit --what=idle:sleep:handle-lid-switch
… --mode=block systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0
nice -n 10 timeout -k 60 1800` (rules 12, 13, 16). Rule 18: the repro,
(a), (c) and the battery of `9e211ca4` wrote stdout and stderr straight
to a file with stdin from `/dev/null`; (c) ran under `flock ~/.machine-fullsuite.lock` with `--verbosity 2`,
`PYTHONFAULTHANDLER=1` and the 300 s log-silence watchdog, its timestamps
added from the side by a reader of the log file. Rule 17: the battery and
its baseline ran with `PYTHONDONTWRITEBYTECODE=1`, and the runner deleted
`__pycache__` in each mutated module's package before the baseline,
before each mutant and after each restore. **The battery is on the final
test module:** `billing/tests/test_allocation_anchor.py` has not changed
since `ff2edee7`. The gate scripts stop at the first failed step
(`set -euo pipefail`) and assert the frozen tip and a clean tree first.

**Why the battery ran twice (a correction of mine).** This file first
said that every run wrote straight to a file. That was not true of the
first battery's inner runs: the runner collected each mutant's test
output through a pipe that it read continuously (`capture_output`), and
only the battery's own summary went to a file. No renderer is involved
(one billing module) and all twelve runs ended normally, but rule 18
says every run. I raised it; the SM ruled a re-run in file form.
`9e211ca4` changes the runner only: each run's stdout and stderr go to
a file, stdin is the null device, and the file is read after the run
has ended. A mutant now counts as killed only with the run's own "Ran"
line, named failing tests and no load failure, never on a non-zero exit
alone, and the "Ran" line is kept in its log (0b). The second battery
is the gate; it gave the same result as the first, against the same
expected sets, which were written before either ran. The test module
and the code are the same at both tips.

## Mutants
| Id | Guards | Failing tests (expected = actual) |
|---|---|---|
| R1 | only a STORED anchor is trusted in the last week | 5 |
| R2 | the due time is exactly a point of the anchor, not near one | 1 |
| R3 | it lies a full day before the end (any time before the end counts) | 1 |
| R4 | exactly one full day is enough (the boundary) | 2 |
| R5 | a chain served to the end owes nothing (the margin removed) | 3 |
| R6 | the margin is one day | 2 |
| R7 | `is_anchor_point` is equality with a point | 1 |
| C1 | the renewal says "stored" only when the stored anchor is the one in use | 1 |
| C2 | a row with no stored anchor does not claim one | 2 |
| C3 | the renewal passes the claim at all | 1 |
| C4 | the individual-plan caller claims no stored anchor | 1 |

## For the verifier
- The design question worth a second mind is the margin: `next_due +
  OWED_MARGIN <= until`, exactly one day counts (R4, R6 hold the
  boundary).
- `is_anchor_point` walks month by month from the anchor, the same way
  `latest_monthly_point` does, so clamped month ends (the 29th to the
  31st) are compared as the chain itself produces them.
  `test_every_day_of_the_month_clamped_dates_too` covers every day of
  the month.
- The extra count is at most one: after the loop, `next_due` is the
  first due time inside the last week, and no second monthly point fits
  in 7 days.
- Worth a probe: an allocation whose stored anchor is set but whose due
  time has drifted (so `allocation_anchor` falls back): C1's case,
  through the real renewal.
- Rule 15: please don't repeat (c).
