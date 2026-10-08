"""
H-88: refresh_timing.allocation_anchor, the piece the licence refresh adds
to H-82's next_monthly_grant; and latest_monthly_point, which the licence
consumption window reopens on.

Property-style, as test_next_monthly_grant is. The chain is simulated the
way _refresh_teacher_credits drives it: a run at time t refreshes when the
row's due time is at or before t, the anchor is resolved (and kept) from
the row's stored anchor, its fallback and the due time being served, and
the next due time comes from next_monthly_grant.
"""

import ast
import os
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from types import SimpleNamespace
from typing import Any

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.test import SimpleTestCase

from billing.refresh_timing import (
    ANCHOR_SNAP,
    allocation_anchor,
    grants_owed,
    latest_monthly_point,
    next_monthly_grant,
)
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
    def test_a_stored_anchor_wins_while_the_due_time_is_on_its_chain(self):
        stored = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        fallback = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
        end = stored + relativedelta(years=1)
        for point in [stored] + anchor_points(stored, end):
            for drift_hours in (-72, 0, 26):
                due = point + timedelta(hours=drift_hours)
                with self.subTest(due=due):
                    self.assertEqual(allocation_anchor(stored, fallback, due), stored)

    def test_a_due_time_moved_off_the_stored_anchor_is_its_own_anchor(self):
        """1a's F1: anchor 5 January, due time moved to 28 March. Not the
        stored anchor (5 April next), and not the fallback either."""
        stored = datetime(2026, 1, 5, 1, 0, tzinfo=UTC)
        moved = datetime(2026, 3, 28, 3, 0, tzinfo=UTC)
        for fallback in (stored, datetime(2026, 2, 28, 1, 0, tzinfo=UTC)):
            with self.subTest(fallback=fallback):
                self.assertEqual(allocation_anchor(stored, fallback, moved), moved)
        _, next_due = next_monthly_grant(
            allocation_anchor(stored, stored, moved),
            moved,
            stored + relativedelta(years=1),
        )
        self.assertEqual(next_due, datetime(2026, 4, 28, 3, 0, tzinfo=UTC))

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


class LatestMonthlyPointTests(SimpleTestCase):
    """H-93: the licence's monthly points, which reopen its window."""

    def test_at_a_point_it_is_that_point_and_just_before_it_the_one_before(self):
        second = timedelta(seconds=1)
        for day in range(1, 32):
            anchor = datetime(2026, 1, day, 1, 0, tzinfo=UTC)
            points = [anchor] + anchor_points(anchor, anchor + relativedelta(years=1))
            for before, point in zip(points, points[1:], strict=False):
                with self.subTest(point=point):
                    self.assertEqual(latest_monthly_point(anchor, point), point)
                    self.assertEqual(
                        latest_monthly_point(anchor, point - second), before
                    )
                    self.assertEqual(
                        latest_monthly_point(anchor, point + timedelta(days=20)),
                        point,
                    )

    def test_a_clamped_date_is_a_point(self):
        """31 January, then 28 February: under a calendar month apart."""
        anchor = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        self.assertEqual(
            latest_monthly_point(anchor, datetime(2026, 2, 28, 3, 0, tzinfo=UTC)),
            datetime(2026, 2, 28, 1, 0, tzinfo=UTC),
        )
        self.assertEqual(
            latest_monthly_point(anchor, datetime(2026, 3, 31, 3, 0, tzinfo=UTC)),
            datetime(2026, 3, 31, 1, 0, tzinfo=UTC),
        )

    def test_before_the_first_month_ends_it_is_the_anchor(self):
        anchor = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        for at in (anchor, anchor + timedelta(days=27), anchor - timedelta(days=3)):
            with self.subTest(at=at):
                self.assertEqual(latest_monthly_point(anchor, at), anchor)


