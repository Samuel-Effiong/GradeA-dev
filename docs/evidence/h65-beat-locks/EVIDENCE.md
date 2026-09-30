# H-65: one run at a time for each Celery Beat task

Branch `task/h65-beat-locks`. Author: d5 (Hardening). Gated tip: **adf8fe0**
(base-updated by 0b onto bundle 4's final tip 4e629d4 as 9887b25).

## The defect

Nothing stopped two runs of the same scheduled task from overlapping: a
redeploy near a fire time, Beat on more than one replica, or a manual run.
For the money tasks an overlap acts twice on stale copies (the double-EXPIRE
race fixed by task/expire-bucket-race 2ba72a6 had no other path); for the
email senders and paid probes it means duplicate emails or double Stripe/AI
spend.

## The fix (2e9dcb0, e5a94d0)

`AutoGrader/beat_locks.py`: `@single_instance(max_hold=...)`, applied under
`@shared_task`.

- Acquire: Redis `SET NX PX` with a per-run token (task id + uuid). Not a
  Postgres advisory lock: production runs behind pgbouncer in transaction
  pooling mode.
- Release: Lua compare-and-delete, so a lapsed run never deletes a newer
  run's lock.
- A heartbeat thread extends a 300 s TTL (Lua compare-and-extend) while the
  run is alive, up to `max_hold`. `max_hold` is below each schedule interval
  (EVERY_5_MIN 4 min, HOURLY 50 min, PRICE_SWEEP 30 min, EVERY_6_HOURS 5 h,
  DAILY 6 h, WEEKLY 12 h), so a killed worker's lock lapses and a hung run
  can't swallow the next scheduled one.
- Held lock: the run skips and logs a WARNING (task name, key, holder id).
- Redis error: the run **fails closed**, skips and logs an ERROR.
- A run that finishes after its lock lapsed logs an ERROR ("finished without
  its lock").

| App | Locked tasks (max_hold) |
|---|---|
| billing (14) | process_license_renewals, cleanup_expired_credit_buckets, process_annual_plan_credit_grants, reconcile_subscription_renewals, process_license_monthly_credit_refreshes, nightly_stripe_live_qa, recalculate_conversion_probabilities, reconcile_subscription_prices (DAILY); expire_active_trials (EVERY_6_HOURS); sweep_stale_stripe_events, sweep_missing_receipt_urls, replay_safe_failed_stripe_events (HOURLY); reconcile_stripe_prices (PRICE_SWEEP); escalate_stale_licence_stripe_intents (EVERY_5_MIN) |
| dashboard (5) | send_weekly_course_summaries, send_weekly_student_summaries, send_weekly_school_admin_summaries (WEEKLY); send_at_risk_student_alerts, send_teacher_inactivity_alerts (DAILY) |
| ai_processor (2) | nightly_grading_benchmark_replay (DAILY), weekly_grading_benchmark_live (WEEKLY) |

Exempt (`EXEMPT_BEAT_TASKS`, with reasons): `record_concurrent_users` (one
sample row per 60 s tick) and `check_beat_health` (the watchdog must never
be skippable). A guard reflects over `CELERY_BEAT_SCHEDULE` so every new
entry is locked or exempt, and checks `max_hold` against each crontab's
interval. `check_beat_health` now reports a lock store that isn't answering
(`lock_store_problem()`).

`reconcile_stripe_prices`' own cache lock moved onto the helper (its
unconditional delete could remove a newer run's lock); its raw-cache-write
allow-list entry is gone.

### Test isolation

The test runner (`AutoGrader/redis_test_runner.py`) wraps
`SimpleTestCase._pre_setup` to call
`AutoGrader/testing/beat_locks.py::clear_beat_locks()`, and
`IsolatedParallelTestSuite.process_setup` covers spawned workers. The
clearing deletes **exact keys** (`known_lock_names()`: every lock name the
process has created) under this process's prefix, no keyspace scan. A test
proves a lock left by one test can't make the next test's task skip.

## Rulings and reviews

- **SM: fail closed only for tasks that catch up.** Catch-up audit
  (accepted by the SM): every money/state task selects everything due up to
  now, so a skipped run is caught up by the next. The missing-receipt
  sweep's 3-day window stays as it is. The dashboard emails and the paid
  probes stay fail-closed by the SM's explicit exception (a missed email or
  probe is preferred over a duplicate).
- **Catch-up tests (d6915aa):** for each of the 10 money/state tasks, a
  backlog item already due before the skipped run; with the lock held
  elsewhere the task skips (WARNING asserted, item untouched, no Stripe
  call); released, the next run processes it.
- **SM's annual cycle-end question:** crossing an annual cycle end by
  skipping needs about 18 consecutive skipped daily runs. The analysis found
  the separate, real monthly-rollover loss, fixed on its own branch
  (task/monthly-rollover-cleanup-race, VERIFIED, in bundle 4).
- **ed / SM / 0b: no keyspace scan in `AutoGrader/testing/`.** That
  directory counts as production to the repo guards. The allow-list entry
  tried in 31c56ec was undone in e5a94d0 in favour of exact keys, as the SM
  preferred; the guard file is back to its original content.

## The merged base exposed three test interactions (44dde21), flag for v2

Gate (a) on 9887b25 (203 tests) had 3 failures, all interactions between
H-65 and bundle 4's F6 fixes, none a defect. Red log kept:
`a_modules_9887b25_red.log`.

- `billing.tests.test_beat_lock_catch_up` (H-65's own test): the cleanup
  summary now contains "N monthly buckets kept for an owed refresh", so the
  test checks "1 buckets processed" and "600 raw credits expired" separately.
- `billing.tests.test_overlapping_run_rechecks` (**a bundle 4 test 1a
  verified**, changed on this branch): its overlap helper nests run A inside
  run B in one thread. With H-65's lock the nested run skips, so the tests
  stopped reaching the per-row re-checks they prove. Those re-checks are the
  defence for a **lapsed** lock, so the helper now models exactly that: run
  B's key is deleted before run A (`beat_locks._redis().delete(lock_key(...))`),
  run A takes the lock itself, and run B must log the ERROR "finished without
  its lock". Mutants M1 and M2 (adf8fe0, at 0b's request) remove those
  re-checks and show the adapted tests still prove them (below).

**For v2:** this rewrites a verified bundle-4 test's helper; the diff is
44dde21, `billing/tests/test_overlapping_run_rechecks.py` (20 lines).

## Gates on the frozen tip adf8fe0

The tree was committed and `git status --porcelain` asserted empty before
the chain; the chain stopped on the first red step. `EXEMPT_EMAIL_DOMAINS=`
empty; every run under `systemd-run --scope -p MemoryMax=6G
-p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`.

| Step | Log | Result |
|---|---|---|
| (a) changed modules: AutoGrader.tests_beat_locks, billing.tests.test_beat_lock_catch_up, AutoGrader.tests_beat_health, AutoGrader.test_health, billing.tests.test_price_reconciliation, the two cache guards, billing.tests.test_monthly_rollover_cleanup_race, billing.tests.test_overlapping_run_rechecks | `a_modules_adf8fe0.log` | 203 tests, OK |
| (b) mutation battery, own DB `test_h65_mut` (dropped afterwards) | `b_mutation_battery_adf8fe0.log`, `results.tsv`, `logs/` | **16/16 killed**, every restore verified |
| pg_stat_activity before (c) (`pg_stat_clear_snapshot()` first) | `c_pg_stat_activity_before.txt` | 12 idle GAMAIDTicket, 1 active postgres |
| (c) ONE regression: billing, dashboard, ai_processor + all 9 beta-line guards, `-v 2` through a timestamper | `c_app_billing_dashboard_ai_guards_adf8fe0.log.gz` | 3115 tests, **OK (skipped=8)**, 595.7 s; wall 626 s |

The 9 guards: AutoGrader.tests_no_wildcard_invalidation,
tests_cache_invalidation_coverage, tests_migration_rollback_defaults,
tests_redis_test_isolation, tests_beat_health,
classrooms.tests_teacher_access_sweep,
classrooms.tests_course_roster_scope_sweep,
assignments.tests_schema_extension, users.tests_schema_extension.

### Mutants

| Id | Mutation | Killed by |
|---|---|---|
| M1 | mid-cycle grant: drop the `next_credit_grant_at > refresh_due_by(now)` re-check (lapsed lock) | 4 failures, incl. test_an_overlapping_run_does_not_grant_the_month_again, test_run_b_blocked_behind_run_a_does_not_grant_again |
| M2 | expire_trial: `if not (locked.is_trial and locked.is_active)` → `if False` (lapsed lock) | 7 failures, incl. test_an_overlapping_run_does_not_expire_the_trial_again, test_expiry_blocked_behind_a_conversion_leaves_it_paid |
| L1 | the release is skipped after the run (0b) | 20 failures, 1 error |
| L2 | release not compare-and-delete | 1 |
| L3 | extend not compare-and-extend | 1 |
| L4 | a cache error runs the task (fails open) | 1 |
| L5 | a held lock doesn't skip | 34 |
| L6 | acquire without NX | 34 |
| L7 | heartbeat ignores max_hold | 1 |
| L8 | no heartbeat | 3 failures, 30 errors |
| L9 | TTL above max_hold | 1 |
| L10 | record_concurrent_users' exemption entry removed | 1 |
| L11 | EVERY_5_MIN max_hold 6 min, above its interval | 1 |
| L12 | the watchdog drops the lock-store line | 1 |
| L13 | the runner doesn't clear locks before a test | 1 |
| L14 | cleanup_expired_credit_buckets loses its decorator | 3 |

### Machine suspend during (b)

The laptop suspended from 22:48:01 to 23:30:31 (+01:00), during mutant M1
(journalctl). M1 completed after the resume and was killed; no step failed
or timed out. The chain's wall clock is therefore about 58 min, of which
about 16 min active. (c) ran after the resume: 22:34:29–22:44:47 UTC.

### Earlier runs

- `dev_modules_wip.log`: the dev run on the WIP before the isolation step,
  126 OK.
- `a_modules_9887b25_red.log`: gate (a) on the base-updated tip, 3 failures
  (above); the chain stopped there.

## Deviations

- `test_overlapping_run_rechecks` (bundle 4, verified) changed on this
  branch; see the v2 flag above.
- Two Beat tasks are exempt rather than locked (reasons in the code).
- The dashboard emails and paid probes fail closed on a Redis error without
  a catch-up (SM's explicit exception).
