# The monthly rollover lost to the 05:00 cleanup

**Branch:** `task/monthly-rollover-cleanup-race`, off beta `abeda10`, stacked on the verified mid-cycle re-check (`2bfa2e8`, merged as `3f5fa73`) at 0b's request, so the two fold in order. **Author:** Hardening (d5). **Verifier:** 1a. **Package:** F6 item 4; the founder folds all four F6 fixes into bundle 4.

Found while answering the SM's H-65 question about a skipped grant run crossing a cycle end.

## The defect (reproduced first, `b6515a2`)

The two monthly refresh tasks set the next due time from the moment each row was **processed**, `now + 1 month`, and the new monthly bucket expired at that same moment:

- annual individual plans: `process_annual_plan_credit_grants` (Beat 02:00) → `SubscriptionService.process_mid_cycle_credit_grant`;
- licence teachers: `process_license_monthly_credit_refreshes` (03:00) → `LicenseSubscriptionService._refresh_teacher_credits`.

A month later the task filtered on its own **start** time, a little earlier than that processing moment, so the row wasn't due yet and waited for the next day's run. In between, `cleanup_expired_credit_buckets` (05:00) found the monthly bucket expired and unprocessed and wrote it off (EXPIRE). The next day's refresh then found no unprocessed monthly bucket to roll over. So, **every month, from the first task-driven refresh onward**:

