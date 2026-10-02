"""
H-88: a teacher under a licence gets one monthly refresh per month of the
contract, on their own anchor day, whatever day of the month they enrolled.

THE BUG
-------
_refresh_teacher_credits set the next due time as `now + 1 month` from the
run that refreshed. relativedelta clamps 31 January to 28 February, and from
then on the chain stays on the 28th: 28 Feb, 28 Mar, ... 28 Jan. That is 12
refreshes plus the enrolment grant, 13 in a 12-month contract. Every due
time also drifted by the run's lateness. H-82 fixed the same chain for
individual annual subscriptions; this is the licence path.

THE FIX
-------
Each allocation has an anchor (grant_anchor_at): the moment of its
enrolment, re-enrolment or reactivation, or of the licence's last renewal,
which restarts every teacher's month. The next due time is the anchor plus
k months (refresh_timing.next_monthly_grant, H-82's helper), for the first
k whose point lies more than a week past the due time just served.

A run that is late (an outage) catches up the refreshes the school paid
for, one per run, each with a WARNING (ids only) and a bucket that lives
from its grant time. The licence's monthly consumption window reopens once
a month, however many refreshes are caught up.

These tests drive the real task (process_license_monthly_credit_refreshes)
at 03:00 on each day a refresh is due, which is equivalent to every daily
run: a run with nothing due does nothing.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.test import TestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.qa_time_travel import QATimeTravelService
from billing.tasks import process_license_monthly_credit_refreshes
from billing.tests.test_annual_grant_anchor import Clock
from classrooms.models import School
from users.models import CustomUser, UserTypes

UTC = dt_timezone.utc
SERVICE_LOGGER = "billing.license_service"
MONTHLY_CREDITS = 20_000_000


def run_time_for(due):
    """The first daily 03:00 run that finds `due` due."""
    run = due.replace(hour=3, minute=0, second=0, microsecond=0)
    return run if run >= due - timedelta(minutes=5) else run + timedelta(days=1)


class LicenceClockTestCase(TestCase):
    """A licence, its teachers and a clock the tests set."""

    carry_over_percent = 0

    def setUp(self):
        self.clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
        patcher = patch("django.utils.timezone.now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.school = School.objects.create(name="H88 School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.POWER_LICENSE,
            display_name="H88 Licence",
            category=PlanCategory.LICENSE,
            tier=PlanTier.POWER,
            interval=BillingInterval.MONTHLY,
            price_cents=19_900,
            monthly_credits=MONTHLY_CREDITS,
            carry_over_percent=self.carry_over_percent,
            carry_over_expiry_months=1,
            is_active=True,
        )
        self.admin = CustomUser.objects.create_user(
            email="h88-admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.n = 0
        # (run time, the teacher's newest MONTHLY bucket's expiry, the
        # licence's consumption window start) after each refresh run
        self.runs = []

    def licence(self, start, months=12):
        self.clock.moment = start
        return LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            contract_months=months,
            max_seats=5,
            billing_cycle_start=start,
            billing_cycle_end=start + relativedelta(months=months),
            consumption_window_start=start,
            billing_method=LicenseBillingMethod.OFFLINE,
            is_active=True,
            auto_renew=True,
        )

    def enrol(self, licence, at):
        self.n += 1
        self.clock.moment = at
        teacher = CustomUser.objects.create_user(
            email=f"h88-teacher-{self.n}@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        return LicenseSubscriptionService._enroll_teacher_internal(licence, teacher)

    def run_refresh(self, allocation, run_at):
        self.clock.moment = run_at
        process_license_monthly_credit_refreshes()
        newest = (
            CreditBucket.objects.filter(
                wallet__user_id=allocation.user_id,
                bucket_type=CreditBucketType.MONTHLY,
            )
            .order_by("-created_at")
            .first()
        )
        assert newest is not None
        window = LicenseSubscription.objects.get(
            pk=allocation.license_subscription_id
        ).consumption_window_start
        self.runs.append((run_at, newest.expires_at, window))

    def drive(self, allocation, outage=None, until=None):
        """Run the refresh task on every day a refresh is due, to the cycle
        end (or `until`). `outage` = (first, last): no run happens between
        them; the first run after it is the next 03:00 after `last`. Runs
        are a day apart, as Beat's are."""
        last_run = None
        while True:
            allocation.refresh_from_db()
            licence = LicenseSubscription.objects.get(
                pk=allocation.license_subscription_id
            )
            due = allocation.next_credit_grant_at
            if due >= licence.billing_cycle_end:
                return
            run_at = run_time_for(due)
            if outage and outage[0] <= run_at <= outage[1]:
                run_at = run_time_for(outage[1])
            if last_run is not None and run_at <= last_run:
                # One run a day: a refresh still owed waits for the next one.
                run_at = last_run + timedelta(days=1)
            if until is not None and run_at >= until:
                return
            last_run = run_at
            self.assertLess(run_at, licence.billing_cycle_end, "the loop ran away")
            self.run_refresh(allocation, run_at)

    def refreshes(self, allocation):
        return list(
            CreditLedger.objects.filter(
                user_id=allocation.user_id,
                ledger_type=CreditLedgerType.GRANT,
                reference=(
                    f"Monthly grant for license {allocation.license_subscription_id}"
                ),
            )
            .order_by("created_at")
            .values_list("created_at", flat=True)
        )

    def anchor_dates(self, start, months):
        return [(start + relativedelta(months=k)).date() for k in months]


