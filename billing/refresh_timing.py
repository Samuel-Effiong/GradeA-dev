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
