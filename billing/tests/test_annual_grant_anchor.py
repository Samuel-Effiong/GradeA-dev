"""
H-82: an annual subscription gets exactly 12 monthly grants per contract
year, on its own anchor day, whatever day of the month it started.

THE BUG
-------
process_mid_cycle_credit_grant set the next due time as `now + 1 month`
from the run that granted. relativedelta clamps 31 January to 28 February,
and from then on the chain stays on the 28th: 28 Feb, 28 Mar, ... 28 Jan.
That is 12 mid-cycle grants plus the activation grant, 13 in a 12-month
contract (1a's probe C, H-65 verification N3). Every month's due time also
drifted by the run's lateness.

THE FIX
-------
The next due time is the anchor (billing_cycle_start) plus k months,
computed from the anchor each time, for the first k whose point lies more
than a week past the due time just served. Not `> now`: rows in production
have already drifted (a 28 Mar due on a 31 Jan anchor), and "the first
anchor point after now" would grant again on 31 Mar, three days later.

A run that is late (an outage) catches up the grants the customer paid
for, one per run, each with a WARNING (ids only) and a bucket that lives
from its grant time, not one born expired.

These tests drive the real task (process_annual_plan_credit_grants) at
02:00 on each day a grant is due, which is equivalent to every daily run:
a run with nothing due does nothing.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.test import TestCase

from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.tasks import process_annual_plan_credit_grants
from billing.tests.test_annual_mid_cycle_grants import make_annual_plan
from billing.tests.test_beat_lock_catch_up import make_clean_user

UTC = dt_timezone.utc
TASKS_LOGGER = "billing.services"


class Clock:
    """timezone.now() for the code under test: a set moment, advancing a
    few milliseconds per call so rows created in one run still order."""

    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        self.moment += timedelta(milliseconds=3)
        return self.moment


def run_time_for(due):
    """The first daily 02:00 run that finds `due` due."""
    run = due.replace(hour=2, minute=0, second=0, microsecond=0)
    return run if run >= due - timedelta(minutes=5) else run + timedelta(days=1)


class AnnualGrantAnchorTests(TestCase):
    def setUp(self):
        self.clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
        patcher = patch("django.utils.timezone.now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plan = make_annual_plan()
        self.n = 0
        # (run time, the newest MONTHLY bucket's expiry) after each grant run
        self.births = []

    def subscribe(self, start):
        self.n += 1
        self.clock.moment = start
        user, wallet = make_clean_user(f"h82-{self.n}@example.com")
        sub = SubscriptionService.activate_subscription(
            user,
            self.plan,
            period_start=start,
            period_end=start + relativedelta(years=1),
        )
        return sub

    def drive(self, sub, outage=None):
        """Run the grant task on every day a grant is due, to the cycle end.
        `outage` = (first, last): no run happens between them; the first run
        after it is at 02:00 on the day after `last`."""
        while True:
            sub.refresh_from_db()
            due = sub.next_credit_grant_at
            if due >= sub.billing_cycle_end:
                return
            run_at = run_time_for(due)
            if outage and outage[0] <= run_at <= outage[1]:
                run_at = run_time_for(outage[1] + timedelta(days=1))
            self.assertLess(run_at, sub.billing_cycle_end, "the loop ran away")
            self.clock.moment = run_at
            process_annual_plan_credit_grants()
            newest = (
                CreditBucket.objects.filter(
                    wallet__user_id=sub.user_id,
                    bucket_type=CreditBucketType.MONTHLY,
                )
                .order_by("-created_at")
                .first()
            )
            assert newest is not None
            self.births.append((run_at, newest.expires_at))

    def mid_cycle_grants(self, sub):
        return list(
            CreditLedger.objects.filter(
                user_id=sub.user_id,
                ledger_type=CreditLedgerType.GRANT,
                metadata__grant_type="ANNUAL_MID_CYCLE",
            )
            .order_by("created_at")
            .values_list("created_at", flat=True)
        )

    def anchor_dates(self, start, months):
        return [(start + relativedelta(months=k)).date() for k in months]

    # --- 12 grants a year, on the anchor day --------------------------------

    def test_a_subscription_from_the_31st_gets_12_grants_on_its_own_days(self):
        """1a's probe C start: 31 January, 01:00."""
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        sub = self.subscribe(start)

        self.drive(sub)

        grants = self.mid_cycle_grants(sub)
        self.assertEqual(
            [g.date() for g in grants], self.anchor_dates(start, range(1, 12))
        )
        self.assertEqual(len(grants) + 1, 12, "activation + 11 mid-cycle")

    def test_control_a_subscription_from_the_15th(self):
        start = datetime(2026, 1, 15, 1, 0, tzinfo=UTC)
        sub = self.subscribe(start)

        self.drive(sub)

        self.assertEqual(
            [g.date() for g in self.mid_cycle_grants(sub)],
            self.anchor_dates(start, range(1, 12)),
        )

    def test_a_leap_february_from_the_30th(self):
        start = datetime(2028, 1, 30, 1, 0, tzinfo=UTC)
        sub = self.subscribe(start)

        self.drive(sub)

        grants = [g.date() for g in self.mid_cycle_grants(sub)]
        self.assertEqual(grants, self.anchor_dates(start, range(1, 12)))
        self.assertEqual(grants[0], datetime(2028, 2, 29).date())
        self.assertEqual(grants[1], datetime(2028, 3, 30).date())

    # --- A row that has already drifted ---------------------------------------

    def test_a_drifted_row_moves_to_the_next_anchor_period_not_back_to_this_one(self):
        """The transition hazard: a 31 Jan subscription whose old chain is on
        28 Mar. Its 28 Mar grant is March's; the next is 30 April, not 31
        March (a second grant three days later)."""
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        sub = self.subscribe(start)
        self.clock.moment = datetime(2026, 2, 28, 2, 0, tzinfo=UTC)
        process_annual_plan_credit_grants()
        drifted = datetime(2026, 3, 28, 2, 0, tzinfo=UTC)
        UserSubscription.objects.filter(pk=sub.pk).update(next_credit_grant_at=drifted)

        self.clock.moment = drifted + timedelta(minutes=1)
        process_annual_plan_credit_grants()
        sub.refresh_from_db()
        self.assertEqual(
            sub.next_credit_grant_at, datetime(2026, 4, 30, 1, 0, tzinfo=UTC)
        )

        self.drive(sub)
        grants = [g.date() for g in self.mid_cycle_grants(sub)]
        self.assertEqual(
            grants,
            [datetime(2026, 2, 28).date(), drifted.date()]
            + self.anchor_dates(start, range(3, 12)),
        )
        self.assertEqual(len(grants) + 1, 12)

    # --- An outage is caught up ---------------------------------------------

    def test_a_three_month_outage_is_caught_up_one_grant_per_run(self):
        start = datetime(2026, 1, 15, 1, 0, tzinfo=UTC)
        sub = self.subscribe(start)
        outage = (
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 5, 31, 23, 0, tzinfo=UTC),
        )

        with self.assertLogs(TASKS_LOGGER, "WARNING") as logs:
            self.drive(sub, outage=outage)

        grants = self.mid_cycle_grants(sub)
        self.assertEqual(len(grants) + 1, 12, "every paid-for grant is granted")
        # March, April and May's grants land on 1, 2 and 3 June; June's on
        # its own day.
        self.assertEqual(
            [g.date() for g in grants[1:5]],
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
            self.assertIn(str(sub.id), line)
            self.assertNotIn("@", line)

    def test_no_granted_bucket_is_born_expired(self):
        start = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        sub = self.subscribe(start)
        outage = (
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 5, 31, 23, 0, tzinfo=UTC),
        )

        self.drive(sub, outage=outage)

        self.assertEqual(len(self.births), 11)
        for run_at, expires_at in self.births:
            self.assertGreater(
                expires_at - run_at,
                timedelta(days=1),
                f"the bucket granted at {run_at} expires at {expires_at}",
            )
