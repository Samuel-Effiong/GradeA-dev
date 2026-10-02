# H-76: the plan change retires its old monthly bucket as processed

Branch `task/h76-plan-change-bucket-processed`. Author: d5 (Hardening).
Code tip 5b25650, base-updated by 0b onto bundle 4's final tip 67a0681 as
**430f4e9**; gated tip 41f3acc (430f4e9 + evidence only).

## The defect

`SubscriptionService.apply_immediate_plan_change` rolls the old MONTHLY
bucket's unused credits into a CARRY_OVER bucket and retires the old bucket
with `expires_at = now`, but did not set `is_processed = True`, unlike its
three siblings (`activate_subscription`'s upgrade rollover,
`process_rollover_and_renewal`, the licence rollover). The 05:00 cleanup
then wrote an EXPIRE row for credits that had already been rolled over, so
the ledger counted them twice. Balances were unaffected. Found while listing
every monthly-bucket creation for the rollover fix (F6), reported to the SM,
and logged as its own row.

## The fix (5b25650)

`active_monthly.is_processed = True`, saved with
`update_fields=["expires_at", "is_processed", "updated_at"]`.

## Behaviour change (4-point record)

**B1. The bucket a plan change retires.**
1. Previous: `expires_at = now`, `is_processed = False`. The next 05:00
   cleanup expired its unused credits again (an EXPIRE ledger row).
2. New: `expires_at = now`, `is_processed = True`. The cleanup skips it.
3. Why it's correct: the plan change has already rolled those credits over,
   so the bucket is finished, exactly as the three sibling rollovers leave
   theirs. With bundle 4's rollover fix (the cleanup keeps an entitled
   owner's newest unprocessed MONTHLY bucket), the new plan's bucket is now
   the owner's only unprocessed MONTHLY bucket.
4. Tests: `billing.tests.test_plan_change_retires_old_bucket`:
   `test_the_old_bucket_is_retired_as_processed`,
   `test_the_cleanup_does_not_expire_what_was_rolled_over`.

## Runs

| Step | Log | Result |
|---|---|---|
| Reproduce-first on e190f06 (2cfda9d, test only) | `repro_e190f06.log` | 2 tests, **2 failures** (not processed; 1 EXPIRE row) |
| Module at 5b25650 | `module_5b25650.log` | 2 OK |
| Mutation battery at 5b25650, own DB `test_h76_mut` | `mutation_battery_5b25650.log`, `results.tsv`, `logs/` | **2/2 killed**, verified restore (P1 the old bucket is marked processed; P2 is_processed is saved) |
| ONE regression at 41f3acc (= 430f4e9 code): billing + all 9 beta-line guards, `-v 2`, UTC timestamps | `c_app_billing_guards_41f3acc.log.gz` | 2031 tests, **OK**, 259.7 s; wall 279 s; no suspend |

The 9 guards: AutoGrader.tests_no_wildcard_invalidation,
tests_cache_invalidation_coverage, tests_migration_rollback_defaults,
tests_redis_test_isolation, tests_beat_health,
classrooms.tests_teacher_access_sweep,
classrooms.tests_course_roster_scope_sweep,
assignments.tests_schema_extension, users.tests_schema_extension.

The regression is the right check for the base update: bundle 4's rollover
fix and H-76 both touch the plan-change path in `billing/services.py`
(auto-merged), and `billing.tests.test_monthly_rollover_cleanup_race` ran in
it.

## Deviations and corrections

- **The regression ran without `--settings=settings_worktree`**, so it used
  the shared default test database name `test_AutoGrader` instead of this
  worktree's `test_h76_plan_change_bucket_processed`. The setting only names
  the database, so the result stands. The log shows the database was
  created fresh (no "destroying old test database"), and the run passed, so
  no other run was sharing it. Reported to 0b.
- 41f3acc's commit message names the module
  `billing.tests.test_plan_change_bucket_processed`; the module is
  `billing.tests.test_plan_change_retires_old_bucket`.

## N1 (after 1a's VERIFIED-WITH-NOTES at c46fdbf)
1a's N1 (probe E1): the licence enrolment (`license_service.
_enroll_teacher_internal`) also retires the teacher's MONTHLY bucket but
left it `is_processed=False`, so the 05:00 cleanup could expire what was
rolled over. It now sets `is_processed=True` and saves it
(`update_fields=["expires_at", "is_processed", "updated_at"]`).
`test_monthly_rollover_cleanup_race` now selects the live bucket with
`is_processed=False`.

| Commit | What |
|---|---|
| `951fe38` | 1a's record, verbatim |
| `e5a75d8` | `LicenceEnrolmentRetiresOldBucketTests` (1a's E1); the race test's selector |
| `477eeed` | the fix; runner mutants P3, P4 |
| `91eadbd` | the repro, module and battery logs |

| Gate | Result | Log |
|---|---|---|
| Repro at e5a75d8 (disposable worktree) | 43 tests, exactly the 2 `LicenceEnrolmentRetiresOldBucketTests` fail | `repro_n1_e5a75d8.log` |
| (a) the 2 modules at 477eeed | 43 OK | `a_modules_477eeed.log` |
| (b) battery at 477eeed (`test_h76_mut`) | 4/4 killed (P1–P4), sha-verified restores | `b_mutation_battery_477eeed.log` |
| (c) billing + 9 guards at 91eadbd (docs-only over 477eeed) | 2033 OK, wall 296 s | `c_app_billing_guards_91eadbd.log.gz` |

All under 0b's grants, 6G, `timeout -k 60 1800`, the rule-16 prefix,
`--settings=settings_worktree` (the deviation recorded above is not
repeated).