class GrantsOwedTests(SimpleTestCase):
    """H-81: the count a renewal reports."""

    def test_the_count_is_the_anchor_points_never_served(self):
        for day in range(1, 32):
            anchor = datetime(2026, 1, day, 1, 0, tzinfo=UTC)
            end = anchor + relativedelta(years=1)
            points = anchor_points(anchor, end)
            for served in range(len(points) + 1):
                next_due = points[served] if served < len(points) else end
                with self.subTest(anchor=anchor, served=served):
                    self.assertEqual(
                        grants_owed(anchor, next_due, end), len(points) - served
                    )

    def test_a_drifted_last_due_time_is_the_renewals_own_period(self):
        anchor = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        end = anchor + relativedelta(years=1)
        for days in range(0, 8):
            with self.subTest(days=days):
                self.assertEqual(
                    grants_owed(anchor, end - timedelta(days=days), end), 0
                )
        self.assertEqual(
            grants_owed(anchor, end - timedelta(days=7, seconds=1), end), 1
        )

    def test_a_due_time_after_until_owes_nothing(self):
        """An early renewal: `until` is the renewal's moment."""
        anchor = datetime(2026, 1, 31, 1, 0, tzinfo=UTC)
        until = datetime(2026, 5, 10, 1, 0, tzinfo=UTC)
        self.assertEqual(
            grants_owed(anchor, anchor + relativedelta(months=4), until), 0
        )


#: H-98: "this due time is on the allocation's STORED anchor". Passed this
#: way so the module still type-checks on the commit before the argument
#: exists.
STORED: dict[str, Any] = {"on_stored_anchor": True}


class GrantsOwedInTheLastWeekTests(SimpleTestCase):
    """H-98. H-81's count ignores a due time within ANCHOR_SNAP (7 days) of
    the cycle's end, so that a row drifted by the old chain is not reported.
    A real point of a STORED anchor in that week, left unserved by an outage
    that ran to the end, was not reported either. It is now, when all three
    hold: the anchor is the stored one, the due time is exactly one of its
    points, and it lies at least one full day before the end (the refresh
    runs once a day: a point due later than that may have had no run with
    Beat healthy)."""

    ANCHOR = datetime(2026, 1, 25, 1, 0, tzinfo=UTC)
    POINT = datetime(2026, 12, 25, 1, 0, tzinfo=UTC)  # ANCHOR + 11 months
    END = datetime(2026, 12, 28, 1, 0, tzinfo=UTC)  # three days after it

    def test_an_unserved_point_of_a_stored_anchor_is_owed(self):
        self.assertEqual(self.POINT, self.ANCHOR + relativedelta(months=11))
        self.assertEqual(grants_owed(self.ANCHOR, self.POINT, self.END, **STORED), 1)

    def test_without_a_stored_anchor_it_is_ignored_as_before(self):
        self.assertEqual(grants_owed(self.ANCHOR, self.POINT, self.END), 0)
        self.assertEqual(
            grants_owed(
                self.ANCHOR, self.POINT, self.END, **{"on_stored_anchor": False}
            ),
            0,
        )

    def test_a_due_time_near_a_point_but_not_on_it_is_ignored(self):
        """The drifted rows the 7 days exist for: near a point, not on it."""
        for off in (
            timedelta(seconds=1),
            timedelta(hours=2),
            timedelta(days=1),
            timedelta(days=3),
        ):
            for due in (self.POINT - off, self.POINT + off):
                if due + timedelta(days=1) > self.END:
                    continue
                with self.subTest(due=due):
                    self.assertEqual(
                        grants_owed(self.ANCHOR, due, self.END, **STORED), 0
                    )

    def test_it_needs_one_full_day_before_the_end(self):
        day = timedelta(days=1)
        for until, owed in (
            (self.POINT + day, 1),  # exactly one day: a daily run fell between
            (self.POINT + day - timedelta(seconds=1), 0),
            (self.POINT + timedelta(hours=12), 0),
            (self.POINT + timedelta(seconds=1), 0),
        ):
            with self.subTest(until=until):
                self.assertEqual(
                    grants_owed(self.ANCHOR, self.POINT, until, **STORED), owed
                )

    def test_a_chain_served_to_the_end_owes_nothing(self):
        """The due time is then capped at the cycle's end, or beyond it."""
        self.assertEqual(grants_owed(self.ANCHOR, self.END, self.END, **STORED), 0)
        self.assertEqual(
            grants_owed(
                self.ANCHOR, self.ANCHOR + relativedelta(months=12), self.END, **STORED
            ),
            0,
        )

    def test_an_outage_that_ran_to_the_end_counts_the_last_point_too(self):
        """Unserved from month 9: months 9 and 10 are more than 7 days
        before the end (H-81 counted them); month 11 is the new one."""
        due = self.ANCHOR + relativedelta(months=9)
        self.assertEqual(grants_owed(self.ANCHOR, due, self.END), 2)
        self.assertEqual(grants_owed(self.ANCHOR, due, self.END, **STORED), 3)

    def test_every_day_of_the_month_clamped_dates_too(self):
        for day in range(1, 32):
            anchor = datetime(2026, 1, day, 1, 0, tzinfo=UTC)
            point = anchor + relativedelta(months=1)  # 31 Jan -> 28 Feb
            for days_before_end in (1, 3, 7):
                until = point + timedelta(days=days_before_end)
                with self.subTest(anchor=anchor, days_before_end=days_before_end):
                    self.assertEqual(grants_owed(anchor, point, until, **STORED), 1)
                    self.assertEqual(grants_owed(anchor, point, until), 0)

    def test_outside_the_last_week_nothing_changes(self):
        """H-81's own table, with and without a stored anchor."""
        for day in range(1, 32):
            anchor = datetime(2026, 1, day, 1, 0, tzinfo=UTC)
            end = anchor + relativedelta(years=1)
            points = anchor_points(anchor, end)
            for served in range(len(points) + 1):
                next_due = points[served] if served < len(points) else end
                with self.subTest(anchor=anchor, served=served):
                    self.assertEqual(
                        grants_owed(anchor, next_due, end, **STORED),
                        grants_owed(anchor, next_due, end),
                    )