class LicenceGrantAnchorTests(LicenceClockTestCase):
    # --- One refresh a month, on the anchor day -----------------------------

    def test_a_teacher_enrolled_on_the_31st_gets_11_refreshes_on_their_days(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)

        self.drive(allocation)

        refreshes = self.refreshes(allocation)
        self.assertEqual(
            [r.date() for r in refreshes], self.anchor_dates(start, range(1, 12))
        )
        self.assertEqual(len(refreshes) + 1, 12, "the enrolment grant + 11")

    def test_control_a_teacher_enrolled_on_the_15th(self):
        start = datetime(2026, 1, 15, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)

        self.drive(allocation)

        self.assertEqual(
            [r.date() for r in self.refreshes(allocation)],
            self.anchor_dates(start, range(1, 12)),
        )

    def test_a_leap_february_from_the_30th(self):
        start = datetime(2028, 1, 30, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)

        self.drive(allocation)

        dates = [r.date() for r in self.refreshes(allocation)]
        self.assertEqual(dates, self.anchor_dates(start, range(1, 12)))
        self.assertEqual(dates[0], datetime(2028, 2, 29).date())
        self.assertEqual(dates[1], datetime(2028, 3, 30).date())

    def test_a_mid_cycle_enrolment_is_anchored_to_its_own_day(self):
        """The licence began on the 10th; the teacher joined on 31 March.
        Their month runs from the 31st, to the licence's end."""
        licence = self.licence(datetime(2026, 1, 10, 1, 0, tzinfo=UTC))
        joined = datetime(2026, 3, 31, 9, 0, tzinfo=UTC)
        allocation = self.enrol(licence, joined)

        self.drive(allocation)

        # 30 Apr ... 31 Dec; 31 Jan 2027 is past the licence's end (10 Jan).
        # A 09:00 due time is found by the next day's 03:00 run.
        self.assertEqual(
            [r.date() for r in self.refreshes(allocation)],
            [d + timedelta(days=1) for d in self.anchor_dates(joined, range(1, 10))],
        )

    # --- The consumption window ------------------------------------------------

    def test_the_consumption_window_reopens_at_every_months_refresh(self):
        """A 31st anchor's months are 28 to 31 days. The window must reopen
        on each refresh, not only on those a full calendar month apart, or
        two months' usage is counted against one month's budget."""
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)

        self.drive(allocation)

        self.assertEqual(len(self.runs), 11)
        for run_at, _, window in self.runs:
            self.assertEqual(
                window.date(), run_at.date(), f"not reopened by the run at {run_at}"
            )

    # --- A renewal restarts the month -----------------------------------------

    def test_a_renewal_mid_chain_restarts_the_teachers_month(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)
        renewed = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
        self.drive(allocation, until=renewed)

        self.clock.moment = renewed
        licence.refresh_from_db()
        LicenseSubscriptionService.process_offline_renewal(
            licence,
            performed_by=self.admin,
            new_billing_cycle_end=renewed + relativedelta(months=12),
        )
        self.drive(allocation)

        dates = [r.date() for r in self.refreshes(allocation)]
        # 28 Feb and 31 Mar on the old anchor; then the 20th's chain, each
        # 12:00 due time found by the next day's run.
        self.assertEqual(
            dates,
            [datetime(2026, 2, 28).date(), datetime(2026, 3, 31).date()]
            + [d + timedelta(days=1) for d in self.anchor_dates(renewed, range(1, 12))],
        )