- customers on plans with carry-over **lost that month's carry-over** (the plan's `carry_over_percent` of their unused monthly credits, capped);
- **every** such customer had **about a day with no monthly credits** (from just after the scheduled run to the next day's run; carry-over and overage buckets still worked).

The same write-off hit a **monthly individual plan** whose renewal (the Stripe webhook, or the 04:00 reconcile) arrived after 05:00: its renewal's rollover selects the newest *unprocessed* monthly bucket, which the cleanup had already processed.

The test drives it with a clock that advances on every call, as a real one does (with a frozen clock the processing moment and the run's start coincide, and the bug can't show), through the real tasks: month 1 at its scheduled run, then month 2's scheduled run, the 05:00 cleanup, and the next day's run.

## The fix: three defences (`billing/refresh_timing.py`)

1. **One "now" per run, with a small due tolerance.** Each task passes its start time to the service, so the due check and the next due time come from the same moment. A refresh due within 5 minutes of a run's start is due on that run (Beat dispatch and worker pickup jitter by seconds from day to day), in the task filter and in the re-check under the lock. A due time capped at an annual cycle's end is excluded in both places, so the tolerance can never grant the renewal's month mid-cycle.
2. **A 2-day grace on every monthly bucket** past its refresh's due time, never past the contract's end: activation, both trial conversions, the immediate plan change, the mid-cycle grant, and the five licence sites (admin allocation, enrolment, renewal, offline renewal, refresh). The customer keeps monthly credits until the refresh retires the bucket (rolling its unused balance over). For a MONTHLY individual plan the due time *is* the cycle end, so nothing changes there.
3. **The cleanup never writes off a wallet's newest unprocessed MONTHLY bucket while its owner is entitled**: an active subscription, or an active allocation under an active licence. For them, an expired monthly bucket means a refresh or renewal is still owed, and that is what must roll it over. Older unprocessed monthly buckets, other bucket types, and anyone no longer entitled are cleaned up as before. A bucket kept a week past its expiry is logged at ERROR (the refresh itself has stopped).

The immediate plan change's site was found by listing every MONTHLY bucket creation in the tree merged with bundle 4 (`e190f06`); every site there now has the grace.

## Behaviour changes (4-point records)

**B1. When a monthly refresh is due.**
1. Previous: a row was due when `next_credit_grant_at <= the run's start`, and its next due time was `processing moment + 1 month`.
2. New: due when `next_credit_grant_at <= run start + 5 minutes` (and, for an annual grant, before the cycle's end); the next due time is `run start + 1 month`. Both services take the run's start as `now` (optional; direct callers are unchanged).
3. Why it's correct: the refresh belongs to that day's run; the old comparison was defeated by the milliseconds between a run's start and a row's processing. At most 5 minutes early, the schedule doesn't drift.
4. Tests: `test_the_new_month_is_granted_on_its_scheduled_run`, `test_a_run_starting_a_little_earlier_than_last_months_still_refreshes`, `test_a_row_processed_late_in_a_slow_run_is_due_on_next_months_run` (both paths), `test_a_due_time_capped_at_the_cycle_end_is_not_granted_early`, `test_the_service_refuses_a_due_time_capped_at_the_cycle_end`.

**B2. A monthly bucket's expiry.**
1. Previous: exactly the next refresh's due time.
2. New: that plus 2 days, capped at the contract's end.
3. Why it's correct: the refresh retires the bucket (expires_at = now, processed) when it runs, so the grace only covers the gap until then; it never outlives the paid contract.
4. Tests: `test_the_monthly_bucket_outlives_its_due_time_by_the_grace` (both paths), `test_the_customer_has_a_monthly_bucket_after_the_scheduled_run`, `FirstMonthGraceTests` (annual activation and plan change get it; a monthly activation is unchanged).

**B3. What the 05:00 cleanup writes off.**
1. Previous: every expired, unprocessed bucket.
2. New: the same, except each entitled owner's newest unprocessed MONTHLY bucket, which is kept (and logged at ERROR after a week).
3. Why it's correct: that bucket is the one the owed refresh or renewal rolls over; writing it off first loses the customer's carry-over.
4. Tests: `CleanupKeepsOwedMonthlyBucketsTests` (seven tests, including the monthly-plan renewal arriving after the cleanup).

## F6: read-only detection for the founder

`detect_monthly_rollovers_lost_to_cleanup.sql` (ids, counts, credit totals and timestamps only; no emails): cleanup write-offs of a MONTHLY bucket followed within 3 days by a new MONTHLY grant to the same owner, meaning the write-off happened while a refresh was owed. It reports months lost and the unused credits written off per wallet, an **upper bound** on the carry-over lost (the plan's `carry_over_percent` of it, capped). Review before compensating: a customer who lapsed and re-subscribed within 3 days is reported too. It's tested against the ledger as the pre-fix code writes it, and on the pre-fix tree against the real damage (below).

## Runs (every run under `systemd-run` MemoryMax=6G, MemorySwapMax=0, `nice -n 10`, `timeout`, with `EXEMPT_EMAIL_DOMAINS` empty)

| Run | Tree | Result | Log |
|---|---|---|---|
| **Reproduce-first:** the new module alone on the unchanged base | `abeda10` (+ the test, `b6515a2`) | **8 tests, 6 failures, 3 per path:** month 2 not granted on its scheduled run; no live monthly bucket after it; month 1's unused credits not rolled over (`[]` instead of `[2000]`). The two exactly-once checks pass (no double grant). | `repro_abeda10.log` |
| The fix's module, before commit | fix WIP | **22 OK** | `fix_module_wip.log` |
| **The same module on the pre-fix tree** (a disposable worktree at `3f5fa73`, own DB, dropped after) | `3f5fa73` | **The months-lost query catches the real lost rollover on both paths** (the lost-rollover test's first assertion). The cleanup guard's tests fail, including **the monthly-plan renewal arriving after the cleanup: its rollover is lost** too. 4 errors are the fix's own module missing there, as expected. | `prefix_3f5fa73_with_queries.log` |
| **Mutation battery, round 1** | `47bdc73` | **8 of 18 killed.** Every guard and grace mutant was killed; the 10 timing mutants survived, because each timing defence was redundant with another in the tests (with one "now" per run the tolerance wasn't needed, and vice versa). Isolating tests were added (`b161f49`). | `mutation_battery_47bdc73_round1.log`, `logs_round1/`, `results_round1.tsv` |
| (A billing regression chained after round 1 was **stopped** by me: it had started on a tree I was editing. No result is claimed from it.) | | | |
| The module | `b161f49` | **28 tests, 1 error**, a bug in my new plan-change test (it expected one unprocessed MONTHLY bucket; see *Noted*). Fixed, test only (`64b7c8b`); the chain stopped there as agreed. | `module_b161f49_test_bug.log` |
| The module | `64b7c8b` | **28 OK** | `module_64b7c8b.log` |
| **Mutation battery, round 2** (19 mutants, one disposable worktree, sha256-checked restores, DB dropped after) | `64b7c8b` | **19 of 19 killed** | `mutation_battery_64b7c8b_round2.log`, `logs/`, `results.tsv` |
| **ONE owning-app regression (rule 15): `billing`** + the guards on beta (`AutoGrader.tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`), stamped, `-v 2` | `64b7c8b` | **1755 tests, 1 failure:** `test_trial_to_annual_conversion.test_grant_date_is_persisted_and_bucket_expires_monthly` pinned the first MONTHLY bucket to expire exactly a month out. That is behaviour change B2; the test's point (a month's bucket, never the year) still holds, and it now expects the month plus the grace (`edc0306`, test only). Everything else OK. | `app_billing_guards_64b7c8b.log.gz` (gzipped: over the 500 KB hook limit raw) |
| Confirmation: the corrected module + the fix's module | `edc0306` | **34 OK** | `confirm_edc0306.log` |

No model or migration change, so no other app reads changed fields. `audit.*` guards are Phase 2 only (not on beta).

## Noted, not changed (the SM's ruling: its own row, H-76, bundle 5)

`apply_immediate_plan_change` retires the old monthly bucket with `expires_at = now` but without `is_processed = True` (its three sibling rollovers set it). So the 05:00 cleanup later writes an EXPIRE row for that bucket's unused credits, which the plan change already rolled into CARRY_OVER: the ledger double-counts the expiry; balances are unaffected. Found while fixing the plan-change grace test.

## Merge with bundle 4

`git merge-tree --write-tree e190f06 edc0306` (bundle 4's tip, with the other three F6 items) **exits 0: clean.** The branch is stacked on the mid-cycle fix (`2bfa2e8`), which e190f06 already contains. In the merged tree, every MONTHLY bucket creation site has the grace (checked by listing them all).
