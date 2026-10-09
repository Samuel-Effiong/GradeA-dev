# Evidence — /subscription/me EXPIRED extended to the license tracks

Worktree: `GAP-subscription-me-license-expired`, branch
`task/subscription-me-license-expired`, off beta `12a8ba4` (which already
carries the INDIVIDUAL-only ACTIVE/EXPIRED/NONE rework this follows up on).
This evidence at commit `fb19ac4`.

## 1. Background

Follow-up to the just-landed `/subscription/me` always-200 rework: only
the INDIVIDUAL track got a real EXPIRED body. A license admin or teacher
whose license/allocation lapsed fell through the individual-only
fallback straight to `NONE` ("no subscription ever"), which is wrong —
they have real history. User-approved instruction: "ensure every track
is affected." This task extends EXPIRED to both license tracks.

## 2. Design decision flagged before building (approved by the SM)

The SM's literal spec for LICENSE_TEACHER EXPIRED was
`is_admin_allocation=False, is_active=False`. Flagged before building:
the ACTIVE-path condition in `resolve_user_billing_context` requires
BOTH `allocation.is_active=True` AND `license_subscription.is_active=True`,
so there are two distinct ways a teacher's context can stop being truly
active:

- (a) the allocation itself was deactivated (`is_active=False`) —
  covered by the literal spec.
- (b) the allocation row was never touched (`is_active=True`) but the
  PARENT LICENSE lapsed — NOT covered by the literal spec, and the
  SM's own instruction that `is_license_active` must report the real
  (possibly-inactive) parent state implies this shape has to reach
  EXPIRED too, or that field would never have anything to report.

Approved fix: broaden the query to the exact complement of the ACTIVE
condition — `is_admin_allocation=False AND NOT (is_active=True AND
license_subscription__is_active=True)` — covering both shapes. See
`LicenseTeacherExpiredTests` below for dedicated tests of each shape.

## 3. Implementation

### billing/serializers.py

- `MyLicenseAdminSubscriptionSerializer.status`: constant
  `CharField(default="ACTIVE")` → `SerializerMethodField` reading
  `self.context.get("status", "ACTIVE")`, same pattern as
  `MySubscriptionSerializer.status`.
