"""
billing/tests/test_license_multi_month_budget.py
================================================
Locks the fix for a bug where a school on a multi-month license contract
silently hands a 0-credit first month to every teacher enrolled after
month 1.

THE BUG
-------
LicenseSubscription.total_credits_consumed accumulates on every
consumption and was reset ONLY at contract renewal — which on the default
contract_months=12 is once a YEAR. But the budget it is measured against
is a MONTHLY figure:

    total_budget = max_seats * plan.monthly_credits      # per month
    remaining_budget = total_budget - total_credits_consumed  # per contract

So once a fully-seated school has consumed one month's pool — which for a
school actually using the product happens during month 1 — remaining_budget
is <= 0 for the rest of the contract, and _enroll_teacher_internal grants
`grant_amount = 0`.

SCOPE, PRECISELY
----------------
The budget check exists in exactly ONE place (_enroll_teacher_internal), so
already-enrolled teachers are never starved: _refresh_teacher_credits passes
allocation.monthly_allocation straight through with no budget check. What
breaks is NEW enrolments from month 2 onward. Their first month is dead, and
it self-heals the following month, so the only trace is an is_capped ledger
entry — which is why nobody noticed.

WHY RESET RATHER THAN WIDEN THE DENOMINATOR
-------------------------------------------
total_credits_consumed has exactly one read in the whole application (the
budget check). No serializer, view, report, BillingTransaction or
LicenseBillingRecord touches it, so resetting cannot corrupt anything
downstream. Widening the denominator to cover the contract would instead
turn a working monthly guardrail into a no-op: a single teacher could draw
the school's entire annual pool in one grant.

test_within_month_cap_still_binds passes BOTH before and after the fix, and
exists to prove the fix did not simply delete the guardrail.
"""

from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.tasks import process_license_monthly_credit_refreshes
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

MONTHLY_CREDITS = 20_000
MAX_SEATS = 2
MONTHLY_POOL = MAX_SEATS * MONTHLY_CREDITS  # 40_000


class LicenseMultiMonthBudgetTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Budget High")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.POWER_LICENSE,
            display_name="Power License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.POWER,
            interval=BillingInterval.MONTHLY,
            price_cents=19_900,
            monthly_credits=MONTHLY_CREDITS,
            carry_over_percent=0,
            carry_over_expiry_months=1,
            is_active=True,
        )
        self.admin = CustomUser.objects.create_user(
            email="budget-admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            contract_months=12,
            max_seats=MAX_SEATS,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=12),
            is_active=True,
            auto_renew=True,
        )

    # -- helpers ---------------------------------------------------------

    def _make_teacher(self, email):
        return CustomUser.objects.create_user(
            email=email,
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

    def _enroll(self, teacher):
        return LicenseSubscriptionService._enroll_teacher_internal(
            self.license, teacher
        )

    def _monthly_bucket_total(self, teacher):
        bucket = (
            CreditBucket.objects.filter(
                wallet__user=teacher, bucket_type=CreditBucketType.MONTHLY
            )
            .order_by("-created_at")
            .first()
        )
        return bucket.total_credits if bucket else None

    def _consume_whole_monthly_pool(self, teacher):
        """Burn the school's entire monthly pool through the REAL
        consumption path, so LicenseSubscription.total_credits_consumed is
        updated by _record_license_consumption rather than by the test."""
        wallet = CreditWallet.objects.get(user=teacher)
        # Top up beyond the enrolment grant so one teacher can actually
        # consume the whole school pool.
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=MONTHLY_POOL,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=60),
        )
        wallet.consume_credits(
            amount=MONTHLY_POOL, feature="Grading Assignment", task_id="budget-burn"
        )

    def _license_consumed(self):
        return LicenseSubscription.objects.get(
            pk=self.license.pk
        ).total_credits_consumed

    def _run_monthly_refresh_at(self, moment):
        with patch("django.utils.timezone.now", return_value=moment):
            process_license_monthly_credit_refreshes()

    # -- tests -----------------------------------------------------------

    def test_monthly_refresh_resets_the_consumption_window(self):
        teacher_a = self._make_teacher("a@school.edu")
        allocation = self._enroll(teacher_a)
        self._consume_whole_monthly_pool(teacher_a)

        self.assertEqual(self._license_consumed(), MONTHLY_POOL)

        self._run_monthly_refresh_at(allocation.next_credit_grant_at)

        self.assertEqual(
            self._license_consumed(),
            0,
            "the per-cycle consumption counter must reset when the monthly "
            "credits refresh; leaving it set measures a month's usage "
            "against every later month's budget",
        )

    def test_teacher_enrolled_in_month_two_receives_a_full_grant(self):
        """The customer-visible consequence."""
        teacher_a = self._make_teacher("a2@school.edu")
        allocation = self._enroll(teacher_a)
        self._consume_whole_monthly_pool(teacher_a)

        self._run_monthly_refresh_at(allocation.next_credit_grant_at)

        teacher_b = self._make_teacher("b2@school.edu")
        with patch(
            "django.utils.timezone.now", return_value=allocation.next_credit_grant_at
        ):
            self._enroll(teacher_b)

        self.assertEqual(
            self._monthly_bucket_total(teacher_b),
            MONTHLY_CREDITS,
            "a teacher enrolled in month 2 must receive a full monthly "
            "allocation; a 0-credit bucket means last month's usage was "
            "charged against this month's budget",
        )

    def test_within_month_cap_still_binds(self):
        """Guardrail regression. Passes BEFORE and AFTER the fix — if this
        ever fails, the fix deleted the cap instead of scoping it."""
        teacher_a = self._make_teacher("a3@school.edu")
        self._enroll(teacher_a)
        self._consume_whole_monthly_pool(teacher_a)

        # No refresh in between: still the same month.
        teacher_b = self._make_teacher("b3@school.edu")
        self._enroll(teacher_b)

        self.assertEqual(
            self._monthly_bucket_total(teacher_b),
            0,
            "within a single month the school's pool is exhausted, so a "
            "newly enrolled teacher must NOT receive credits",
        )

    def test_contract_renewal_still_resets(self):
        """Non-regression: the existing contract-renewal reset must keep
        working.

        The license has to be genuinely DUE first — process_license_renewal
        returns early while billing_cycle_end is still in the future
        (license_service.py:1602), which is correct behaviour and not
        something to patch around.
        """
        teacher_a = self._make_teacher("a4@school.edu")
        self._enroll(teacher_a)
        self._consume_whole_monthly_pool(teacher_a)
        self.assertEqual(self._license_consumed(), MONTHLY_POOL)

        LicenseSubscription.objects.filter(pk=self.license.pk).update(
            billing_cycle_end=timezone.now() - timedelta(days=1)
        )
        self.license.refresh_from_db()

        LicenseSubscriptionService.process_license_renewal(self.license)

        self.assertEqual(self._license_consumed(), 0)

    def test_refresh_is_idempotent_within_the_same_month(self):
        """Running the refresh twice must not reset a second time and
        thereby erase consumption recorded between the two runs."""
        teacher_a = self._make_teacher("a5@school.edu")
        allocation = self._enroll(teacher_a)
        self._consume_whole_monthly_pool(teacher_a)

        moment = allocation.next_credit_grant_at
        self._run_monthly_refresh_at(moment)

        # Consume again inside the new window.
        wallet = CreditWallet.objects.get(user=teacher_a)
        wallet.consume_credits(
            amount=1_000, feature="Grading Assignment", task_id="budget-burn-2"
        )
        self.assertEqual(self._license_consumed(), 1_000)

        # A second sweep in the same month must leave that 1,000 alone.
        self._run_monthly_refresh_at(moment)
        self.assertEqual(
            self._license_consumed(),
            1_000,
            "a second refresh within the same month must not reset the "
            "counter again and discard consumption already recorded",
        )


class AllocationRefreshUnaffectedTests(TestCase):
    """Already-enrolled teachers were never starved by this bug; that must
    remain true after the fix."""

    def setUp(self):
        self.school = School.objects.create(name="Steady High")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO_LICENSE,
            display_name="Pro License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            price_cents=9_900,
            monthly_credits=MONTHLY_CREDITS,
            carry_over_percent=0,
            carry_over_expiry_months=1,
            is_active=True,
        )
        self.admin = CustomUser.objects.create_user(
            email="steady-admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            contract_months=12,
            max_seats=MAX_SEATS,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=12),
            is_active=True,
            auto_renew=True,
        )

    def test_existing_teacher_keeps_receiving_full_monthly_credits(self):
        teacher = CustomUser.objects.create_user(
            email="steady@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        allocation = LicenseSubscriptionService._enroll_teacher_internal(
            self.license, teacher
        )

        for month in range(1, 4):
            allocation = SchoolCreditAllocation.objects.get(pk=allocation.pk)
            with patch(
                "django.utils.timezone.now",
                return_value=allocation.next_credit_grant_at,
            ):
                process_license_monthly_credit_refreshes()

            latest = (
                CreditBucket.objects.filter(
                    wallet__user=teacher, bucket_type=CreditBucketType.MONTHLY
                )
                .order_by("-created_at")
                .first()
            )
            self.assertEqual(
                latest.total_credits,
                allocation.monthly_allocation,
                f"month {month}: an enrolled teacher must keep receiving "
                "their full monthly allocation",
            )
