# User list sorting, beta port: survey (checkpoint 1, no code)

Brief 3 (SM -16, 2026-11-11). The owner approved it for beta as a bug fix.
The source is staging's user-list work, e2a0a146a..f90bb9b23 (design and
evidence in docs/evidence/user-list-sorting/, rulings D1-D7 in its DESIGN
section 8).

Base: origin/beta 2c8165b69. Beta is a direct ancestor of staging (merge
base = beta head; staging is 316 commits ahead, beta 0), so the survey is a
diff of every file the port reads or changes between beta and the staging
base aeb8b157e.

## 1. Credits on beta

B1a is not on beta: there is no `CreditWallet.reserved_credits` (confirmed
by model introspection) and no `billing/holds.py`. Beta's billing number
for "credits left" is

- `CreditWallet.total_remaining_credits()`: Sum(total_credits - used_credits)
  over buckets with expires_at null or > now, `or 0`. No clamp at 0.
- shown as `CreditWallet.display_balance` = `math.floor(total / 1000)`. This
  is what access_control, license_service, qa_console and
  billing/serializers.py read.

Port: `credits_left_raw` = Case(no wallet -> null, else Coalesce(live sum,
0)). The staging `Greatest(0, ... - reserved_credits)` goes away, so beta
has no reserved_credits reference. `credits_left` = the display value. The
parity test is pinned to beta's own functions: `credits_left ==
wallet.display_balance` and `credits_left_raw ordering ==
total_remaining_credits()`.

**Q-B1 (needs a ruling): negative balances.** Nothing stops used_credits
exceeding total_credits on beta (there is no check constraint), so the sum
can be negative. Staging never hit this because of the max(0) clamp. Postgres
integer division truncates toward zero (-1500/1000 = -1), but display_balance
floors (-2). Proposal: compute credits_left as a floor (Floor over a decimal
division, cast to int) so it equals display_balance for every value, and add
a negative-balance fixture to the parity test. The alternative, clamping at
0, would differ from beta's own number.

## 2. Same shape on beta?

| Item | Beta vs staging base aeb8b157e |
|---|---|
| users/views.py, filters.py, mixins.py, serializers.py, models.py | byte-identical |
| UserCacheMixin (users/mixins.py), AutoGrader/cache_generation.py | identical; the retrieve-cache backlog item applies to beta too |
| H-34 lock/unlock (in users/views.py) | identical |
| H-38 sweep classrooms/tests_teacher_access_sweep.py | identical; the two ALLOWED entries port as-is |
| classrooms/models.py | differs only by H-5 HOT_PATH_FIELDS in StudentCourse.save; teacher_course_access_q unchanged |
| assignments/tests_schema_extension.py, drf-spectacular extensions | identical; the OpenApiTests target_class restore ports as-is |
| billing/access_control.py (_resolve_access_context), subscription_resolver.py | identical |
| billing/serializers.py (CreditWalletSerializer) | identical, so ListCreditWalletSerializer ports as-is |
| billing/models.py | beta lacks B1a/B1b only (AIJob, HoldState, wallet reserved_credits and warning fields). PlanTier, PlanCategory, BillingInterval, CreditBucketType and CONVERSION_FACTOR are the same |
| students/models.py | beta lacks ErasedStudentRecord; ai_graded_at is the same |
| users.CustomUser.email_verified_at | same |
| Relations the list reads (subscriptions, managed_license_subscriptions, school_credit_allocations, credit_wallet, buckets, usage log fields) | all present (introspection) |

The staging diff for users/ and classrooms/ (8 files, +2169/-4) passes
`git apply --check` on beta cleanly.

## 3. Payload pin

To be re-taken on beta's base before any product change (step P0), with the
pin fixture adapted: its individual wallet loses `reserved=1_500`. The parent
serializer never reads reserved_credits, so the values may well equal
staging's, but the snapshot will be beta's own.

## 4. Query count (M0, NOTES)

Beta base, 30 teachers with wallets: **5 rows = 69 queries, 25 rows = 309**
(12 per row + 9). Target: the same fixed **13** as staging at 5 and 25 rows,
cold cache. The list serializer and prefetches are unchanged, and dropping
the reserved column changes no query.

## 5. Other adaptations

- Tests: drop `billing.holds.spendable_credits` and the `wallet(reserved=)`
  helper argument. Rewrite the reserved-dependent cases: the parity
  FIXTURES (reserved column dropped, negative-balance fixture added), the
  "held" credits case, and the reserved=100 fixture in the ordering/filter
  tests.
- Plant Q-credits: on staging it removed the reserved subtraction. On beta
  it becomes "drop the expiry filter from the live-bucket sum" (expired
  buckets counted). The kill is declared on the expired-bucket and parity
  tests. The other 7 plants port unchanged (same files and lines).
- Guards: 7 of the 67 standing guard labels do not exist on beta (B1a/B1b
  and C1-era files: classrooms.tests_e1_grade_guard,
  students.tests_student_data_map, ai_processor.tests_metering_chokepoint,
  billing.tests.test_wallet_save_guard, test_zero_credit_registry,
  test_holds_entry, test_holds_single_call). The near uses the 60 that
  exist. On the beta base, users + billing + guards60 = 4302 tests
  (build_suite).
- The beta-into-staging merge: expect a conflict only in users/user_list.py
  credits_left_raw/credits_left (staging: Greatest(0, live - reserved),
  integer division; beta: no reserved, floor) and in the tests that
  reference reserved. Staging's version must win there. The rest is
  identical text, so it should merge without conflicts.

## 6. Plan and time

Re-apply, not cherry-pick: the staging history interleaves evidence and
fixes. Commits: (1) design (this), (2) tests adapted plus pin P0 on the base,
red declared, (3) product, (4) green, plants (8), near, evidence.
Estimate: 3-4 h after the rulings (tests adaptation about 1 h, product and
green about 45 min, plants about 30 min, near about 10 min, evidence and
checkpoint about 45 min).
