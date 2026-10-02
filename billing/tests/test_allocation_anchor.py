"""
H-88: refresh_timing.allocation_anchor, the piece the licence refresh adds
to H-82's next_monthly_grant.

Property-style, as test_next_monthly_grant is. The chain is simulated the
way _refresh_teacher_credits drives it: a run at time t refreshes when the
row's due time is at or before t, the anchor is resolved (and kept) from
the row's stored anchor, its fallback and the due time being served, and
the next due time comes from next_monthly_grant.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from dateutil.relativedelta import relativedelta
from django.test import SimpleTestCase

from billing.refresh_timing import ANCHOR_SNAP, allocation_anchor, next_monthly_grant
from billing.tests.test_next_monthly_grant import anchor_points

UTC = dt_timezone.utc


def simulate(fallback, first_due, end, stored=None, runs=None):
    """(served due times, the anchor the row ends with) for daily 03:00
    runs from `first_due` to `end`."""
    due, served = first_due, []
    run = first_due.replace(hour=3, minute=0, second=0, microsecond=0)
    while run < end:
        if runs is None or run in runs:
            if due < end and due <= run + timedelta(minutes=5):
                served.append(due)
                stored = allocation_anchor(stored, fallback, due)
                _, due = next_monthly_grant(stored, due, end)
        run += timedelta(days=1)
    return served, stored


class AllocationAnchorTests(SimpleTestCase):
    def test_a_stored_anchor_always_wins(self):
        stored = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        fallback = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
        for due in (stored + relativedelta(months=2), fallback, stored):
            self.assertEqual(allocation_anchor(stored, fallback, due), stored)

    def test_a_due_time_on_the_fallbacks_chain_uses_the_fallback(self):
        """Every start day, every month, drifted by the old chain: up to 3
        days early (the 31st clamped to the 28th) or a day late (a late
        run)."""
        for day in range(1, 32):
            fallback = datetime(2026, 1, day, 1, 0, tzinfo=UTC)
            end = fallback + relativedelta(years=1)
            for point in anchor_points(fallback, end):
                for drift_hours in (-72, -48, -24, 0, 2, 26):
                    due = point + timedelta(hours=drift_hours)
                    with self.subTest(fallback=fallback, due=due):
                        self.assertEqual(
                            allocation_anchor(None, fallback, due), fallback
                        )

    def test_a_due_time_off_the_fallbacks_chain_is_its_own_anchor(self):
        """A teacher re-enrolled since the last renewal: the fallback is the
        renewal's day, their due times are on another."""
        fallback = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
        for offset_days in range(8, 21):
            due = fallback + relativedelta(months=2) + timedelta(days=offset_days)
            with self.subTest(offset_days=offset_days):
                self.assertEqual(allocation_anchor(None, fallback, due), due)

    def test_the_snap_window_is_ANCHOR_SNAP_on_both_sides(self):
        fallback = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
        point = fallback + relativedelta(months=3)
        second = timedelta(seconds=1)
        for due, expected in (
            (point - ANCHOR_SNAP, fallback),
            (point + ANCHOR_SNAP, fallback),
            (point - ANCHOR_SNAP - second, point - ANCHOR_SNAP - second),
            (point + ANCHOR_SNAP + second, point + ANCHOR_SNAP + second),
        ):
            with self.subTest(due=due):
                self.assertEqual(allocation_anchor(None, fallback, due), expected)

    def test_a_fallback_later_than_the_due_time_does_not_loop(self):
        fallback = datetime(2026, 6, 1, 1, 0, tzinfo=UTC)
        due = datetime(2026, 3, 1, 1, 0, tzinfo=UTC)
        self.assertEqual(allocation_anchor(None, fallback, due), due)


class OldRowChainTests(SimpleTestCase):
    """Rows older than the field: no stored anchor."""

    def test_a_drifted_row_converges_without_a_double_refresh(self):
        """The example: a 31 January licence, the row drifted to 28 March.
        That refresh is March's; the next is 30 April, not 31 March."""
        fallback = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        end = fallback + relativedelta(years=1)
        drifted = datetime(2026, 3, 28, 3, 0, tzinfo=UTC)

        served, anchor = simulate(fallback, drifted, end)

        self.assertEqual(anchor, fallback)
        self.assertEqual(served[0], drifted)
        self.assertEqual(served[1:], anchor_points(fallback, end)[2:])

    def test_every_start_day_drifted_at_any_month_gets_one_refresh_a_period(self):
        for day in range(1, 32):
            fallback = datetime(2026, 1, day, 1, 0, tzinfo=UTC)
            end = fallback + relativedelta(years=1)
            points = anchor_points(fallback, end)
            for i, point in enumerate(points[:-1]):
                for drift_days in (0, 1, 2, 3):
                    drifted = point - timedelta(days=drift_days, hours=-1)
                    with self.subTest(fallback=fallback, drifted=drifted):
                        served, _ = simulate(fallback, drifted, end)
                        self.assertEqual(served, [drifted] + points[i + 1 :])

    def test_a_re_enrolled_row_keeps_its_rhythm_with_no_irregular_interval(self):
        """The residual group. Every day of the month it could be on, against
        a fallback on the 10th: once anchored to its own due time, every
        interval is a calendar month from that time (28 to 31 days), and
        the anchor is kept, so a 31st does not clamp to the 28th for good."""
        fallback = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
        end = fallback + relativedelta(years=1)
        for day in list(range(18, 32)) + [1, 2]:
            month = 3 if day >= 18 else 4
            first_due = datetime(2026, month, day, 3, 0, tzinfo=UTC)
            with self.subTest(first_due=first_due):
                served, anchor = simulate(fallback, first_due, end)
                self.assertEqual(anchor, first_due)
                expected = [first_due] + [
                    p
                    for p in anchor_points(first_due, end + relativedelta(years=1))
                    if p < end
                ]
                self.assertEqual(served, expected)
                gaps = [(b - a).days for a, b in zip(served, served[1:], strict=False)]
                self.assertTrue(all(28 <= gap <= 31 for gap in gaps), gaps)

    def test_weekly_runs_only_delay(self):
        fallback = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        end = fallback + relativedelta(years=1)
        first = fallback + relativedelta(months=1)
        day = first.replace(hour=3, minute=0)
        mondays = set()
        while day < end:
            if day.weekday() == 0:
                mondays.add(day)
            day += timedelta(days=1)

        served, _ = simulate(fallback, first, end, stored=fallback, runs=mondays)

        self.assertEqual(served, anchor_points(fallback, end)[: len(served)])
        self.assertGreaterEqual(len(served), 10)
