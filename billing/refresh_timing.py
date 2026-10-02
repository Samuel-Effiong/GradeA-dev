"""
When a monthly credit refresh is due, and how long a monthly bucket lives.

THE RACE THIS CLOSES
--------------------
Two Beat tasks refresh credits monthly: process_annual_plan_credit_grants
(02:00, annual individual plans) and process_license_monthly_credit_refreshes
(03:00, licence teachers). Each used to set the next due time from the
moment the row was PROCESSED (`now + 1 month`), with the monthly bucket
expiring at that same moment. A month later the task filtered on its own
START time, a little earlier, so the row wasn't due yet and waited a day.
Meanwhile cleanup_expired_credit_buckets (05:00) wrote the expired bucket
off, so the next day's refresh found nothing to roll over: every month the
customer lost that month's carry-over and had about a day with no monthly
credits (billing/tests/test_monthly_rollover_cleanup_race.py).

THREE DEFENCES
--------------
1. One "now" per run. Each task passes its start time to the service, so
   the due check and the next due time come from the same moment, and a
   refresh counts as due on a run if it falls due within
   REFRESH_DUE_TOLERANCE of that run's start (Beat dispatch and worker
   pickup jitter by seconds from one day to the next).
2. A monthly bucket outlives its refresh's due time by MONTHLY_BUCKET_GRACE,
   so the customer keeps monthly credits until the daily refresh retires
   it (rolling its unused balance over). Never past the contract's end:
   the renewal owns that boundary.
3. cleanup_expired_credit_buckets never writes off a wallet's newest
   unprocessed MONTHLY bucket while its owner is still entitled (an active
   subscription, or an active licence allocation): for them an expired
   monthly bucket means a refresh or renewal is still owed, and that is
   what must roll it over.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta  # type: ignore

#: A refresh due within this long after a run starts is due on that run.
REFRESH_DUE_TOLERANCE = timedelta(minutes=5)

#: How long a monthly bucket outlives its refresh's due time. Two days, so
#: one skipped or late daily run still finds it live.
MONTHLY_BUCKET_GRACE = timedelta(days=2)

#: A MONTHLY bucket kept for an owed refresh this long past its expiry is
#: logged at ERROR by the cleanup: the refresh itself has stopped.
OWED_REFRESH_OVERDUE = timedelta(days=7)


def refresh_due_by(now):
    """Everything due up to this moment is due on a run started at `now`."""
    return now + REFRESH_DUE_TOLERANCE


def monthly_bucket_expiry(next_due, contract_end):
    """When a monthly bucket granted for the period ending `next_due` should
    expire: MONTHLY_BUCKET_GRACE after it, but not past `contract_end`
    (and never before `next_due` itself)."""
    return max(next_due, min(next_due + MONTHLY_BUCKET_GRACE, contract_end))


#: The next monthly grant is the first anchor point more than this far past
#: the due time just served (H-82). See next_monthly_grant.
ANCHOR_SNAP = timedelta(days=7)


def next_monthly_grant(anchor, served_due, contract_end):
    """The monthly grant after the one due at `served_due`, on a contract
    anchored at `anchor`: (k, due), where due is `anchor + k months`, capped
    at `contract_end`.

    Each point is computed from the anchor, never from the previous due
    time, so a 31st anchor gives 28 Feb, 31 Mar, 30 Apr... (chaining
    `+ 1 month` clamps to the 28th once and stays there: 13 grants a year).

    k is the first whose point lies more than ANCHOR_SNAP past
    `served_due`, NOT past "now":
      * A row already drifted by the old chain (31 Jan anchor, due 28 Mar)
        is serving March's grant, so its next is 30 April; "the first point
        after now" would be 31 March, a second grant three days later.
      * A late run (an outage) leaves `served_due` where it was, so the
        chain catches up one grant per run instead of skipping months.
    This assumes `served_due` is within ANCHOR_SNAP before its own anchor
    point: the old chain's drift is at most 3 days (the 31st clamped to the
    28th), and a month is at least 28 days, so the point after the current
    one is always more than ANCHOR_SNAP away. A due time further off its
    anchor (none is written today) still yields one grant per period: the
    next due is always more than ANCHOR_SNAP, and at most a month plus
    ANCHOR_SNAP, after it (tested).
    """
    floor = served_due + ANCHOR_SNAP
    k = 1
    while anchor + relativedelta(months=k) <= floor:
        k += 1
    return k, min(anchor + relativedelta(months=k), contract_end)
