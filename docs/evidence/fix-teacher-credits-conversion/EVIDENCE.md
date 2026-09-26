# Evidence — teacher-detail credit figures conversion fix

Worktree: `GAP-fix-teacher-credits-conversion`, branch
`task/fix-teacher-credits-conversion`, off beta `174be2d`. This evidence
at commit `5457d32`.

## 1. Background

User-reported display bug (via screenshot): the school-admin "teacher
detail" dashboard endpoint (`dashboard/views.py`'s
`SchoolAdminDashboardView.teacher_detail`, `GET
schools/admin/teacher-detail/<teacher_id>`) showed credit figures as raw
internal token counts (e.g. "40.00M") instead of the actual user-facing
credit amount. Every other credit figure in the codebase divides the raw
stored value by `CONVERSION_FACTOR = 1000` (`billing/models.py`) before
showing it to a user (the `// CONVERSION_FACTOR` pattern throughout
`billing/serializers.py` and `billing/models.py`) — this endpoint never
applied that division anywhere.

Root cause and bucket-aggregation logic were pre-traced by the SM: all 5
`CreditBucketType` values are correctly accounted for (`TRIAL`
deliberately excluded — a license-invited teacher never has one;
`MANUAL_GRANT` correctly folded into `overage`) — this fix is purely the
missing division, not a logic change to what gets summed.

## 2. Fix (dashboard/views.py)

Imported `CONVERSION_FACTOR` from `billing.models` (already imported in
this file for `CreditBucketType`/`CreditUsageLog`) and applied `//
CONVERSION_FACTOR` to all four affected field groups:

1. `category_totals[category]["amount"]` (grading/creation/feedback/other)
   — `percent` is a ratio computed from the raw (undivided) `amount`
   variable in the same loop and is left untouched.
2. `result["credits_used"]` — the all-time total.
3. `result["credits_remaining"]` — `monthly`, `carry_over`, `overage`
   each divided individually; `total` divides the **summed raw** values,
   not the sum of the three already-floored parts (these can differ —
   see the mutation battery's TOTAL mutant below).
4. `daily_usage[].credits` — the 60-day usage chart.

`credits_used_percentage` (current-plan usage ratio) was already correct
and untouched — both its numerator and denominator are raw, so scaling
is a no-op for a ratio.

## 3. Test suite (dashboard/tests.py, TeacherDetailAPITest)

Three existing tests asserted raw, un-converted amounts as their
expected values — i.e. they encoded the bug itself as correct behavior.
Updated their logged raw amounts to scale by `CONVERSION_FACTOR` so the
same readable expected numbers (1000, 500, 2000, etc.) now describe the
corrected, user-facing figures instead of raw storage units:
`test_feature_mix_percentages_and_unmapped_feature_falls_to_other`,
`test_refunded_usage_excluded`, `test_days_active_and_daily_usage_window`.
`test_credits_used_percentage_excludes_overage` needed no change — it
only asserts the ratio field.

A second, separate file was also affected and only surfaced by the full
regression run (not the targeted suite, which doesn't import it):
`dashboard/tests_teacher_credits_remaining.py`'s
`test_each_source_is_isolated_and_manual_grant_folds_into_overage` and
`test_trial_bucket_never_leaks_into_any_category` likewise asserted raw
bucket totals against `credits_remaining`. Fixed the same way, in commit
`5457d32`. `test_expired_buckets_are_excluded` and
`test_teacher_with_no_wallet_gets_all_zero` needed no change - their
expected values are all-zero, which floor division leaves unchanged.

Four new tests, one per affected field group, each using a raw value
that is **not** a multiple of `CONVERSION_FACTOR` to confirm floor
division (matching every sibling billing field's `//` convention, not
`round`):

- `test_credits_used_is_the_raw_total_divided_by_conversion_factor` —
  12345 raw -> 12.
- `test_category_amount_is_the_raw_total_divided_by_conversion_factor` —
  7999 raw -> 7 (`amount`), `percent` still ~100.0 (untouched ratio).
- `test_credits_remaining_is_the_raw_total_divided_by_conversion_factor`
  — three buckets with non-multiple remaining balances (4300, 2999,
  1000) -> 4, 2, 1; `total` = (4300+2999+1000)//1000 = **8**, deliberately
  checked against the wrong alternative 4+2+1=7 in the docstring to
  pin down which one the implementation does.
- `test_daily_usage_credits_is_the_raw_total_divided_by_conversion_factor`
  — 6789 raw -> 6.

```
python manage.py test dashboard.tests.TeacherDetailAPITest --settings=settings_worktree -v 2
```

**Ran 12 tests. OK.**

## 4. Mutation testing

7 mutants (`mutate.py.txt`) against the 7 division sites in
`dashboard/views.py`. Applied one at a time from a collision-safe backup
copy, restore verified by md5 against the pre-mutation original after
every mutant (never `git checkout`), confirmed both programmatically
(`restored_md5_ok`) and by `git status --short` showing only the
untracked evidence directory throughout.

**Result: 7 / 7 KILLED**, first pass, no fixes needed.

| id | protection weakened | expected test |
|----|---|---|
| CAT | category `amount` not converted | `test_category_amount_is_the_raw_total_divided_by_conversion_factor` |
| USED | `credits_used` not converted | `test_credits_used_is_the_raw_total_divided_by_conversion_factor` |
| MONTH | `credits_remaining.monthly` not converted | `test_credits_remaining_is_the_raw_total_divided_by_conversion_factor` |
| CARRY | `credits_remaining.carry_over` not converted | `test_credits_remaining_is_the_raw_total_divided_by_conversion_factor` |
| OVER | `credits_remaining.overage` not converted | `test_credits_remaining_is_the_raw_total_divided_by_conversion_factor` |
| TOTAL | `credits_remaining.total` floors the raw sum only after the division is removed (reverts to the un-converted raw total) | `test_credits_remaining_is_the_raw_total_divided_by_conversion_factor` |
| DAILY | `daily_usage[].credits` not converted | `test_daily_usage_credits_is_the_raw_total_divided_by_conversion_factor` |

Raw log: `mutation_log.jsonl`. Full results incl. md5s:
`mutation_results.json`.

## 5. Full regression

Isolated env `audit-lead-teachercredits` (private Postgres 16 + Redis),
`settings_worktree.py` -> `test_fix_teacher_credits_conversion`. Log
redirected to a file, never piped through `tail` before backgrounding.

```
python manage.py test --settings=settings_worktree --noinput
```

First run (before the `tests_teacher_credits_remaining.py` fix) caught
the two failures described in §3 above: **FAILED (failures=2,
skipped=26)**, 4602 tests. Re-run after that fix, on commit `5457d32`:

**Ran 4602 tests. OK (skipped=26).**

Full tail: `full_regression_summary.txt`.

## 6. Tree state

`git status --short` throughout steps 3-5: clean except this untracked
evidence directory. No mutation, test run, or regression left any
tracked file modified.
