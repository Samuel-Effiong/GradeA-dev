"""
H-82: refresh_timing.next_monthly_grant, the anchored monthly due time.

Property-style: every start day 1-31 (and a leap February), on-time, late
and skipped runs, a three-month outage, and rows already drifted by the
old `now + 1 month` chain. In every case an annual contract gets exactly 12
grants (the activation grant plus 11), never two in one anchor period, and
none at or after the contract's end.

The chain is simulated the way process_annual_plan_credit_grants drives
it: a run at time t grants when the row's due time is at or before t, and
the next due time comes from the helper. A run's time never feeds the
helper, so the run pattern can only delay grants, not add or drop them.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from dateutil.relativedelta import relativedelta
from django.test import SimpleTestCase

from billing.refresh_timing import ANCHOR_SNAP, next_monthly_grant

UTC = dt_timezone.utc


def anchor_points(anchor, end):
    points = []
    k = 1
    while anchor + relativedelta(months=k) < end:
        points.append(anchor + relativedelta(months=k))
        k += 1
    return points


def simulate(anchor, runs, first_due=None):
    """The due times served, in order, for runs at `runs` (sorted)."""
    end = anchor + relativedelta(years=1)
    due = first_due or anchor + relativedelta(months=1)
    served = []
    for run in runs:
        if due >= end:
            break
        if due <= run:
            served.append(due)
            _, due = next_monthly_grant(anchor, due, end)
    return served, due, end


def daily_runs(anchor, hour=2, skip=lambda day: False):
    end = anchor + relativedelta(years=1) + timedelta(days=40)
    day = anchor.replace(hour=hour, minute=0, second=0, microsecond=0)
    runs = []
    while day < end:
        if not skip(day):
            runs.append(day)
        day += timedelta(days=1)
    return runs


def period_of(anchor, moment):
    """The anchor period [anchor + k, anchor + k + 1 month) holding moment."""
    k = 0
    while anchor + relativedelta(months=k + 1) <= moment:
        k += 1
    return k


class NextMonthlyGrantTests(SimpleTestCase):
    def assert_twelve_a_year(self, anchor, served, end):
        self.assertEqual(len(served), 11, f"anchor {anchor}: {served}")
        self.assertTrue(all(due < end for due in served))
        periods = [period_of(anchor, due + ANCHOR_SNAP) for due in served]
        self.assertEqual(len(set(periods)), len(periods), "two in one period")

    def test_every_start_day_gets_its_own_anchor_points(self):
        for year in (2026, 2027, 2028):  # 2028: a leap February
            for month in (1, 3, 8, 12):
                for day in range(1, 32):
                    try:
                        anchor = datetime(year, month, day, 1, 0, tzinfo=UTC)
                    except ValueError:
                        continue
                    with self.subTest(anchor=anchor):
                        served, last, end = simulate(anchor, daily_runs(anchor))
                        self.assertEqual(served, anchor_points(anchor, end))
                        self.assertEqual(last, end, "the chain ends at the renewal")
                        self.assert_twelve_a_year(anchor, served, end)

    def test_late_and_skipped_runs_only_delay(self):
        anchor = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        patterns = {
            "a run in the afternoon": daily_runs(anchor, hour=15),
            "every other day": daily_runs(anchor, skip=lambda d: d.day % 2 == 0),
            "weekly": daily_runs(anchor, skip=lambda d: d.weekday() != 0),
            "a three-month outage": daily_runs(
                anchor,
                skip=lambda d: datetime(2026, 3, 1, tzinfo=UTC)
                <= d
                < datetime(2026, 6, 1, tzinfo=UTC),
            ),
        }
        for name, runs in patterns.items():
            with self.subTest(name):
                served, _, end = simulate(anchor, runs)
                self.assertEqual(served, anchor_points(anchor, end))
                self.assert_twelve_a_year(anchor, served, end)

    def test_a_drifted_chain_converges_without_a_double_grant(self):
        """Rows on the old chain: any start day, drifted by up to 3 days
        early at any month. The served due (drifted) is its own period's
        grant; the next is the following anchor point."""
        for day in range(1, 32):
            try:
                anchor = datetime(2026, 1, day, 1, 0, tzinfo=UTC)
            except ValueError:
                continue
            end = anchor + relativedelta(years=1)
            points = anchor_points(anchor, end)
            for i, point in enumerate(points[:-1]):
                for drift_days in (0, 1, 2, 3):
                    drifted = point - timedelta(days=drift_days, hours=-1)
                    with self.subTest(anchor=anchor, point=point, drift=drift_days):
                        k, due = next_monthly_grant(anchor, drifted, end)
                        self.assertEqual(due, points[i + 1])
                        self.assertEqual(k, i + 2)

    def test_the_example_28_march_on_a_31_january_anchor(self):
        anchor = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        end = anchor + relativedelta(years=1)
        drifted = datetime(2026, 3, 28, 2, 0, tzinfo=UTC)

        served, _, _ = simulate(anchor, daily_runs(anchor), first_due=drifted)

        self.assertEqual(served[0], drifted)
        self.assertEqual(served[1], datetime(2026, 4, 30, 1, 0, tzinfo=UTC))
        self.assertEqual(served[1:], anchor_points(anchor, end)[2:])

    def test_a_due_time_far_off_its_anchor_still_gives_one_per_period(self):
        """Not written today (the docstring's assumption); if it were, the
        next due is always more than ANCHOR_SNAP and at most a month plus
        ANCHOR_SNAP after it, on an anchor point."""
        anchor = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        end = anchor + relativedelta(years=1)
        points = set(anchor_points(anchor, end))
        due = anchor + relativedelta(months=3)
        for offset_days in range(-20, 26):
            served = due + timedelta(days=offset_days)
            with self.subTest(offset_days=offset_days):
                _, nxt = next_monthly_grant(anchor, served, end)
                self.assertIn(nxt, points)
                self.assertGreater(nxt - served, ANCHOR_SNAP)
                self.assertLessEqual(nxt - served, timedelta(days=31) + ANCHOR_SNAP)

    def test_the_last_due_is_capped_at_the_contract_end(self):
        anchor = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        end = anchor + relativedelta(years=1)
        k, due = next_monthly_grant(anchor, anchor + relativedelta(months=11), end)
        self.assertEqual((k, due), (12, end))
        short_end = anchor + relativedelta(months=11, days=10)
        _, due = next_monthly_grant(
            anchor, anchor + relativedelta(months=11), short_end
        )
        self.assertEqual(due, short_end)