class TheRenewalReportsTests(SimpleTestCase):
    """H-98 at its caller: LicenseSubscriptionService._report_owed_refreshes
    says "stored anchor" only for an allocation whose stored anchor is the
    one in use."""

    START = datetime(2025, 12, 28, 1, 0, tzinfo=UTC)
    END = datetime(2026, 12, 28, 1, 0, tzinfo=UTC)
    ANCHOR = datetime(2026, 1, 25, 1, 0, tzinfo=UTC)
    POINT = datetime(2026, 12, 25, 1, 0, tzinfo=UTC)

    def report(self, **allocation):
        from billing.license_service import LicenseSubscriptionService

        licence = SimpleNamespace(
            id=77, billing_cycle_start=self.START, billing_cycle_end=self.END
        )
        row = SimpleNamespace(
            id=501,
            user_id=4821,
            created_at=self.ANCHOR,
            **allocation,
        )
        with self.assertLogs("billing.license_service", level="ERROR") as logs:
            # assertLogs needs one record; this one is ours.
            import logging

            logging.getLogger("billing.license_service").error("sentinel")
            LicenseSubscriptionService._report_owed_refreshes(
                licence, [row], self.END + timedelta(minutes=3)
            )
        return [r for r in logs.records if r.getMessage() != "sentinel"]

    def test_a_point_of_the_stored_anchor_in_the_last_week_is_reported(self):
        records = self.report(
            grant_anchor_at=self.ANCHOR, next_credit_grant_at=self.POINT
        )

        self.assertEqual(len(records), 1)
        message = records[0].getMessage()
        self.assertIn("allocation 501 (user 4821) is owed 1 monthly refresh", message)
        self.assertIn("License 77", message)
        self.assertNotIn("@", message)

    def test_a_row_with_no_stored_anchor_is_not_reported_there(self):
        """A row older than the field: its fallback anchor (created_at
        here) puts the due time on a point too, but nothing was stored."""
        records = self.report(grant_anchor_at=None, next_credit_grant_at=self.POINT)

        self.assertEqual(records, [])

    def test_a_stored_anchor_that_is_not_the_one_in_use_does_not_count(self):
        """The due time is far off the stored anchor's chain (something
        moved it), so the due time itself becomes the anchor in use: it is
        then on its own chain, but not on the stored one."""
        moved = self.END - timedelta(days=3)  # 25 Dec... of another rhythm
        stale = datetime(2026, 1, 10, 1, 0, tzinfo=UTC)
        records = self.report(grant_anchor_at=stale, next_credit_grant_at=moved)

        self.assertEqual(records, [])

    def test_the_individual_plan_caller_has_no_stored_anchor_and_says_so(self):
        """billing/services.py passes the cycle start as the anchor; there
        is no stored anchor for an individual plan, so it must not claim
        one."""
        with open(os.path.join(settings.BASE_DIR, "billing", "services.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "grants_owed"
        ]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args), 3)
        self.assertEqual(calls[0].keywords, [])
