"""
Coverage for LicenseSubscription.total_credits_consumed accounting.

The rollup used to live in a post_save signal on CreditUsageLog
(billing/signals.py) — which NEVER fired, because consume_credits creates
its usage logs with bulk_create(), and bulk_create does not emit
post_save. The counter therefore silently stayed at 0 for every license.
The rollup is now an explicit call inside CreditWallet.consume_credits
(_record_license_consumption), and refund_credits reverses it (clamped at
zero). These tests pin both directions plus the exclusions.

Run with:
    python manage.py test billing.tests.test_license_consumption_accounting
"""

from billing.models import LicenseSubscription, PlanCategory, PlanType
from billing.services import SubscriptionService
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase
from users.models import UserTypes


class LicenseConsumptionRollupTests(ExecuteGradedTaskTestBase):
    def setUp(self):
        super().setUp()
        plan = self._make_plan(PlanType.POWER_LICENSE, category=PlanCategory.LICENSE)
        self.admin = self._make_user(UserTypes.SCHOOL_ADMIN, "lic-admin@example.com")
        self.license_sub = self._make_license(plan, self.admin)
        self.teacher = self._make_user(UserTypes.TEACHER, "lic-teacher@example.com")
        self._make_allocation(self.license_sub, self.teacher)
        self.wallet = self._give_credits(self.teacher, 100_000)

    def _license_total(self):
        return LicenseSubscription.objects.get(
            pk=self.license_sub.pk
        ).total_credits_consumed

    def test_consumption_increments_the_license_counter(self):
        self.wallet.consume_credits(
            amount=700, feature="Grading Assignment", task_id="lic-task-1"
        )
        self.assertEqual(self._license_total(), 700)

        self.wallet.consume_credits(
            amount=300, feature="Grading Assignment", task_id="lic-task-2"
        )
        self.assertEqual(self._license_total(), 1_000)

    def test_refund_reverses_the_license_counter(self):
        self.wallet.consume_credits(
            amount=700, feature="Grading Assignment", task_id="lic-task-3"
        )
        refunded = SubscriptionService.refund_credits("lic-task-3")

        self.assertEqual(refunded, 700)
        self.assertEqual(self._license_total(), 0)

    def test_refund_clamps_at_zero_after_cycle_reset(self):
        # A renewal legitimately resets the per-cycle counter between the
        # consume and the refund; the reversal must clamp, not go negative
        # (the field is a PositiveIntegerField).
        self.wallet.consume_credits(
            amount=700, feature="Grading Assignment", task_id="lic-task-4"
        )
        LicenseSubscription.objects.filter(pk=self.license_sub.pk).update(
            total_credits_consumed=100
        )

        SubscriptionService.refund_credits("lic-task-4")

        self.assertEqual(self._license_total(), 0)

    def test_admin_analytics_allocation_is_excluded(self):
        # is_admin_allocation=True rows are analytics-only and must not
        # count toward the school's consumption cap — same exclusion every
        # other license-consumption computation applies.
        self._make_allocation(self.license_sub, self.admin, is_admin=True)
        admin_wallet = self._give_credits(self.admin, 100_000)

        admin_wallet.consume_credits(
            amount=500, feature="Weekly Course Summary", task_id="lic-task-5"
        )

        self.assertEqual(self._license_total(), 0)

    def test_individual_teacher_without_allocation_is_untouched(self):
        individual = self._make_teacher_with_credits()

        individual.credit_wallet.consume_credits(
            amount=500, feature="Grading Assignment", task_id="lic-task-6"
        )

        self.assertEqual(self._license_total(), 0)