class LicenceOutageTests(LicenceClockTestCase):
    carry_over_percent = 50

    OUTAGE = (
        datetime(2026, 3, 1, tzinfo=UTC),
        datetime(2026, 5, 31, 23, 0, tzinfo=UTC),
    )

    def test_a_three_month_outage_is_caught_up_one_refresh_per_run(self):
        start = datetime(2026, 1, 15, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)

        with self.assertLogs(SERVICE_LOGGER, "WARNING") as logs:
            self.drive(allocation, outage=self.OUTAGE)

        refreshes = self.refreshes(allocation)
        self.assertEqual(len(refreshes) + 1, 12, "every paid-for month is granted")
        # March, April and May's refreshes land on 1, 2 and 3 June; June's
        # on its own day.
        self.assertEqual(
            [r.date() for r in refreshes[1:5]],
            [
                datetime(2026, 6, 1).date(),
                datetime(2026, 6, 2).date(),
                datetime(2026, 6, 3).date(),
                datetime(2026, 6, 15).date(),
            ],
        )
        caught_up = [line for line in logs.output if "caught up" in line]
        self.assertEqual(len(caught_up), 3, logs.output)
        for line in caught_up:
            self.assertIn("WARNING", line)
            self.assertIn(str(allocation.id), line)
            self.assertNotIn("@", line)

    def test_a_catch_up_never_grants_more_than_the_months_paid_for(self):
        """The SM's check: N refreshes on consecutive days grant N months'
        allocations and no more, the carry-over comes out of credits already
        granted, and the consumption window reopens once, not N times."""
        start = datetime(2026, 1, 15, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)

        self.drive(allocation, outage=self.OUTAGE)

        monthly = CreditLedger.objects.filter(
            user_id=allocation.user_id,
            ledger_type=CreditLedgerType.GRANT,
            bucket__bucket_type=CreditBucketType.MONTHLY,
        )
        self.assertEqual(monthly.count(), 12)
        self.assertEqual(
            sum(entry.amount for entry in monthly), 12 * allocation.monthly_allocation
        )
        for bucket in CreditBucket.objects.filter(
            wallet__user_id=allocation.user_id,
            bucket_type=CreditBucketType.CARRY_OVER,
        ):
            self.assertLessEqual(bucket.total_credits, allocation.monthly_allocation)
        catch_up = [
            window
            for run_at, _, window in self.runs
            if datetime(2026, 6, 1, tzinfo=UTC)
            <= run_at
            < datetime(2026, 6, 4, tzinfo=UTC)
        ]
        self.assertEqual(len(catch_up), 3)
        self.assertEqual(
            {window.date() for window in catch_up}, {datetime(2026, 6, 1).date()}
        )

    def test_no_refreshed_bucket_is_born_expired_or_outlives_the_contract(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)

        self.drive(allocation, outage=self.OUTAGE)

        self.assertEqual(len(self.runs), 11)
        for run_at, expires_at, _ in self.runs:
            self.assertGreater(
                expires_at - run_at,
                timedelta(days=1),
                f"the bucket granted at {run_at} expires at {expires_at}",
            )
            self.assertLessEqual(expires_at, licence.billing_cycle_end)

    def test_a_catch_up_at_the_cycle_end_never_outlives_the_contract(self):
        """An outage over the last two anchors, ending two days before the
        contract does (1a's V3 for H-82, on the licence path)."""
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)
        end = licence.billing_cycle_end
        outage = (
            start + relativedelta(months=10) - timedelta(days=1),
            end - timedelta(days=2),
        )

        self.drive(allocation, outage=outage)

        self.assertEqual(len(self.refreshes(allocation)) + 1, 12)
        for run_at, expires_at, _ in self.runs:
            self.assertGreater(expires_at, run_at)
            self.assertLessEqual(
                expires_at, end, f"the bucket granted at {run_at} outlives {end}"
            )