- `MyLicenseAdminSubscriptionSerializer.get_days_until_renewal`: nulled
  for `status == "EXPIRED"` (same "don't clamp a negative delta to a
  misleading 0" guard as the individual case).
- `MyLicenseTeacherSubscriptionSerializer.status`: same
  CharField→SerializerMethodField change.
- `MyLicenseTeacherSubscriptionSerializer.get_days_until_next_credit_grant`:
  also nulled for `status == "EXPIRED"` — not explicitly asked for in
  the SM's brief, but the same clamped-negative-delta bug applies to a
  stale `next_credit_grant_at`, so applied the same guard for
  consistency.

### billing/views.py

- `get_my_subscription`'s ACTIVE branches for LICENSE_TEACHER/
  LICENSE_ADMIN now explicitly pass `context={"status": "ACTIVE"}` (the
  license serializers' `get_status` default covers this anyway, but
  explicit beats implicit here since the individual branch already
  does the same).
- The no-active-context fallback is now a 3-step priority chain, in the
  SAME order `resolve_user_billing_context` uses for ACTIVE:
  1. Most-recent inactive `UserSubscription` (`-billing_cycle_end`) —
     unchanged from the prior task.
  2. Most-recent inactive `LicenseSubscription` where
     `admin_user=request.user` (`-billing_cycle_end`) →
     `MyLicenseAdminSubscriptionSerializer`, `status="EXPIRED"`,
     `managed_license_count=0` explicitly (NOT the ACTIVE-path default
     of 1 — there is no active license to count, and the default would
     wrongly tell the frontend this admin still manages one).
  3. Most-recent `SchoolCreditAllocation` matching the broadened
     complement filter above (`-updated_at` — `SchoolCreditAllocation`
     has no `billing_cycle_end`; `updated_at` is the closest "when did
     this state last change" signal available, which is why its
     ordering field differs from the other two tracks) →
     `MyLicenseTeacherSubscriptionSerializer`, `status="EXPIRED"`.
     `is_admin_allocation=False` stays excluded, mirroring the ACTIVE
     lookup's own exclusion of the admin's analytics-only row.
  4. Truly `NONE` only if none of the three has any history at all.

A user with lapsed history on more than one track gets whichever the
ACTIVE path would have picked first — proven by
`PriorityOrderExpiredTests` below, not assumed.

### @extend_schema

Added two new 200 examples ("Expired license (school admin)", "Expired
license (teacher)") alongside the three already there, and updated the
description to describe the full 3-track priority chain instead of the
INDIVIDUAL-only language from the prior pass.

## 4. Test suite (billing/tests/test_subscription_me_status.py)

12 new tests added to the existing file:

- `LicenseAdminExpiredTests` (4): lapsed license reports EXPIRED;
  `days_until_renewal` nulled; `managed_license_count`/
  `has_other_managed_licenses` correctly report 0/False (not the
  ACTIVE-path default); most-recent (by `billing_cycle_end`) of two
  lapsed licenses wins.
- `LicenseTeacherExpiredTests` (7): shape (a) the teacher's own
  allocation deactivated while the license is fine
  (`is_license_active` stays `True`); shape (b) the license itself
  lapsed while the allocation row was never touched
  (`is_license_active` reads `False` — this is the exact case the
  broadened query exists for); `days_until_next_credit_grant` nulled;
  an admin's own analytics allocation never surfaces via the admin's
  own `/me` call (resolves via the license-admin branch first); a
  dedicated edge case with NO managed license at all proving the
  `is_admin_allocation=False` exclusion actually does something on the
  teacher path itself (the admin's-own-call test above never reaches
  that filter — the admin branch returns first); most-recently-touched
  (`-updated_at`) of two lapsed allocations wins.
- `PriorityOrderExpiredTests` (2): expired individual history beats
  expired license-admin history on the same user; expired license-admin
  history beats expired license-teacher history on the same user —
  both proving the fallback chain picks the same track the ACTIVE path
  would have picked, not an arbitrary one.

```
python manage.py test billing.tests.test_subscription_me_status --settings=settings_worktree -v 2
```

**Ran 22 tests. OK.**

Plus the existing suites this endpoint/its models touch, unchanged
from the prior task's sweep (no new contract-breaking surface found —
this pass only adds new response shapes on paths that previously
404'd/NONE'd, never removes or changes an existing field on the ACTIVE
shapes):

```
python manage.py test billing.tests.test_free_plan_activation_security \
  billing.tests.test_subscription_reactivation billing.tests.test_subscription_cancel \
  billing.tests.test_subscription_upgrade billing.tests.test_cancellation_visibility \
  billing.tests.test_license_cancellation --settings=settings_worktree -v 1
```

**Ran 224 tests. OK.**

## 5. Mutation testing

10 mutants (`mutate.py.txt`) against the 10 sites this change added or
touched, across `billing/views.py` and `billing/serializers.py`.
Applied one at a time from a collision-safe backup copy, restore
verified by md5 against the pre-mutation original after every mutant
(never `git checkout`), confirmed both programmatically
(`restored_md5_ok`) and by `git status --short` showing only the
untracked evidence directory throughout.

**Result: 10 / 10 KILLED**, first pass. Re-ran the full battery a
second time against the fully committed tree (`fb19ac4`) to confirm
the mutant patterns still match after both commits — also 10/10.

| id | protection weakened | expected test |
|----|---|---|
| ASTATUS | `MyLicenseAdminSubscriptionSerializer.get_status` ignores context, always ACTIVE | `test_lapsed_license_admin_reports_status_expired` |
| ADAYSREN | admin `get_days_until_renewal` EXPIRED guard removed | `test_expired_license_admin_nulls_days_until_renewal` |
| TSTATUS | `MyLicenseTeacherSubscriptionSerializer.get_status` ignores context, always ACTIVE | `test_teachers_own_allocation_deactivated_reports_status_expired` |
| TDAYSREN | teacher `get_days_until_next_credit_grant` EXPIRED guard removed | `test_expired_teacher_nulls_days_until_next_credit_grant` |
| AORDER | `last_managed_license` ordering reversed (oldest lapsed license wins) | `test_most_recent_lapsed_license_is_the_one_returned` |
| ACOUNT | admin EXPIRED reports the ACTIVE-path default `managed_license_count=1` instead of `0` | `test_expired_license_admin_reports_zero_managed_licenses` |
| NARROW | `last_allocation` reverts to the SM's literal `is_active=False` spec, dropping the license-lapsed-only shape | `test_license_itself_lapsed_reports_status_expired_and_is_license_active_false` |
| TORDER | `last_allocation` ordering reversed (oldest-touched allocation wins) | `test_most_recently_updated_lapsed_allocation_is_the_one_returned` |
| NOEXCL | `last_allocation` drops the `is_admin_allocation=False` filter | `test_admin_only_allocation_without_managed_license_is_excluded_from_teacher_lookup` |
| PRIORITY | priority chain reordered — teacher lookup checked before license-admin lookup | `test_expired_license_admin_history_wins_over_expired_license_teacher_history` |

Raw log: `mutation_log.jsonl`. Full results incl. md5s:
`mutation_results.json`.

## 6. Full regression

Isolated env `audit-lead-licexp` (private Postgres 16 + Redis),
`settings_worktree.py` -> `test_subscription_me_license_expired`. Log
redirected to a file, never piped through `tail` before backgrounding.

```
python manage.py test --settings=settings_worktree --noinput
```

**Ran 4669 tests. OK (skipped=26).**

Full tail: `full_regression_summary.txt`.

## 7. Tree state

`git status --short` throughout steps 4-6: clean except this untracked
evidence directory. No mutation, test run, or regression left any
tracked file modified.
