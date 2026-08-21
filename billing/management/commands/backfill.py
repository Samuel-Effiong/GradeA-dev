"""
=====================================================================================
ONE-TIME BACKFILL — run this once, after deploying files 14-17, BEFORE
relying on the fix for any subscription that already had a scheduled
change before this deploy.
=====================================================================================

WHY THIS IS NEEDED
--------------------
Any `UserSubscription` row that ALREADY had `pending_plan` set before this
fix shipped has NO `stripe_schedule_id` — it was scheduled under the old,
buggy reactive-sync design. Without this backfill, those specific
subscriptions would still hit the original bug at their next renewal (their
`pending_change_type` is now visible to the frontend thanks to files 11/17,
but Stripe still doesn't know anything is scheduled for THEM specifically,
since `stripe_schedule_id` is null on those rows).

New scheduling requests made AFTER this deploy are unaffected — they always
go through `schedule_plan_change_on_stripe` and get a real Stripe schedule
from the start. This backfill only matters for whatever was already
in-flight at deploy time.

Run this via `python manage.py shell < this_file.py`, or paste it into a
Django shell session, or wrap it in a proper management command if you'd
rather — it's written as a plain script since it's a one-time operation,
not a piece of the app.

SAFETY
-------
- Read-only against Stripe until it actually calls
  `schedule_plan_change_on_stripe`, which is the same method the live
  feature uses — no new code path, no new risk beyond what's already
  reviewed in file 16.
- Idempotent: `schedule_plan_change_on_stripe` already handles "no existing
  schedule -> create one" cleanly, and this script only targets rows where
  `stripe_schedule_id` is genuinely empty, so re-running it after a partial
  failure is safe — already-backfilled rows are skipped.
- Each row is processed independently with its own try/except so one
  failure doesn't stop the rest from being protected.
=====================================================================================
"""

import logging

from django.utils import timezone

from billing.models import StripeSubscriptionStatus, UserSubscription

# from billing.services import SubscriptionService
from billing.stripe_service import StripeSubscriptionScheduleService

logger = logging.getLogger(__name__)

now = timezone.now()

# Any active, non-trial, Stripe-billed subscription with a pending change
# already scheduled, but no Stripe schedule protecting it yet, and whose
# cycle hasn't already ended (if it already ended, the renewal either
# already processed via the old buggy path — nothing left to backfill for
# that row — or is about to be picked up by the normal renewal pipeline
# regardless).
candidates = (
    UserSubscription.objects.filter(
        is_active=True,
        is_trial=False,
        pending_plan__isnull=False,
        billing_cycle_end__gt=now,
    )
    .exclude(stripe_subscription_id__isnull=True)
    .exclude(stripe_subscription_id="")
    .filter(stripe_schedule_id__in=[None, ""])
    .select_related("plan", "pending_plan")
)

total = candidates.count()
print(f"Found {total} subscription(s) with a pending change but no Stripe schedule.")

succeeded = 0
failed = 0

for user_sub in candidates:
    try:
        if user_sub.stripe_status == StripeSubscriptionStatus.PAST_DUE:
            print(
                f"SKIPPING {user_sub.id} (user {user_sub.user.email}): "
                f"PAST_DUE — resolve payment before backfilling this one."
            )
            continue

        schedule_id = StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
            user_sub, user_sub.pending_plan
        )

        # Persist the schedule id without re-composing pending_change_type/
        # note (they're already correct from before this fix — only the
        # Stripe-side enforcement was missing). schedule_plan_change()
        # requires a full re-supply of type/note by design (see file 15),
        # so pass through the EXISTING values rather than SubscriptionService
        # .schedule_plan_change to avoid overwriting an already-correct,
        # already-shown-to-the-user message with a freshly regenerated one.
        user_sub.stripe_schedule_id = schedule_id
        user_sub.save(update_fields=["stripe_schedule_id", "updated_at"])

        succeeded += 1
        print(
            f"OK: {user_sub.id} (user {user_sub.user.email}) -> "
            f"schedule {schedule_id}"
        )
    except Exception as exc:  # noqa: BLE001 — deliberately broad: one bad
        # row must never stop the rest of the backfill from running.
        failed += 1
        logger.error(
            "Backfill failed for subscription %s (user %s): %s",
            user_sub.id,
            user_sub.user.email,
            exc,
            exc_info=True,
        )
        print(f"FAILED: {user_sub.id} (user {user_sub.user.email}): {exc}")

print(f"\nDone. {succeeded} backfilled, {failed} failed, out of {total} total.")
if failed:
    print(
        "Investigate the failures above manually — each one is still "
        "running on the OLD (unprotected) reactive-sync path until fixed."
    )
