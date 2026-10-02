"""
H-81: a renewal reports the monthly grants that came due in the ending
cycle and were never made.

HOW A GRANT IS LOST
-------------------
The monthly grant tasks stop serving a due time once the cycle has ended:
the renewal owns that boundary. So if Beat is down from a grant's due time
to the cycle's end, that grant is never made, and until now nothing said
so (1a's H-65 N2; and 1a's H-82 O2 for a contract's last grant).

THE DETECTION
-------------
At the moment a renewal replaces the due time, a due time more than a week
before the old cycle's end is a grant that was owed. The renewal logs it
at ERROR, with ids only and the number owed. It grants nothing: support
credits the customer by hand. A subscription or licence that ends without
renewing is not reported.

Covered: the individual renewal (process_rollover_and_renewal) and both
licence renewals (process_license_renewal, process_offline_renewal).
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.test import TestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import SchoolCreditAllocation, UserSubscription
from billing.services import SubscriptionService
from billing.tests.test_annual_grant_anchor import Clock
from billing.tests.test_annual_mid_cycle_grants import make_annual_plan
from billing.tests.test_beat_lock_catch_up import make_clean_user
from billing.tests.test_licence_grant_anchor import LicenceClockTestCase

UTC = dt_timezone.utc
START = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
END = START + relativedelta(years=1)


def owed_lines(logs):
    return [line for line in logs.output if "owed" in line]


class LicenceRenewalReportsOwedRefreshesTests(LicenceClockTestCase):
    def setUp(self):
        super().setUp()
        self.licence_sub = self.licence(START)
        self.allocation = self.enrol(self.licence_sub, START)

    def stop_the_chain_at(self, due):
        SchoolCreditAllocation.objects.filter(pk=self.allocation.pk).update(
            next_credit_grant_at=due
        )

    def renew_offline(self, at):
        self.clock.moment = at
        self.licence_sub.refresh_from_db()
        LicenseSubscriptionService.process_offline_renewal(
            self.licence_sub,
            performed_by=self.admin,
            new_billing_cycle_end=at + relativedelta(months=12),
        )

    def renew_by_task(self, at):
        self.clock.moment = at
        self.licence_sub.refresh_from_db()
        LicenseSubscriptionService.process_license_renewal(self.licence_sub)

    def assertOwed(self, logs, count):
        [line] = owed_lines(logs)
        self.assertIn("ERROR", line)
        self.assertIn(f"owed {count} ", line)
        for part in (
            self.licence_sub.id,
            self.allocation.id,
            self.allocation.user_id,
        ):
            self.assertIn(str(part), line)
        self.assertNotIn("@", line)

    def test_an_outage_to_the_contract_end_is_reported_by_the_offline_renewal(self):
        """1a's O2, driven: Beat stops before the last two anchors and the
        superadmin renews an hour after the cycle ends."""
        self.drive(self.allocation, until=START + relativedelta(months=10))

        with self.assertLogs("billing.license_service", "ERROR") as logs:
            self.renew_offline(END + timedelta(hours=1))

        self.assertOwed(logs, 2)

    def test_the_last_refresh_never_made_is_reported_by_the_renewal_task(self):
        self.stop_the_chain_at(START + relativedelta(months=11))

        with self.assertLogs("billing.license_service", "ERROR") as logs:
            self.renew_by_task(END + timedelta(hours=1))

        self.assertOwed(logs, 1)

    def test_control_a_chain_served_to_the_end_reports_nothing(self):
        self.drive(self.allocation)

        with self.assertNoLogs("billing.license_service", "ERROR"):
            self.renew_by_task(END + timedelta(hours=1))

    def test_a_row_drifted_to_the_28th_is_not_reported_as_owed(self):
        """An old chain's last due time, three days before a cycle ending
        on the 31st, is the renewal's own period."""
        self.stop_the_chain_at(END - timedelta(days=3))

        with self.assertNoLogs("billing.license_service", "ERROR"):
            self.renew_by_task(END + timedelta(hours=1))

    def test_an_early_offline_renewal_reports_nothing(self):
        """A superadmin renewing in month 4: the later refreshes of the old
        cycle are in the future, not owed."""
        early = START + relativedelta(months=3) + timedelta(days=10)
        self.drive(self.allocation, until=early)

        with self.assertNoLogs("billing.license_service", "ERROR"):
            self.renew_offline(early)

    def test_the_renewal_still_renews(self):
        """Detection only: the renewal goes on, and grants one month."""
        self.stop_the_chain_at(START + relativedelta(months=9))
        before = len(self.refreshes(self.allocation))

        with self.assertLogs("billing.license_service", "ERROR") as logs:
            self.renew_offline(END + timedelta(hours=1))

        self.assertOwed(logs, 3)
        self.assertEqual(len(self.refreshes(self.allocation)), before)
        self.allocation.refresh_from_db()
        self.assertEqual(
            self.allocation.next_credit_grant_at.date(),
            (END + relativedelta(months=1)).date(),
        )


class IndividualRenewalReportsOwedGrantsTests(TestCase):
    def setUp(self):
        self.clock = Clock(START)
        patcher = patch("django.utils.timezone.now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user, _ = make_clean_user("h81-annual@example.com")
        self.sub = SubscriptionService.activate_subscription(
            self.user, make_annual_plan(), period_start=START, period_end=END
        )

    def renew(self, next_due):
        UserSubscription.objects.filter(pk=self.sub.pk).update(
            next_credit_grant_at=next_due
        )
        self.clock.moment = END + timedelta(hours=1)
        return SubscriptionService.process_rollover_and_renewal(
            self.sub, period_start=END, period_end=END + relativedelta(years=1)
        )

    def test_the_last_grant_never_made_is_reported(self):
        """1a's H-82 O2."""
        with self.assertLogs("billing.services", "ERROR") as logs:
            self.renew(START + relativedelta(months=11))

        [line] = owed_lines(logs)
        self.assertIn("ERROR", line)
        self.assertIn("1 monthly", line)
        self.assertIn(str(self.sub.id), line)
        self.assertIn(str(self.user.id), line)
        self.assertNotIn("@", line)

    def test_three_grants_never_made_are_counted(self):
        with self.assertLogs("billing.services", "ERROR") as logs:
            renewed = self.renew(START + relativedelta(months=9))

        [line] = owed_lines(logs)
        self.assertIn("3 monthly", line)
        # Detection only: the renewal went on.
        self.assertTrue(renewed.is_active)
        self.assertNotEqual(renewed.pk, self.sub.pk)

    def test_control_a_chain_served_to_the_end_reports_nothing(self):
        with self.assertNoLogs("billing.services", "ERROR"):
            self.renew(END)

    def test_a_row_drifted_to_the_28th_is_not_reported_as_owed(self):
        with self.assertNoLogs("billing.services", "ERROR"):
            self.renew(END - timedelta(days=3))