class RowsOlderThanTheAnchorTests(LicenceClockTestCase):
    """Allocations written before grant_anchor_at existed: no stored anchor,
    and a due time from the old `now + 1 month` chain."""

    def old_row(self, allocation, due):
        SchoolCreditAllocation.objects.filter(pk=allocation.pk).update(
            grant_anchor_at=None, next_credit_grant_at=due
        )

    def test_an_enrolment_stores_its_anchor(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)

        self.assertIsNotNone(allocation.grant_anchor_at)
        self.assertLess(allocation.grant_anchor_at - start, timedelta(seconds=1))

    def test_a_drifted_row_moves_to_the_next_anchor_period_not_back_to_this_one(self):
        """The transition hazard: a 31 January licence whose old chain is on
        28 March. That refresh is March's; the next is 30 April, not 31
        March (a second refresh three days later)."""
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)
        self.run_refresh(allocation, datetime(2026, 2, 28, 3, 0, tzinfo=UTC))
        drifted = datetime(2026, 3, 28, 3, 0, tzinfo=UTC)
        self.old_row(allocation, drifted)

        self.run_refresh(allocation, drifted)
        allocation.refresh_from_db()
        self.assertEqual(
            allocation.next_credit_grant_at.replace(microsecond=0),
            datetime(2026, 4, 30, 1, 0, tzinfo=UTC),
        )
        # The fallback (the licence's start) is now the row's stored anchor.
        self.assertLess(allocation.grant_anchor_at - start, timedelta(seconds=1))

        self.drive(allocation)
        dates = [r.date() for r in self.refreshes(allocation)]
        self.assertEqual(
            dates,
            [datetime(2026, 2, 28).date(), drifted.date()]
            + self.anchor_dates(start, range(3, 12)),
        )
        self.assertEqual(len(dates) + 1, 12)

    def test_a_row_re_enrolled_before_the_field_keeps_its_own_rhythm(self):
        """Re-enrolled on the 23rd under a licence that began on the 10th,
        before anyone stored the moment. Its due time is its anchor: the
        23rd of each month, with no irregular interval."""
        start = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)
        due = datetime(2026, 3, 23, 3, 0, tzinfo=UTC)
        self.old_row(allocation, due)

        self.run_refresh(allocation, due)
        allocation.refresh_from_db()
        self.assertEqual(allocation.grant_anchor_at, due)
        self.assertEqual(
            allocation.next_credit_grant_at, datetime(2026, 4, 23, 3, 0, tzinfo=UTC)
        )

        self.drive(allocation)
        self.assertEqual(
            [r.date() for r in self.refreshes(allocation)],
            self.anchor_dates(due, range(0, 10)),
        )

    def test_a_renewal_overwrites_the_stored_anchor(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)
        renewed = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)

        self.clock.moment = renewed
        LicenseSubscriptionService.process_offline_renewal(
            licence,
            performed_by=self.admin,
            new_billing_cycle_end=renewed + relativedelta(months=12),
        )

        allocation.refresh_from_db()
        self.assertLess(allocation.grant_anchor_at - renewed, timedelta(seconds=1))
        self.assertGreaterEqual(allocation.grant_anchor_at, renewed)

    def test_the_renewal_task_overwrites_the_stored_anchor(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)
        renewed = licence.billing_cycle_end + timedelta(hours=1)

        self.clock.moment = renewed
        LicenseSubscriptionService.process_license_renewal(licence)

        allocation.refresh_from_db()
        self.assertGreaterEqual(allocation.grant_anchor_at, renewed)
        self.assertLess(allocation.grant_anchor_at - renewed, timedelta(seconds=1))

    def test_an_old_row_from_before_the_last_renewal_is_anchored_to_the_renewal(self):
        """The fallback is the LATER of the row's creation and the licence's
        cycle start: a renewal on 20 April restarted this teacher's month,
        whatever day they first enrolled."""
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)
        renewed = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
        LicenseSubscription.objects.filter(pk=licence.pk).update(
            billing_cycle_start=renewed,
            billing_cycle_end=renewed + relativedelta(months=12),
        )
        due = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
        self.old_row(allocation, due)

        self.run_refresh(allocation, datetime(2026, 5, 21, 3, 0, tzinfo=UTC))

        allocation.refresh_from_db()
        self.assertEqual(allocation.grant_anchor_at, renewed)
        self.assertEqual(
            allocation.next_credit_grant_at, datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
        )

    def test_the_admin_allocation_stores_its_anchor_and_restarts_on_reactivation(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        self.clock.moment = start
        admin_allocation = LicenseSubscriptionService._grant_admin_allocation(licence)
        anchor = admin_allocation.grant_anchor_at
        assert anchor is not None
        self.assertGreaterEqual(anchor, start)
        self.assertLess(anchor - start, timedelta(seconds=1))

        SchoolCreditAllocation.objects.filter(pk=admin_allocation.pk).update(
            is_active=False
        )
        later = datetime(2026, 3, 12, 9, 0, tzinfo=UTC)
        self.clock.moment = later
        reactivated = LicenseSubscriptionService._grant_admin_allocation(licence)

        self.assertEqual(reactivated.pk, admin_allocation.pk)
        anchor = reactivated.grant_anchor_at
        assert anchor is not None
        self.assertGreaterEqual(anchor, later)
        self.assertLess(anchor - later, timedelta(seconds=1))


class MovedDueTimeTests(LicenceClockTestCase):
    """1a's F1: a stored anchor is only as good as the due time that sits on
    its chain. If something moves the due time and not the anchor (the QA
    time-travel tool; old code after a rollback, which renews and re-enrols
    without knowing the column), trusting the anchor would bring the next
    refresh back to the old chain within days."""

    def test_a_due_time_moved_off_its_stored_anchor_becomes_the_anchor(self):
        """1a's example: anchor 5 January, due time moved to 28 March. The
        next refresh is 28 April, not 5 April (8 days later)."""
        start = datetime(2026, 1, 5, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)
        moved = datetime(2026, 3, 28, 3, 0, tzinfo=UTC)
        SchoolCreditAllocation.objects.filter(pk=allocation.pk).update(
            next_credit_grant_at=moved
        )

        self.run_refresh(allocation, moved)

        allocation.refresh_from_db()
        self.assertEqual(
            allocation.next_credit_grant_at, datetime(2026, 4, 28, 3, 0, tzinfo=UTC)
        )
        self.assertEqual(allocation.grant_anchor_at, moved)

    def test_a_stale_anchor_heals_at_the_next_refresh_and_then_holds(self):
        """After a rollback and roll-forward: one refresh re-anchors the row
        to its due time, and the chain is monthly from there."""
        start = datetime(2026, 1, 5, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)
        moved = datetime(2026, 3, 28, 3, 0, tzinfo=UTC)
        SchoolCreditAllocation.objects.filter(pk=allocation.pk).update(
            next_credit_grant_at=moved
        )

        self.drive(allocation)

        self.assertEqual(
            [r.date() for r in self.refreshes(allocation)],
            self.anchor_dates(moved, range(0, 10)),
        )

    def test_the_qa_time_travel_tool_clears_the_anchor_with_the_due_time(self):
        start = datetime(2026, 1, 5, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)
        self.clock.moment = datetime(2026, 3, 28, 4, 0, tzinfo=UTC)
        target = datetime(2026, 3, 28, 3, 0, tzinfo=UTC)

        QATimeTravelService.rewind_license_subscription(
            licence.id, "mid_cycle_grant", target
        )

        allocation.refresh_from_db()
        self.assertEqual(allocation.next_credit_grant_at, target)
        self.assertIsNone(allocation.grant_anchor_at)


class ConsumptionWindowOnLicencePointsTests(LicenceClockTestCase):
    """H-93: the licence's consumption window reopens on the LICENCE's own
    monthly points (its cycle start plus k months), at the first teacher
    refresh after each one: 12 windows a year, whatever days the teachers
    are anchored on (1a's O1: with teachers on the 1st and the 25th, a rule
    based on the window's age reopened it about every 24 days)."""

    def windows(self, licence, first, last):
        """Run the task at 03:00 every day; the distinct window starts."""
        seen = []
        day = first.replace(hour=3, minute=0, second=0, microsecond=0)
        while day < last:
            self.clock.moment = day
            process_license_monthly_credit_refreshes()
            window = LicenseSubscription.objects.get(
                pk=licence.pk
            ).consumption_window_start
            if not seen or window != seen[-1]:
                seen.append(window)
            day += timedelta(days=1)
        return seen

    def test_teachers_on_the_1st_and_the_25th_share_12_windows_a_year(self):
        start = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        self.enrol(licence, start)
        self.enrol(licence, datetime(2026, 1, 25, 9, 0, tzinfo=UTC))

        windows = self.windows(licence, start, licence.billing_cycle_end)

        self.assertEqual(
            [w.date() for w in windows], self.anchor_dates(start, range(0, 12))
        )

    def test_a_teacher_anchored_off_the_licences_day_still_gives_12_windows(self):
        """Nobody is anchored on the licence's day (the 1st): the window
        reopens at the first refresh after each licence point, the 11th."""
        start = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        self.enrol(licence, datetime(2026, 1, 10, 9, 0, tzinfo=UTC))

        windows = self.windows(licence, start, licence.billing_cycle_end)

        self.assertEqual(len(windows), 12)
        self.assertEqual({w.day for w in windows[1:]}, {11})

    def test_after_an_outage_the_next_licence_point_reopens_on_time(self):
        """The first catch-up refresh reopens the window; so does the
        licence's next point two weeks later (no six-week window)."""
        start = datetime(2026, 1, 15, 1, 0, tzinfo=UTC)
        allocation = self.enrol(self.licence(start), start)
        outage = (
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 5, 31, 23, 0, tzinfo=UTC),
        )

        self.drive(allocation, outage=outage)

        reopened = {run_at.date(): window.date() for run_at, _, window in self.runs}
        june = datetime(2026, 6, 1).date()
        self.assertEqual(reopened[june], june)
        self.assertEqual(reopened[datetime(2026, 6, 2).date()], june)
        self.assertEqual(reopened[datetime(2026, 6, 3).date()], june)
        mid_june = datetime(2026, 6, 15).date()
        self.assertEqual(reopened[mid_june], mid_june)

    def test_a_renewal_mid_year_moves_the_points_to_the_new_cycle_start(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)
        renewed = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
        self.drive(allocation, until=renewed)
        self.clock.moment = renewed
        licence.refresh_from_db()
        LicenseSubscriptionService.process_offline_renewal(
            licence,
            performed_by=self.admin,
            new_billing_cycle_end=renewed + relativedelta(months=12),
        )
        licence.refresh_from_db()
        self.assertEqual(licence.consumption_window_start.date(), renewed.date())
        self.runs.clear()

        self.drive(allocation)

        # Each 12:00 point is met by the next day's 03:00 run.
        self.assertEqual(
            [window.date() for _, _, window in self.runs],
            [d + timedelta(days=1) for d in self.anchor_dates(renewed, range(1, 12))],
        )

    def test_a_point_within_the_due_tolerance_of_the_run_reopens(self):
        """A licence that began at 03:02: the 03:00 run refreshes its
        teacher (due within the tolerance), so it reopens the window too."""
        start = datetime(2026, 1, 31, 3, 2, tzinfo=UTC)
        licence = self.licence(start)
        allocation = self.enrol(licence, start)

        self.run_refresh(allocation, datetime(2026, 2, 28, 3, 0, tzinfo=UTC))

        self.assertEqual(len(self.refreshes(allocation)), 1)
        self.assertEqual(self.runs[-1][2].date(), datetime(2026, 2, 28).date())
